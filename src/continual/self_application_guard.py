from __future__ import annotations

from copy import deepcopy
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Mapping

from .store import Store


_CONTEXT_BOOLEAN_FIELDS = (
    "logical_reset_used",
    "fresh_execution_used",
    "independent_execution_used",
)


def _required_text(record: Mapping[str, Any], key: str, error_type: type[ValueError]) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise error_type(f"{key} must be a non-empty string")
    return value.strip()


def _assert_context_booleans(
    record: Mapping[str, Any],
    error_type: type[ValueError],
) -> None:
    context = record.get("context", {})
    if not isinstance(context, Mapping):
        raise error_type("context must be an object")
    for key in _CONTEXT_BOOLEAN_FIELDS:
        if key in context and not isinstance(context[key], bool):
            raise error_type(f"context.{key} must be a boolean")


def _assert_unclaimed_native_paths(
    root: Path,
    record: Mapping[str, Any],
    *,
    conflict_type: type[ValueError],
    error_type: type[ValueError],
) -> None:
    """Prevent a self-application record from overwriting unrelated native O state."""

    store = Store(root)
    execution_id = _required_text(record, "execution_id", error_type)
    run_id = record.get("run_id")
    if run_id is None:
        run_id = f"run-self-{store.stable_digest(execution_id, length=20)}"
    if not isinstance(run_id, str) or not run_id:
        raise error_type("run_id must be a non-empty string")

    episode_id = record.get("episode_id")
    if episode_id is None:
        episode_id = f"episode-{run_id.removeprefix('run-')}"
    if not isinstance(episode_id, str) or not episode_id:
        raise error_type("episode_id must be a non-empty string")

    evidence_id = record.get("evidence_id")
    if evidence_id is None:
        evidence_id = f"self-application-{store.stable_digest(execution_id, length=20)}"
    if not isinstance(evidence_id, str) or not evidence_id:
        raise error_type("evidence_id must be a non-empty string")

    run_dir = store.run_dir(run_id)
    invocation_path = run_dir / "invocations" / "self-application.json"
    if run_dir.exists() and not invocation_path.is_file():
        try:
            occupied = any(run_dir.iterdir())
        except OSError as exc:
            raise conflict_type(f"cannot inspect existing run_id {run_id}: {exc}") from exc
        if occupied:
            raise conflict_type(f"run_id already belongs to another native run: {run_id}")

    existing_episode_path = (
        root / ".continual" / "episodes" / episode_id / "episode.json"
    )
    if existing_episode_path.exists():
        existing_episode = store.read_json(existing_episode_path, {})
        if not isinstance(existing_episode, dict) or existing_episode.get("execution_id") != execution_id:
            raise conflict_type(
                f"episode_id already belongs to another execution: {episode_id}"
            )

    existing_evidence_path = (
        root
        / ".continual"
        / "evidence"
        / "self-application"
        / f"{evidence_id}.json"
    )
    if existing_evidence_path.exists():
        existing_evidence = store.read_json(existing_evidence_path, {})
        if not isinstance(existing_evidence, dict) or existing_evidence.get("execution_id") != execution_id:
            raise conflict_type(
                f"evidence_id already belongs to another execution: {evidence_id}"
            )


def guard_record_self_application(
    original: Callable[[Path, Mapping[str, Any]], dict[str, Any]],
    *,
    error_type: type[ValueError],
    conflict_type: type[ValueError],
) -> Callable[[Path, Mapping[str, Any]], dict[str, Any]]:
    """Add strict input and native-state collision checks before any durable mutation."""

    if getattr(original, "_o_self_application_guarded", False):
        return original

    @wraps(original)
    def guarded(root: Path, raw_record: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(raw_record, Mapping):
            raise error_type("record must be an object")
        record = deepcopy(dict(raw_record))
        _assert_context_booleans(record, error_type)
        resolved_root = Path(root).resolve()
        _assert_unclaimed_native_paths(
            resolved_root,
            record,
            conflict_type=conflict_type,
            error_type=error_type,
        )
        return original(resolved_root, record)

    guarded._o_self_application_guarded = True  # type: ignore[attr-defined]
    guarded._o_self_application_unguarded = original  # type: ignore[attr-defined]
    return guarded


def install_self_application_guard() -> None:
    """Install the fail-closed public self-application boundary once per interpreter."""

    from . import self_application

    self_application.record_self_application = guard_record_self_application(
        self_application.record_self_application,
        error_type=self_application.SelfApplicationError,
        conflict_type=self_application.SelfApplicationConflict,
    )
