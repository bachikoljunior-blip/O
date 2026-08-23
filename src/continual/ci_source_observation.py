from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from .store import Store


class CiSourceObservationError(ValueError):
    """Raised when decision-relevant CI lacks an exact usable receipt."""


_SHA = re.compile(r"^[0-9a-f]{40}$")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CiSourceObservationError(f"{label} must be non-empty text")
    return value


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CiSourceObservationError(f"{label} must be a positive integer")
    return value


def _sha(value: Any, label: str) -> str:
    text = _text(value, label).lower()
    if not _SHA.fullmatch(text):
        raise CiSourceObservationError(f"{label} must be a full lowercase SHA")
    return text


def _timestamp(value: Any, label: str) -> datetime:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CiSourceObservationError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise CiSourceObservationError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _required_jobs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise CiSourceObservationError("required_jobs must be a non-empty array")
    jobs: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    seen_names: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise CiSourceObservationError(f"required_jobs[{index}] must be an object")
        exact = dict(raw)
        if set(exact) != {"id", "name"}:
            raise CiSourceObservationError("required job fields must be exactly id and name")
        job_id = _positive_int(exact.get("id"), f"required_jobs[{index}].id")
        name = _text(exact.get("name"), f"required_jobs[{index}].name")
        if job_id in seen_ids or name in seen_names:
            raise CiSourceObservationError("required job ids and names must be unique")
        seen_ids.add(job_id)
        seen_names.add(name)
        jobs.append({"id": job_id, "name": name})
    return sorted(jobs, key=lambda item: item["id"])


def _policy(state: Mapping[str, Any]) -> dict[str, Any] | None:
    value = state.get("ci_source_observation_policy")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise CiSourceObservationError("CI source observation policy must be an object")
    exact = dict(value)
    fields = {
        "required",
        "repository_full_name",
        "exact_head_sha",
        "workflow_run_id",
        "workflow_id",
        "required_jobs",
        "max_age_seconds",
        "executor_binding",
    }
    if set(exact) != fields or exact.get("required") is not True:
        raise CiSourceObservationError("CI source observation policy fields are invalid")
    repository = _text(exact.get("repository_full_name"), "repository_full_name")
    if repository.count("/") != 1:
        raise CiSourceObservationError("repository_full_name must be owner/name")
    max_age = _positive_int(exact.get("max_age_seconds"), "max_age_seconds")
    return {
        "required": True,
        "repository_full_name": repository,
        "exact_head_sha": _sha(exact.get("exact_head_sha"), "exact_head_sha"),
        "workflow_run_id": _positive_int(exact.get("workflow_run_id"), "workflow_run_id"),
        "workflow_id": _positive_int(exact.get("workflow_id"), "workflow_id"),
        "required_jobs": _required_jobs(exact.get("required_jobs")),
        "max_age_seconds": max_age,
        "executor_binding": _text(exact.get("executor_binding"), "executor_binding"),
    }


def _authority(state: Mapping[str, Any], store: Store) -> dict[str, Any]:
    if state.get("status") != "running":
        raise CiSourceObservationError("status must be running")
    generation = _positive_int(state.get("lease_generation"), "lease_generation")
    return {
        "owner_kind": _text(state.get("owner_kind"), "owner_kind"),
        "execution_id": _text(state.get("execution_id"), "execution_id"),
        "lease_generation": generation,
        "fence_token_digest": store.stable_digest(
            _text(state.get("fence_token"), "fence_token"), length=64
        ),
    }


def _directory(root: Path, run_id: str, observation_id: str) -> Path:
    return (
        root.resolve()
        / ".continual"
        / "runs"
        / run_id
        / "ci-source-observations"
        / observation_id
    )


def _write_once(path: Path, value: Mapping[str, Any], digest_field: str) -> dict[str, Any]:
    encoded = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    if path.exists():
        current = _read(path, value["record_type"], digest_field, Store(path.parent))
        if current.get(digest_field) != value.get(digest_field):
            raise CiSourceObservationError("immutable CI source observation conflict")
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
        raise CiSourceObservationError(f"missing or malformed {record_type}") from exc
    if not isinstance(value, dict) or value.get("record_type") != record_type:
        raise CiSourceObservationError(f"malformed {record_type}")
    body = deepcopy(value)
    supplied = body.pop(digest_field, None)
    if supplied != store.stable_digest(body, length=64):
        raise CiSourceObservationError(f"tampered {record_type}")
    return value


def prepare_ci_source_observation(
    root: Path,
    *,
    run_id: str,
    state: Mapping[str, Any],
    model_identity: str,
) -> dict[str, Any]:
    """Precommit one exact-head GitHub Actions observation."""

    root = root.resolve()
    store = Store(root)
    policy = _policy(state)
    if policy is None:
        raise CiSourceObservationError("CI source observation policy is not enabled")
    authority = _authority(state, store)
    seed = {
        "run_id": run_id,
        "source": policy,
        "authority": authority,
        "model_identity": model_identity,
    }
    observation_id = "ci-run-" + store.stable_digest(seed, length=24)
    body = {
        "schema_version": 1,
        "record_type": "ci_source_observation_request",
        "run_id": run_id,
        "observation_id": observation_id,
        "executor_binding": policy["executor_binding"],
        "model_identity": _text(model_identity, "model_identity"),
        "source": {
            "kind": "github_actions",
            "repository_full_name": policy["repository_full_name"],
            "exact_head_sha": policy["exact_head_sha"],
            "workflow_run_id": policy["workflow_run_id"],
            "workflow_id": policy["workflow_id"],
            "required_jobs": deepcopy(policy["required_jobs"]),
        },
        "authority": authority,
        "freshness": {"kind": "max_age", "max_age_seconds": policy["max_age_seconds"]},
        "evidence_class": "operator_connector_readback",
        "operation": "read_workflow_run_and_jobs",
        "requested_at": store.utc_now(),
    }
    body["request_digest"] = store.stable_digest(body, length=64)
    return _write_once(
        _directory(root, run_id, observation_id) / "request.json",
        body,
        "request_digest",
    )


def _run_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    exact = dict(value)
    fields = {"id", "workflow_id", "name", "status", "conclusion", "head_sha"}
    if set(exact) != fields:
        raise CiSourceObservationError("workflow run projection fields mismatch")
    return {
        "id": _positive_int(exact.get("id"), "run.id"),
        "workflow_id": _positive_int(exact.get("workflow_id"), "run.workflow_id"),
        "name": _text(exact.get("name"), "run.name"),
        "status": _text(exact.get("status"), "run.status"),
        "conclusion": _text(exact.get("conclusion"), "run.conclusion"),
        "head_sha": _sha(exact.get("head_sha"), "run.head_sha"),
    }


def _job_projections(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise CiSourceObservationError("jobs projection must be a non-empty array")
    jobs: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    seen_names: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise CiSourceObservationError(f"jobs[{index}] must be an object")
        exact = dict(raw)
        if set(exact) != {"id", "name", "status", "conclusion"}:
            raise CiSourceObservationError("job projection fields mismatch")
        job = {
            "id": _positive_int(exact.get("id"), f"jobs[{index}].id"),
            "name": _text(exact.get("name"), f"jobs[{index}].name"),
            "status": _text(exact.get("status"), f"jobs[{index}].status"),
            "conclusion": _text(exact.get("conclusion"), f"jobs[{index}].conclusion"),
        }
        if job["id"] in seen_ids or job["name"] in seen_names:
            raise CiSourceObservationError("job ids and names must be unique")
        seen_ids.add(job["id"])
        seen_names.add(job["name"])
        jobs.append(job)
    return sorted(jobs, key=lambda item: item["id"])


def record_ci_source_observation_receipt(
    root: Path,
    *,
    run_id: str,
    observation_id: str,
    request_digest: str,
    executor_binding: str,
    model_identity: str,
    workflow_run: Mapping[str, Any],
    jobs: Any,
    observed_at: str,
) -> dict[str, Any]:
    """Cross-bind bounded connector run/jobs output to its prior request."""

    root = root.resolve()
    store = Store(root)
    directory = _directory(root, run_id, observation_id)
    request = _read(
        directory / "request.json",
        "ci_source_observation_request",
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
    expected_identity = {
        "run_id": request["run_id"],
        "observation_id": request["observation_id"],
        "request_digest": request["request_digest"],
        "executor_binding": request["executor_binding"],
        "model_identity": request["model_identity"],
    }
    if identity != expected_identity:
        raise CiSourceObservationError("CI source observation identity mismatch")
    run = _run_projection(workflow_run)
    exact_jobs = _job_projections(jobs)
    source = request["source"]
    if run["id"] != source["workflow_run_id"]:
        raise CiSourceObservationError("workflow run id mismatch")
    if run["workflow_id"] != source["workflow_id"]:
        raise CiSourceObservationError("workflow id mismatch")
    if run["head_sha"] != source["exact_head_sha"]:
        raise CiSourceObservationError("workflow head mismatch")
    if run["status"] != "completed" or run["conclusion"] != "success":
        raise CiSourceObservationError("workflow run is not successful")
    required = source["required_jobs"]
    actual_identity = [{"id": job["id"], "name": job["name"]} for job in exact_jobs]
    if actual_identity != required:
        raise CiSourceObservationError("required CI job topology mismatch")
    if any(job["status"] != "completed" or job["conclusion"] != "success" for job in exact_jobs):
        raise CiSourceObservationError("required CI job is not successful")
    observed = _timestamp(observed_at, "observed_at")
    requested = _timestamp(request.get("requested_at"), "requested_at")
    if observed < requested:
        raise CiSourceObservationError("CI source observation predates its request")
    body = {
        "schema_version": 1,
        "record_type": "ci_source_observation_receipt",
        **identity,
        "source": deepcopy(source),
        "source_version": {
            "exact_head_sha": run["head_sha"],
            "workflow_run_id": run["id"],
            "workflow_id": run["workflow_id"],
        },
        "projection": {"workflow_run": run, "required_jobs": exact_jobs},
        "authority": deepcopy(request["authority"]),
        "observed_at": observed_at,
        "status": "succeeded",
        "evidence_class": request["evidence_class"],
        "unknowns": ["connector observation is timestamped, not linearizable latest proof"],
    }
    body["receipt_digest"] = store.stable_digest(body, length=64)
    return _write_once(
        directory / "receipt.json", body, "receipt_digest"
    )


def verify_ci_source_observation(
    root: Path,
    *,
    run_id: str,
    state: Mapping[str, Any],
    now: str,
) -> dict[str, Any] | None:
    """Return the exact newest CI receipt or fail closed when policy is enabled."""

    root = root.resolve()
    store = Store(root)
    policy = _policy(state)
    if policy is None:
        return None
    authority = _authority(state, store)
    current = _timestamp(now, "now")
    base = root / ".continual" / "runs" / run_id / "ci-source-observations"
    candidates: list[dict[str, Any]] = []
    for request_path in sorted(base.glob("ci-run-*/request.json")):
        request = _read(
            request_path,
            "ci_source_observation_request",
            "request_digest",
            store,
        )
        receipt_path = request_path.parent / "receipt.json"
        if not receipt_path.is_file():
            continue
        receipt = _read(
            receipt_path,
            "ci_source_observation_receipt",
            "receipt_digest",
            store,
        )
        expected_source = {
            "kind": "github_actions",
            "repository_full_name": policy["repository_full_name"],
            "exact_head_sha": policy["exact_head_sha"],
            "workflow_run_id": policy["workflow_run_id"],
            "workflow_id": policy["workflow_id"],
            "required_jobs": policy["required_jobs"],
        }
        if (
            request.get("run_id") != run_id
            or request.get("executor_binding") != policy["executor_binding"]
            or request.get("source") != expected_source
            or request.get("authority") != authority
        ):
            continue
        if receipt.get("request_digest") != request["request_digest"]:
            raise CiSourceObservationError("CI source request mismatch")
        if receipt.get("executor_binding") != request["executor_binding"]:
            raise CiSourceObservationError("CI source executor mismatch")
        if receipt.get("model_identity") != request["model_identity"]:
            raise CiSourceObservationError("CI source model mismatch")
        if receipt.get("source") != request["source"]:
            raise CiSourceObservationError("CI source identity mismatch")
        if receipt.get("authority") != authority or receipt.get("status") != "succeeded":
            raise CiSourceObservationError("CI source authority mismatch")
        projection = receipt.get("projection")
        if not isinstance(projection, Mapping):
            raise CiSourceObservationError("CI source projection is malformed")
        run = _run_projection(projection.get("workflow_run", {}))
        jobs = _job_projections(projection.get("required_jobs"))
        if run["id"] != policy["workflow_run_id"] or run["workflow_id"] != policy["workflow_id"]:
            raise CiSourceObservationError("CI source run mismatch")
        if run["head_sha"] != policy["exact_head_sha"]:
            raise CiSourceObservationError("CI source head mismatch")
        if run["status"] != "completed" or run["conclusion"] != "success":
            raise CiSourceObservationError("CI source run is not successful")
        if [{"id": j["id"], "name": j["name"]} for j in jobs] != policy["required_jobs"]:
            raise CiSourceObservationError("CI source topology mismatch")
        if any(j["status"] != "completed" or j["conclusion"] != "success" for j in jobs):
            raise CiSourceObservationError("CI source required job is not successful")
        observed = _timestamp(receipt.get("observed_at"), "observed_at")
        requested = _timestamp(request.get("requested_at"), "requested_at")
        if observed < requested:
            raise CiSourceObservationError("CI source observation predates request")
        age = (current - observed).total_seconds()
        if age < 0:
            raise CiSourceObservationError("CI source observation is future-skewed")
        if age > policy["max_age_seconds"]:
            continue
        candidates.append({"request": request, "receipt": receipt, "age": age})
    if not candidates:
        raise CiSourceObservationError("no matching fresh CI source observation receipt")
    selected = max(candidates, key=lambda item: item["receipt"]["observed_at"])
    request = selected["request"]
    receipt = selected["receipt"]
    return {
        "source_id": "github_actions_ci",
        "observation_id": request["observation_id"],
        "request_digest": request["request_digest"],
        "receipt_digest": receipt["receipt_digest"],
        "authoritative_locator": (
            f"github://{policy['repository_full_name']}/actions/runs/"
            f"{policy['workflow_run_id']}@{policy['exact_head_sha']}"
        ),
        "source_version": deepcopy(receipt["source_version"]),
        "observed_at": receipt["observed_at"],
        "freshness": {
            "kind": "max_age",
            "age_seconds": selected["age"],
            "max_age_seconds": policy["max_age_seconds"],
        },
        "projection": deepcopy(receipt["projection"]),
        "evidence_class": receipt["evidence_class"],
        "unknowns": deepcopy(receipt["unknowns"]),
        "claim_scope": "internal_ci_provenance_not_behavioral_or_completion_evidence",
    }
