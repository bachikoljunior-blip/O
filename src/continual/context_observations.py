from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .store import Store
class ContextObservationError(ValueError):
    """Raised when an external observation is not durably O-requested and bound."""


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FORBIDDEN_KEYS = {
    "api_key",
    "authorization",
    "password",
    "private_key",
    "secret",
    "token",
}
_SECRET_TEXT = re.compile(
    r"(?i)(?:bearer\s+[a-z0-9._~+/-]{12,}|(?:api[_-]?key|secret|token)\s*[:=]\s*\S+)"
)


def _require_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ContextObservationError(f"invalid {label}")
    return value


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContextObservationError(f"{label} must be non-empty")
    return value


def _walk_public(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).strip().lower() in _FORBIDDEN_KEYS:
                raise ContextObservationError(f"forbidden private field at {path}.{key}")
            _walk_public(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_public(child, f"{path}[{index}]")
    elif isinstance(value, str) and _SECRET_TEXT.search(value):
        raise ContextObservationError(f"secret-like text is forbidden at {path}")


def _public_object(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContextObservationError(f"{label} must be an object")
    public = deepcopy(dict(value))
    _walk_public(public, label)
    return public


def _digest_matches(
    store: Store,
    value: Mapping[str, Any],
    *,
    digest_field: str,
    timestamp_field: str,
) -> bool:
    supplied = value.get(digest_field)
    body = deepcopy(dict(value))
    body.pop(digest_field, None)
    body.pop(timestamp_field, None)
    return isinstance(supplied, str) and supplied == store.stable_digest(body, length=64)


def _read_record(
    store: Store,
    path: Path,
    *,
    record_type: str,
    digest_field: str,
    timestamp_field: str,
) -> dict[str, Any]:
    if not path.is_file():
        raise ContextObservationError(
            f"malformed {record_type.replace('_', ' ')}"
        )
    try:
        value = store.read_json(path, None)
    except (OSError, ValueError) as exc:
        raise ContextObservationError(
            f"malformed {record_type.replace('_', ' ')}"
        ) from exc
    if (
        not isinstance(value, dict)
        or value.get("record_type") != record_type
        or not _digest_matches(
            store,
            value,
            digest_field=digest_field,
            timestamp_field=timestamp_field,
        )
    ):
        raise ContextObservationError(f"tampered {record_type.replace('_', ' ')}")
    return value


def _create_or_replay(
    store: Store,
    path: Path,
    record: dict[str, Any],
    *,
    record_type: str,
    digest_field: str,
    timestamp_field: str,
    conflict: str,
) -> dict[str, Any]:
    def existing() -> dict[str, Any]:
        current = _read_record(
            store,
            path,
            record_type=record_type,
            digest_field=digest_field,
            timestamp_field=timestamp_field,
        )
        if current.get(digest_field) != record.get(digest_field):
            raise ContextObservationError(conflict)
        return deepcopy(current)

    if path.exists():
        return existing()
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return existing()
    with os.fdopen(fd, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return deepcopy(record)


def _observation_dir(root: Path, run_id: str, observation_id: str) -> Path:
    return (
        root.resolve()
        / ".continual"
        / "runs"
        / run_id
        / "context-observations"
        / observation_id
    )


def _active_observation_execute_request(
    root: Path,
    *,
    run_id: str,
    invocation_id: str,
    executor_binding: str,
    model_identity: str,
) -> dict[str, Any]:
    # The observation adapter is read-only. Bind it to the exact current
    # Execute journal, while ignoring historical orphan journals that the
    # stricter external-effect authority path intentionally blocks on.
    from .work_session import _verified_request

    store = Store(root)
    request_path = (
        root
        / ".continual"
        / "work-model"
        / "invocations"
        / invocation_id
        / "request.json"
    )
    if not request_path.is_file():
        raise ContextObservationError("Work request does not exist")
    try:
        request = _verified_request(store, request_path)
    except (OSError, ValueError) as exc:
        raise ContextObservationError(str(exc)) from exc
    if (
        request.get("run_id") != run_id
        or request.get("component") != "execute"
        or request.get("executor_binding") != executor_binding
        or request.get("model_identity") != model_identity
        or (request_path.parent / "response.json").exists()
    ):
        raise ContextObservationError("observation Work request identity mismatch")
    matching: list[dict[str, Any]] = []
    for path in sorted(
        (root / ".continual" / "runs" / run_id / "invocations").glob("*.json")
    ):
        value = store.read_json(path, None)
        if not isinstance(value, dict):
            raise ContextObservationError("malformed native invocation journal")
        if (
            value.get("status") == "awaiting_work_model"
            and value.get("work_invocation_id") == invocation_id
        ):
            matching.append(value)
    request_ref = request_path.relative_to(root).as_posix()
    if (
        len(matching) != 1
        or matching[0].get("component") != "execute"
        or matching[0].get("work_request_ref") != request_ref
        or matching[0].get("work_request_digest") != request.get("request_digest")
    ):
        raise ContextObservationError(
            "observation requires one exact active native Execute journal"
        )
    return request


def _required_text_list(value: Any, label: str, *, maximum: int = 32) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > maximum
        or not all(isinstance(item, str) and item.strip() for item in value)
        or len(set(value)) != len(value)
    ):
        raise ContextObservationError(
            f"{label} must be 1..{maximum} unique non-empty strings"
        )
    return list(value)


def _validated_source(value: Mapping[str, Any]) -> dict[str, Any]:
    source = _public_object(value, "source")
    if source.get("kind") != "github_file":
        raise ContextObservationError("only github_file observations are supported")
    repository = _require_text(source.get("repository_full_name"), "repository_full_name")
    if repository.count("/") != 1:
        raise ContextObservationError("repository_full_name must be owner/name")
    path = _require_text(source.get("path"), "source.path")
    ref = _require_text(source.get("ref"), "source.ref")
    expected_commit = _require_text(
        source.get("expected_commit_sha"), "source.expected_commit_sha"
    )
    if ref != expected_commit:
        raise ContextObservationError(
            "github_file observation must bind ref to expected immutable commit"
        )
    if len(expected_commit) != 40 or any(
        char not in "0123456789abcdef" for char in expected_commit.lower()
    ):
        raise ContextObservationError("expected_commit_sha must be a full hex SHA")
    if path.startswith("/") or ".." in Path(path).parts:
        raise ContextObservationError("source.path must be repository relative")
    if set(source) != {
        "kind",
        "repository_full_name",
        "path",
        "ref",
        "expected_commit_sha",
    }:
        raise ContextObservationError("github_file source contains unsupported fields")
    return source


def _validated_freshness(value: Mapping[str, Any]) -> dict[str, Any]:
    freshness = _public_object(value, "freshness")
    kind = freshness.get("kind")
    if kind == "immutable_version":
        if set(freshness) != {"kind", "invalidates_on"}:
            raise ContextObservationError("immutable freshness contains unsupported fields")
    elif kind == "max_age":
        seconds = freshness.get("max_age_seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 1:
            raise ContextObservationError("max_age_seconds must be positive")
        if set(freshness) != {"kind", "max_age_seconds", "invalidates_on"}:
            raise ContextObservationError("max_age freshness contains unsupported fields")
    else:
        raise ContextObservationError("unsupported observation freshness kind")
    _required_text_list(freshness.get("invalidates_on"), "freshness.invalidates_on")
    return freshness


def _parse_time(value: Any, label: str) -> datetime:
    text = _require_text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContextObservationError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ContextObservationError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _read_observation_record(
    store: Store,
    path: Path,
    *,
    record_type: str,
    digest_field: str,
    timestamp_field: str,
) -> dict[str, Any]:
    return _read_record(
        store,
        path,
        record_type=record_type,
        digest_field=digest_field,
        timestamp_field=timestamp_field,
    )


def prepare_context_observation(
    root: Path,
    *,
    run_id: str,
    observation_id: str,
    invocation_id: str,
    executor_binding: str,
    model_identity: str,
    source: Mapping[str, Any],
    selected_fields: list[str],
    freshness: Mapping[str, Any],
    evidence_class: str = "operator_connector_readback",
) -> dict[str, Any]:
    """Persist one immutable read-only activity before the outer executor runs it."""

    root = root.resolve()
    run_id = _require_id(run_id, "run_id")
    observation_id = _require_id(observation_id, "observation_id")
    request = _active_observation_execute_request(
        root,
        run_id=run_id,
        invocation_id=invocation_id,
        executor_binding=executor_binding,
        model_identity=model_identity,
    )
    source_value = _validated_source(source)
    fields = _required_text_list(selected_fields, "selected_fields")
    freshness_value = _validated_freshness(freshness)
    evidence_class = _require_text(evidence_class, "evidence_class")
    body = {
        "schema_version": 1,
        "record_type": "context_observation_request",
        "run_id": run_id,
        "observation_id": observation_id,
        "invocation_id": invocation_id,
        "work_request_digest": request["request_digest"],
        "executor_binding": executor_binding,
        "model_identity": model_identity,
        "source": source_value,
        "selected_fields": fields,
        "freshness": freshness_value,
        "evidence_class": evidence_class,
        "operation": "read",
    }
    store = Store(root)
    body["request_digest"] = store.stable_digest(body, length=64)
    body["requested_at"] = store.utc_now()
    path = _observation_dir(root, run_id, observation_id) / "request.json"
    return _create_or_replay(
            store,
            path,
            body,
            record_type="context_observation_request",
            digest_field="request_digest",
            timestamp_field="requested_at",
            conflict="immutable context observation request conflict",
        )


def record_context_observation_receipt(
    root: Path,
    *,
    run_id: str,
    observation_id: str,
    request_digest: str,
    executor_binding: str,
    model_identity: str,
    source_version: Mapping[str, Any],
    projection: Mapping[str, Any],
    observed_at: str,
    status: str = "succeeded",
    unknowns: list[str] | None = None,
) -> dict[str, Any]:
    """Persist one bounded connector result cross-bound to its prior request."""

    root = root.resolve()
    store = Store(root)
    run_id = _require_id(run_id, "run_id")
    observation_id = _require_id(observation_id, "observation_id")
    directory = _observation_dir(root, run_id, observation_id)
    request = _read_observation_record(
        store,
        directory / "request.json",
        record_type="context_observation_request",
        digest_field="request_digest",
        timestamp_field="requested_at",
    )
    identity = {
        "run_id": run_id,
        "observation_id": observation_id,
        "request_digest": request_digest,
        "executor_binding": executor_binding,
        "model_identity": model_identity,
    }
    expected = {
        "run_id": request["run_id"],
        "observation_id": request["observation_id"],
        "request_digest": request["request_digest"],
        "executor_binding": request["executor_binding"],
        "model_identity": request["model_identity"],
    }
    if identity != expected:
        raise ContextObservationError("observation receipt request identity mismatch")
    _parse_time(observed_at, "observed_at")
    if status not in {"succeeded", "error"}:
        raise ContextObservationError("unsupported observation status")
    version = _public_object(source_version, "source_version")
    projection_value = _public_object(projection, "projection")
    if set(version) != {"commit_sha", "blob_sha"}:
        raise ContextObservationError("github source_version must contain commit_sha and blob_sha")
    if version.get("commit_sha") != request["source"]["expected_commit_sha"]:
        raise ContextObservationError("observation source commit mismatch")
    blob_sha = _require_text(version.get("blob_sha"), "source_version.blob_sha")
    if len(blob_sha) != 40:
        raise ContextObservationError("source_version.blob_sha must be a full SHA")
    if set(projection_value) != set(request["selected_fields"]):
        raise ContextObservationError("observation projection field set mismatch")
    unknown_values = list(unknowns or [])
    if not all(isinstance(item, str) and item.strip() for item in unknown_values):
        raise ContextObservationError("unknowns must contain non-empty strings")
    if status == "succeeded" and unknown_values:
        raise ContextObservationError("successful observation may not contain unknowns")
    body = {
        "schema_version": 1,
        "record_type": "context_observation_receipt",
        **identity,
        "source": deepcopy(request["source"]),
        "source_version": version,
        "projection": projection_value,
        "status": status,
        "unknowns": unknown_values,
        "evidence_class": request["evidence_class"],
    }
    body["receipt_digest"] = store.stable_digest(body, length=64)
    body["observed_at"] = observed_at
    result = _create_or_replay(
            store,
            directory / "receipt.json",
            body,
            record_type="context_observation_receipt",
            digest_field="receipt_digest",
            timestamp_field="observed_at",
            conflict="immutable context observation receipt conflict",
        )
    verify_context_observation(
        root,
        run_id=run_id,
        observation_id=observation_id,
        enforce_freshness=False,
    )
    return result


def verify_context_observation(
    root: Path,
    *,
    run_id: str,
    observation_id: str,
    now: str | None = None,
    enforce_freshness: bool = True,
) -> dict[str, Any]:
    """Verify request/receipt identity and optional max-age freshness."""

    root = root.resolve()
    store = Store(root)
    directory = _observation_dir(root, run_id, observation_id)
    request = _read_observation_record(
        store,
        directory / "request.json",
        record_type="context_observation_request",
        digest_field="request_digest",
        timestamp_field="requested_at",
    )
    receipt = _read_observation_record(
        store,
        directory / "receipt.json",
        record_type="context_observation_receipt",
        digest_field="receipt_digest",
        timestamp_field="observed_at",
    )
    pairs = [
        ("run_id", run_id),
        ("observation_id", observation_id),
        ("request_digest", request["request_digest"]),
        ("executor_binding", request["executor_binding"]),
        ("model_identity", request["model_identity"]),
        ("source", request["source"]),
        ("evidence_class", request["evidence_class"]),
    ]
    for field, expected in pairs:
        if receipt.get(field) != expected:
            raise ContextObservationError(f"observation receipt {field} mismatch")
    if receipt.get("status") != "succeeded":
        raise ContextObservationError("observation receipt did not succeed")
    if set(receipt.get("projection", {})) != set(request["selected_fields"]):
        raise ContextObservationError("observation projection field set mismatch")
    version = receipt.get("source_version", {})
    if version.get("commit_sha") != request["source"]["expected_commit_sha"]:
        raise ContextObservationError("observation source commit mismatch")
    observed = _parse_time(receipt.get("observed_at"), "observed_at")
    freshness = request["freshness"]
    if enforce_freshness and freshness["kind"] == "max_age":
        current = _parse_time(now or store.utc_now(), "now")
        age = (current - observed).total_seconds()
        if age < 0:
            raise ContextObservationError("observation receipt is future-skewed")
        if age > freshness["max_age_seconds"]:
            raise ContextObservationError("observation receipt is stale")
    return {"request": deepcopy(request), "receipt": deepcopy(receipt)}


def observation_ledger_entry(
    root: Path,
    *,
    run_id: str,
    observation_id: str,
    source_id: str,
) -> dict[str, Any]:
    verified = verify_context_observation(
        root, run_id=run_id, observation_id=observation_id
    )
    request = verified["request"]
    receipt = verified["receipt"]
    source = request["source"]
    return {
        "source_id": _require_text(source_id, "source_id"),
        "observation_id": observation_id,
        "request_ref": (
            f".continual/runs/{run_id}/context-observations/"
            f"{observation_id}/request.json"
        ),
        "request_digest": request["request_digest"],
        "receipt_ref": (
            f".continual/runs/{run_id}/context-observations/"
            f"{observation_id}/receipt.json"
        ),
        "receipt_digest": receipt["receipt_digest"],
        "authoritative_locator": (
            f"github://{source['repository_full_name']}@"
            f"{source['expected_commit_sha']}/{source['path']}"
        ),
        "source_version": deepcopy(receipt["source_version"]),
        "observed_at": receipt["observed_at"],
        "freshness": deepcopy(request["freshness"]),
        "projection": deepcopy(receipt["projection"]),
        "evidence_class": receipt["evidence_class"],
        "unknowns": deepcopy(receipt["unknowns"]),
    }


def verify_context_observation_ledger(
    root: Path, ledger: Mapping[str, Any]
) -> list[dict[str, Any]]:
    if not isinstance(ledger, Mapping) or ledger.get("schema_version") != 1:
        raise ContextObservationError("malformed context observation ledger")
    entries = ledger.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ContextObservationError("context observation ledger entries must be non-empty")
    verified: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    seen_observations: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise ContextObservationError(f"ledger entry {index} must be an object")
        source_id = _require_text(entry.get("source_id"), "source_id")
        run_id = _require_text(entry.get("run_id"), "run_id")
        observation_id = _require_text(
            entry.get("observation_id"), "observation_id"
        )
        if source_id in seen_sources or observation_id in seen_observations:
            raise ContextObservationError("ledger source and observation ids must be unique")
        expected = observation_ledger_entry(
            root,
            run_id=run_id,
            observation_id=observation_id,
            source_id=source_id,
        )
        expected["run_id"] = run_id
        if dict(entry) != expected:
            raise ContextObservationError("context observation ledger binding mismatch")
        seen_sources.add(source_id)
        seen_observations.add(observation_id)
        verified.append(expected)
    return verified
