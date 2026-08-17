from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

from agi.acquired_programs import execute_program
from continual.contracted_external_tools import ContractedExternalToolRegistry
from continual.learned_tools import LearnedToolError
from continual.learning_engine import LearningEnabledEngine
from continual.store import Store


class _ForbiddenModelClient:
    """Fail immediately if a supposedly mechanical replay tries to invoke a live model."""

    model = "disabled-for-mechanical-replay"

    def call(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(
            "materialized runtime replay unexpectedly requested live model execution"
        )


def _mechanical_engine(root: Path) -> LearningEnabledEngine:
    """Construct only the stateful mechanical half of LearningEnabledEngine.

    The retained-capability replay deliberately exercises the verified learned-tool dispatch path,
    which is required to be deterministic and credential-free after Candidate materialization. Using
    the normal constructor would eagerly create ModelClient even though this probe must never call a
    model. Bypassing that eager dependency makes the test stricter: any accidental semantic fallback
    hits _ForbiddenModelClient and fails immediately instead of depending on CI credentials.
    """

    resolved = root.resolve()
    engine = object.__new__(LearningEnabledEngine)
    engine.root = resolved
    engine.store = Store(resolved)
    engine.model = _ForbiddenModelClient()
    engine._external_tool_registry = ContractedExternalToolRegistry(resolved, {})
    return engine


def _digest_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate_trial_snapshot(root: Path, candidate_id: str) -> dict[str, str]:
    trial_dir = root / ".continual" / "candidates" / candidate_id / "trials"
    return {path.name: _digest_bytes(path) for path in sorted(trial_dir.glob("*.json"))}


def _new_runtime_run(engine: LearningEnabledEngine) -> str:
    run_id = engine.store.new_id("run")
    run_dir = engine.store.run_dir(run_id)
    run_dir.mkdir(parents=True)
    engine.store.atomic_json(
        run_dir / "snapshot.json",
        {"revision": 0, "status": "continue", "phase": "unit_pending"},
    )
    return run_id


def _invoke(
    engine: LearningEnabledEngine,
    run_id: str,
    *,
    scope: str,
    tool_id: str,
    value: Any,
) -> dict[str, Any]:
    return engine._invoke(
        run_id,
        "execute",
        {
            "snapshot": {"revision": 0},
            "execution_unit": {
                "goal": "replay a persisted regression-verified capability after repository materialization",
                "scope": scope,
                "learned_tool_call": {"tool_id": tool_id, "input": value},
            },
        },
    )


def _probe_for_domain(domain: str) -> Any:
    if domain == "string":
        return "materialized-retention-probe"
    if domain == "sequence":
        return [13, 21, 34, 55]
    if domain == "numeric":
        return 17
    if domain == "object":
        return {"alpha": 3, "beta": "retention", "gamma": [2, 5, 8]}
    raise RuntimeError(f"unsupported materialized capability input domain: {domain}")


def _select_acquired_candidate(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for path in sorted((root / ".continual" / "candidates").glob("*/candidate.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            continue
        if raw.get("target_component") != "acquired_program":
            continue
        if raw.get("status") != "active-for-scope":
            continue
        acquired = raw.get("acquired_program")
        verified = raw.get("verified_scope_states")
        if not isinstance(acquired, Mapping) or not isinstance(verified, Mapping):
            continue
        scope = acquired.get("scope")
        if not isinstance(scope, str) or scope not in verified:
            continue
        candidates.append((dict(raw), dict(acquired)))
    if not candidates:
        raise RuntimeError("no active materialized acquired-program Candidate is available")
    return candidates[0]


def run_materialized_runtime_retention_replay(root: Path) -> dict[str, Any]:
    """Falsify whether a committed learned capability really survives fresh Engine instances.

    A materialization is useful only if runtime code can consume the committed Candidate state after
    checkout without spending another Candidate trial. This probe selects a persisted verified pure
    acquired program, establishes that the same tool is unavailable from a repository root with no
    Candidate state, then exercises it through the real LearningEnabledEngine mechanical dispatch
    twice in one run and once more through a newly constructed Engine. The deterministic Candidate
    trial files must remain byte-identical throughout, and no live-model credential may be required.
    """

    root = root.resolve()
    candidate, acquired = _select_acquired_candidate(root)
    candidate_id = str(candidate["candidate_id"])
    scope = str(acquired["scope"])
    tool_id = str(acquired["tool_id"])
    program = acquired.get("program")
    if not isinstance(program, Mapping):
        raise RuntimeError("materialized acquired program descriptor is missing")
    input_domain = str(program.get("input_domain", ""))
    probe = _probe_for_domain(input_domain)
    expected = execute_program(program, probe)

    with tempfile.TemporaryDirectory(prefix="agi-materialized-baseline-") as temporary:
        empty_root = Path(temporary)
        empty_engine = _mechanical_engine(empty_root)
        empty_run = _new_runtime_run(empty_engine)
        baseline_failed_closed = False
        try:
            _invoke(
                empty_engine,
                empty_run,
                scope=scope,
                tool_id=tool_id,
                value=probe,
            )
        except LearnedToolError:
            baseline_failed_closed = True
        if not baseline_failed_closed:
            raise RuntimeError("materialized capability was unexpectedly callable without Candidate state")

    trial_snapshot_before = _candidate_trial_snapshot(root, candidate_id)

    first_engine = _mechanical_engine(root)
    first_run = _new_runtime_run(first_engine)
    first = _invoke(first_engine, first_run, scope=scope, tool_id=tool_id, value=probe)
    repeated = _invoke(first_engine, first_run, scope=scope, tool_id=tool_id, value=probe)

    second_engine = _mechanical_engine(root)
    second_run = _new_runtime_run(second_engine)
    restarted = _invoke(second_engine, second_run, scope=scope, tool_id=tool_id, value=probe)

    outputs = [first, repeated, restarted]
    for output in outputs:
        result = output.get("result")
        if not isinstance(result, Mapping):
            raise RuntimeError("materialized runtime replay returned malformed execution output")
        if result.get("output") != expected:
            raise RuntimeError("materialized runtime replay disagreed with persisted program semantics")
        if result.get("candidate_id") != candidate_id:
            raise RuntimeError("materialized runtime replay used the wrong Candidate")
        if result.get("program_kind") != "acquired_program":
            raise RuntimeError("materialized runtime replay did not use the acquired-program path")

    trial_snapshot_after = _candidate_trial_snapshot(root, candidate_id)
    if trial_snapshot_after != trial_snapshot_before:
        raise RuntimeError("materialized runtime replay consumed or rewrote Candidate trial state")

    first_result = first["result"]
    return {
        "schema_version": 1,
        "passed": True,
        "candidate_id": candidate_id,
        "scope": scope,
        "tool_id": tool_id,
        "input_domain": input_domain,
        "probe_sha256": hashlib.sha256(
            json.dumps(probe, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "output_sha256": hashlib.sha256(
            json.dumps(expected, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "baseline_without_candidate_failed_closed": True,
        "same_run_replay_reused": repeated == first,
        "fresh_engine_replay_matched": restarted["result"] == first_result,
        "candidate_trial_state_unchanged": True,
        "candidate_trial_file_count": len(trial_snapshot_before),
        "live_model_invocation_required": False,
        "execution_kind": first_result.get("execution_kind"),
        "program_kind": first_result.get("program_kind"),
        "claim_boundary": (
            "Internal retained-capability runtime evidence only. The capability, evaluator, and "
            "materialization pipeline remain repository-internal and do not prove AGI."
        ),
    }
