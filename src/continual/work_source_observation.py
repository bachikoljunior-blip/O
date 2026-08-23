from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .store import Store


class WorkSourceObservationError(ValueError):
    """Raised when a mandatory Work source lacks a usable remote receipt."""


_SHA = re.compile(r"^[0-9a-f]{40}$")
_PUBLIC_AUTHORITY_FIELDS = (
    "status",
    "owner_kind",
    "execution_id",
    "lease_generation",
    "fence_token_digest",
    "heartbeat_at",
)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkSourceObservationError(f"{label} must be non-empty")
    return value


def _timestamp(value: Any, label: str) -> datetime:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorkSourceObservationError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise WorkSourceObservationError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _sha(value: Any, label: str) -> str:
    text = _text(value, label).lower()
    if not _SHA.fullmatch(text):
        raise WorkSourceObservationError(f"{label} must be a full lowercase SHA")
    return text


def _policy(state: Mapping[str, Any]) -> dict[str, Any] | None:
    value = state.get("authoritative_source_observation_policy")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise WorkSourceObservationError("source observation policy must be an object")
    exact = dict(value)
    if exact.get("required") is not True:
        raise WorkSourceObservationError("source observation policy must be required")
    repository = _text(exact.get("repository_full_name"), "repository_full_name")
    if repository.count("/") != 1:
        raise WorkSourceObservationError("repository_full_name must be owner/name")
    if exact.get("ref") != "main":
        raise WorkSourceObservationError("source observation ref must be main")
    max_age = exact.get("max_age_seconds")
    if isinstance(max_age, bool) or not isinstance(max_age, int) or max_age < 1:
        raise WorkSourceObservationError("max_age_seconds must be positive")
    executor = _text(exact.get("executor_binding"), "executor_binding")
    if set(exact) != {
        "required",
        "repository_full_name",
        "ref",
        "max_age_seconds",
        "executor_binding",
    }:
        raise WorkSourceObservationError("source observation policy has unsupported fields")
    return {
        "required": True,
        "repository_full_name": repository,
        "ref": "main",
        "max_age_seconds": max_age,
        "executor_binding": executor,
    }


def _authority(state: Mapping[str, Any], store: Store) -> dict[str, Any]:
    if state.get("status") != "running":
        raise WorkSourceObservationError("status must be running")
    generation = state.get("lease_generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise WorkSourceObservationError("lease_generation must be positive")
    return {
        "status": "running",
        "owner_kind": _text(state.get("owner_kind"), "owner_kind"),
        "execution_id": _text(state.get("execution_id"), "execution_id"),
        "lease_generation": generation,
        "fence_token_digest": store.stable_digest(
            _text(state.get("fence_token"), "fence_token"), length=64
        ),
        "heartbeat_at": _text(state.get("heartbeat_at"), "heartbeat_at"),
    }


def _directory(root: Path, run_id: str, observation_id: str) -> Path:
    return (
        root.resolve()
        / ".continual"
        / "runs"
        / run_id
        / "work-source-observations"
        / observation_id
    )


def _write_once(path: Path, value: Mapping[str, Any], digest_field: str) -> dict[str, Any]:
    encoded = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise WorkSourceObservationError(
                "malformed immutable source observation"
            ) from exc
        if not isinstance(current, dict):
            raise WorkSourceObservationError("malformed immutable source observation")
        body = deepcopy(current)
        supplied = body.pop(digest_field, None)
        if supplied != Store(path.parent).stable_digest(body, length=64):
            raise WorkSourceObservationError("tampered immutable source observation")
        if current.get(digest_field) != value.get(digest_field):
            raise WorkSourceObservationError("immutable source observation conflict")
        return current
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _write_once(path, value, digest_field)
    with os.fdopen(fd, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return deepcopy(dict(value))


def _read(path: Path, record_type: str, digest_field: str, store: Store) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise WorkSourceObservationError(f"missing or malformed {record_type}") from exc
    if not isinstance(value, dict) or value.get("record_type") != record_type:
        raise WorkSourceObservationError(f"malformed {record_type}")
    body = deepcopy(value)
    supplied = body.pop(digest_field, None)
    if supplied != store.stable_digest(body, length=64):
        raise WorkSourceObservationError(f"tampered {record_type}")
    return value


def prepare_work_source_observation(
    root: Path,
    *,
    run_id: str,
    state: Mapping[str, Any],
    state_blob_sha: str,
    expected_commit_sha: str,
    model_identity: str,
) -> dict[str, Any]:
    """Precommit an immutable connector read before the read or semantic freeze."""

    root = root.resolve()
    store = Store(root)
    policy = _policy(state)
    if policy is None:
        raise WorkSourceObservationError("source observation policy is not enabled")
    blob = _sha(state_blob_sha, "state_blob_sha")
    commit = _sha(expected_commit_sha, "expected_commit_sha")
    authority = _authority(state, store)
    seed = {
        "run_id": run_id,
        "repository_full_name": policy["repository_full_name"],
        "ref": policy["ref"],
        "expected_commit_sha": commit,
        "expected_blob_sha": blob,
        "authority": authority,
        "executor_binding": policy["executor_binding"],
    }
    observation_id = "work-state-" + store.stable_digest(seed, length=24)
    body = {
        "schema_version": 1,
        "record_type": "work_source_observation_request",
        "run_id": run_id,
        "observation_id": observation_id,
        "executor_binding": policy["executor_binding"],
        "model_identity": _text(model_identity, "model_identity"),
        "source": {
            "kind": "github_file",
            "repository_full_name": policy["repository_full_name"],
            "path": "agi/WORK_EXECUTION_STATE.json",
            "ref": policy["ref"],
            "expected_commit_sha": commit,
            "expected_blob_sha": blob,
        },
        "authority": authority,
        "freshness": {
            "kind": "max_age",
            "max_age_seconds": policy["max_age_seconds"],
            "invalidates_on": [
                "resolved commit change",
                "state blob change",
                "authority projection change",
                "observation age",
            ],
        },
        "evidence_class": "operator_connector_readback",
        "operation": "read",
        "requested_at": store.utc_now(),
    }
    body["request_digest"] = store.stable_digest(body, length=64)
    return _write_once(
        _directory(root, run_id, observation_id) / "request.json",
        body,
        "request_digest",
    )


def record_work_source_observation_receipt(
    root: Path,
    *,
    run_id: str,
    observation_id: str,
    request_digest: str,
    executor_binding: str,
    model_identity: str,
    commit_sha: str,
    blob_sha: str,
    projection: Mapping[str, Any],
    observed_at: str,
) -> dict[str, Any]:
    """Persist the bounded connector result cross-bound to its prior request."""

    root = root.resolve()
    store = Store(root)
    directory = _directory(root, run_id, observation_id)
    request = _read(
        directory / "request.json",
        "work_source_observation_request",
        "request_digest",
        store,
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
        raise WorkSourceObservationError("source observation identity mismatch")
    normalized_commit = _sha(commit_sha, "commit_sha")
    normalized_blob = _sha(blob_sha, "blob_sha")
    if normalized_commit != request["source"]["expected_commit_sha"]:
        raise WorkSourceObservationError("source observation commit mismatch")
    if normalized_blob != request["source"]["expected_blob_sha"]:
        raise WorkSourceObservationError("source observation blob mismatch")
    exact_projection = dict(projection)
    if set(exact_projection) != set(_PUBLIC_AUTHORITY_FIELDS):
        raise WorkSourceObservationError("source observation projection fields mismatch")
    if exact_projection != request["authority"]:
        raise WorkSourceObservationError("source observation authority mismatch")
    observed = _timestamp(observed_at, "observed_at")
    requested = _timestamp(request.get("requested_at"), "requested_at")
    if observed < requested:
        raise WorkSourceObservationError(
            "source observation receipt predates its precommit request"
        )
    body = {
        "schema_version": 1,
        "record_type": "work_source_observation_receipt",
        **identity,
        "source": deepcopy(request["source"]),
        "source_version": {
            "commit_sha": normalized_commit,
            "blob_sha": normalized_blob,
        },
        "projection": deepcopy(exact_projection),
        "observed_at": observed_at,
        "status": "succeeded",
        "evidence_class": request["evidence_class"],
        "unknowns": [],
    }
    body["receipt_digest"] = store.stable_digest(body, length=64)
    return _write_once(
        directory / "receipt.json", body, "receipt_digest"
    )


def verify_work_source_observation(
    root: Path,
    *,
    run_id: str,
    state: Mapping[str, Any],
    state_blob_sha: str,
    now: str,
) -> dict[str, Any] | None:
    """Return the newest exact fresh receipt, or fail closed when required."""

    root = root.resolve()
    store = Store(root)
    policy = _policy(state)
    if policy is None:
        return None
    blob = _sha(state_blob_sha, "state_blob_sha")
    authority = _authority(state, store)
    current = _timestamp(now, "now")
    candidates: list[dict[str, Any]] = []
    base = root / ".continual" / "runs" / run_id / "work-source-observations"
    for request_path in sorted(base.glob("work-state-*/request.json")):
        directory = request_path.parent
        try:
            request = _read(
                request_path,
                "work_source_observation_request",
                "request_digest",
                store,
            )
            receipt_path = directory / "receipt.json"
            if not receipt_path.is_file():
                continue
            receipt = _read(
                receipt_path,
                "work_source_observation_receipt",
                "receipt_digest",
                store,
            )
            if (
                request.get("run_id") != run_id
                or request.get("executor_binding") != policy["executor_binding"]
                or request.get("authority") != authority
                or request.get("source", {}).get("repository_full_name")
                != policy["repository_full_name"]
                or request.get("source", {}).get("ref") != policy["ref"]
                or request.get("source", {}).get("expected_blob_sha") != blob
            ):
                continue
            if receipt.get("request_digest") != request["request_digest"]:
                raise WorkSourceObservationError("source observation request mismatch")
            if receipt.get("executor_binding") != request["executor_binding"]:
                raise WorkSourceObservationError("source observation executor mismatch")
            if receipt.get("model_identity") != request["model_identity"]:
                raise WorkSourceObservationError("source observation model mismatch")
            if receipt.get("source") != request["source"]:
                raise WorkSourceObservationError("source observation source mismatch")
            if receipt.get("source_version") != {
                "commit_sha": request["source"]["expected_commit_sha"],
                "blob_sha": blob,
            }:
                raise WorkSourceObservationError("source observation version mismatch")
            if receipt.get("projection") != authority or receipt.get("status") != "succeeded":
                raise WorkSourceObservationError("source observation authority mismatch")
            observed = _timestamp(receipt.get("observed_at"), "observed_at")
            age = (current - observed).total_seconds()
            if age < 0:
                raise WorkSourceObservationError("source observation is future-skewed")
            if age > policy["max_age_seconds"]:
                continue
            candidates.append(
                {
                    "observation_id": request["observation_id"],
                    "request_digest": request["request_digest"],
                    "receipt_digest": receipt["receipt_digest"],
                    "source_version": deepcopy(receipt["source_version"]),
                    "observed_at": receipt["observed_at"],
                    "age_seconds": age,
                    "max_age_seconds": policy["max_age_seconds"],
                    "evidence_class": receipt["evidence_class"],
                    "claim_scope": "timestamped_connector_observation_not_linearizable_latest_proof",
                }
            )
        except WorkSourceObservationError:
            raise
    if not candidates:
        raise WorkSourceObservationError(
            "mandatory Work source lacks a matching fresh authoritative observation"
        )
    return max(candidates, key=lambda value: value["observed_at"])
