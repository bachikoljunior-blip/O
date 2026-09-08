from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping


QUEUE_PATH = Path("agi/USER_REQUEST_QUEUE.json")
REQUEST_KINDS = {"ambiguity", "proposal", "environment", "permission", "operation", "information"}
REQUEST_PRIORITIES = {"required", "helpful"}
REQUEST_STATUSES = {"open", "fulfilled", "withdrawn", "superseded"}
FORBIDDEN_SECRET_KEYS = {
    "api_key",
    "credential",
    "credentials",
    "password",
    "private_key",
    "secret",
    "token",
}


class UserRequestQueueError(RuntimeError):
    pass


def _aware_datetime(value: Any, *, field: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field} must be a non-empty ISO-8601 string")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{field} must be a valid ISO-8601 timestamp")
        return None
    if parsed.tzinfo is None:
        errors.append(f"{field} must include a timezone")
        return None
    return parsed


def _find_forbidden_secret_keys(value: Any, path: str = "queue") -> list[str]:
    errors: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized = key.lower().replace("-", "_")
            child_path = f"{path}.{key}"
            if normalized in FORBIDDEN_SECRET_KEYS:
                errors.append(f"forbidden secret-bearing field: {child_path}")
            errors.extend(_find_forbidden_secret_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_find_forbidden_secret_keys(child, f"{path}[{index}]"))
    return errors


def validate_user_request_queue(value: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if value.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    revision = value.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        errors.append("revision must be a non-negative integer")

    policy = value.get("policy")
    if not isinstance(policy, Mapping):
        errors.append("policy must be an object")
    else:
        required_policy = {
            "project_waiting_is_stop_condition": False,
            "secrets_allowed": False,
            "open_requests_require_safe_alternative_work": True,
            "open_requests_require_finite_reevaluation": True,
            "review_each_root_cycle": True,
        }
        for key, expected in required_policy.items():
            if policy.get(key) is not expected:
                errors.append(f"policy.{key} must be {expected!r}")

    requests = value.get("requests")
    if not isinstance(requests, list):
        errors.append("requests must be an array")
        requests = []
    seen_ids: set[str] = set()
    for index, request in enumerate(requests):
        prefix = f"requests[{index}]"
        if not isinstance(request, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        request_id = request.get("id")
        if not isinstance(request_id, str) or not request_id.strip():
            errors.append(f"{prefix}.id must be non-empty")
        elif request_id in seen_ids:
            errors.append(f"duplicate request id: {request_id}")
        else:
            seen_ids.add(request_id)
        if request.get("kind") not in REQUEST_KINDS:
            errors.append(f"{prefix}.kind is invalid")
        if request.get("priority") not in REQUEST_PRIORITIES:
            errors.append(f"{prefix}.priority is invalid")
        status = request.get("status")
        if status not in REQUEST_STATUSES:
            errors.append(f"{prefix}.status is invalid")
        for field in ("summary", "reason", "requested_user_action", "reevaluate_on"):
            field_value = request.get(field)
            if not isinstance(field_value, str) or not field_value.strip():
                errors.append(f"{prefix}.{field} must be non-empty")
        _aware_datetime(request.get("created_at"), field=f"{prefix}.created_at", errors=errors)
        _aware_datetime(request.get("reevaluate_by"), field=f"{prefix}.reevaluate_by", errors=errors)
        if status == "open":
            if request.get("non_blocking") is not True:
                errors.append(f"{prefix}.non_blocking must be true while open")
            alternatives = request.get("safe_alternative_work")
            if not isinstance(alternatives, list) or not alternatives or not all(
                isinstance(item, str) and item.strip() for item in alternatives
            ):
                errors.append(f"{prefix}.safe_alternative_work must contain non-empty actions")

    _aware_datetime(value.get("updated_at"), field="updated_at", errors=errors)
    errors.extend(_find_forbidden_secret_keys(value))
    return errors


def load_user_request_queue(root: Path) -> dict[str, Any]:
    path = root / QUEUE_PATH
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UserRequestQueueError(f"cannot read user request queue: {exc}") from exc
    if not isinstance(value, dict):
        raise UserRequestQueueError("user request queue must be a JSON object")
    errors = validate_user_request_queue(value)
    if errors:
        raise UserRequestQueueError("invalid user request queue: " + "; ".join(errors))
    return value


def due_user_requests(
    value: Mapping[str, Any], *, now: datetime | None = None
) -> list[dict[str, Any]]:
    errors = validate_user_request_queue(value)
    if errors:
        raise UserRequestQueueError("invalid user request queue: " + "; ".join(errors))
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise UserRequestQueueError("now must include a timezone")
    due: list[dict[str, Any]] = []
    for request in value["requests"]:
        if request["status"] != "open":
            continue
        deadline = datetime.fromisoformat(request["reevaluate_by"].replace("Z", "+00:00"))
        if deadline <= current:
            due.append(dict(request))
    return due


def enqueue_user_request(
    root: Path,
    request: Mapping[str, Any],
    *,
    expected_revision: int,
    updated_at: datetime | None = None,
) -> dict[str, Any]:
    """Atomically append one idempotent request under the single-writer lease.

    Repository publication still requires the normal Git/GitHub expected-head checks. This local
    revision binding prevents stale in-process writers and duplicate request identifiers.
    """

    current = load_user_request_queue(root)
    existing = next(
        (item for item in current["requests"] if item.get("id") == request.get("id")),
        None,
    )
    if existing is not None:
        if dict(existing) == dict(request):
            return current
        raise UserRequestQueueError(f"request id already exists with different content: {request.get('id')}")
    if current["revision"] != expected_revision:
        raise UserRequestQueueError(
            f"queue revision conflict: expected {expected_revision}, observed {current['revision']}"
        )

    candidate = deepcopy(current)
    candidate["revision"] += 1
    candidate["requests"].append(dict(request))
    timestamp = updated_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise UserRequestQueueError("updated_at must include a timezone")
    candidate["updated_at"] = timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    errors = validate_user_request_queue(candidate)
    if errors:
        raise UserRequestQueueError("invalid queued request: " + "; ".join(errors))

    path = root / QUEUE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(candidate, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    handle, temporary_name = tempfile.mkstemp(prefix=".user-request-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as temporary:
            temporary.write(serialized)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return candidate
