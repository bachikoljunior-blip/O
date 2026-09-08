from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .store import Store
from .work_session import WorkSessionError, verified_work_invocation


class BehavioralOutcomeError(ValueError):
    """Raised when a precommitted behavioral outcome fails closed."""


CLAIM_SCOPE = "internal_provider_behavioral_outcome_not_independent_or_completion_evidence"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_INVOCATION = re.compile(r"^invoke-[0-9a-f]{24}$")
_SECRET_TEXT = re.compile(
    r"(?:\bBearer\s+[A-Za-z0-9._~+/=-]{12,}|\b(?:ghp|github_pat|sk)-[A-Za-z0-9_-]{12,})",
    re.IGNORECASE,
)
_FORBIDDEN_KEYS = {
    "api_key",
    "authorization",
    "chain_of_thought",
    "cookie",
    "credentials",
    "hidden_reasoning",
    "password",
    "raw_system_prompt",
    "scratchpad",
    "secret",
    "system_prompt",
}


def _text(value: Any, label: str, *, maximum: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BehavioralOutcomeError(f"{label} must be non-empty text")
    if maximum is not None and len(value.encode("utf-8")) > maximum:
        raise BehavioralOutcomeError(f"{label} exceeds its byte budget")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise BehavioralOutcomeError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _timestamp(value: Any, label: str) -> datetime:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BehavioralOutcomeError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise BehavioralOutcomeError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BehavioralOutcomeError("value must be bounded canonical JSON") from exc


def _walk_public(value: Any, path: str = "answer") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_KEYS:
                raise BehavioralOutcomeError(f"forbidden private field at {path}.{key}")
            _walk_public(child, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _walk_public(child, f"{path}[{index}]")
        return
    if isinstance(value, str) and _SECRET_TEXT.search(value):
        raise BehavioralOutcomeError(f"secret-like text is forbidden at {path}")


def _authority(state: Mapping[str, Any], store: Store, *, now: str) -> dict[str, Any]:
    if not isinstance(state, Mapping) or state.get("status") != "running":
        raise BehavioralOutcomeError("authority status must be running")
    generation = state.get("lease_generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise BehavioralOutcomeError("lease_generation must be positive")
    inbox = state.get("user_input_inbox")
    if not isinstance(inbox, Mapping):
        raise BehavioralOutcomeError("user_input_inbox authority is missing")
    revision = inbox.get("highest_acknowledged_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise BehavioralOutcomeError("inbox revision must be non-negative")
    stale_after = state.get("stale_after_seconds")
    if isinstance(stale_after, bool) or not isinstance(stale_after, int) or stale_after < 1:
        raise BehavioralOutcomeError("stale_after_seconds must be positive")
    heartbeat_text = _text(state.get("heartbeat_at"), "heartbeat_at")
    heartbeat = _timestamp(heartbeat_text, "heartbeat_at")
    current = _timestamp(now, "now")
    age = (current - heartbeat).total_seconds()
    if age < -120:
        raise BehavioralOutcomeError("authority heartbeat is future-skewed")
    if age > stale_after:
        raise BehavioralOutcomeError("authority heartbeat is stale")
    return {
        "status": "running",
        "owner_kind": _text(state.get("owner_kind"), "owner_kind"),
        "execution_id": _text(state.get("execution_id"), "execution_id"),
        "lease_generation": generation,
        "fence_token_digest": store.stable_digest(
            _text(state.get("fence_token"), "fence_token"), length=64
        ),
        "highest_acknowledged_inbox_revision": revision,
        "heartbeat_at": heartbeat_text,
        "stale_after_seconds": stale_after,
    }


def _stable_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(item)
        for key, item in value.items()
        if key not in {"heartbeat_at", "stale_after_seconds"}
    }


def _task(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "task_id",
        "instruction",
        "input",
        "answer_format",
        "response_pointer",
    }:
        raise BehavioralOutcomeError("task has an unexpected schema")
    task = {
        "task_id": _text(value.get("task_id"), "task_id", maximum=200),
        "instruction": _text(value.get("instruction"), "instruction", maximum=2048),
        "input": deepcopy(value.get("input")),
        "answer_format": value.get("answer_format"),
        "response_pointer": deepcopy(value.get("response_pointer")),
    }
    if task["answer_format"] != "canonical_json":
        raise BehavioralOutcomeError("answer_format must be canonical_json")
    if task["response_pointer"] != ["result", "behavioral_answer"]:
        raise BehavioralOutcomeError("response_pointer must target result.behavioral_answer")
    _walk_public(task["input"], "task.input")
    if len(_canonical_bytes(task["input"])) > 8192:
        raise BehavioralOutcomeError("task input exceeds its byte budget")
    return task


def _rubric(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "judge_kind",
        "judge_version",
        "expected_answer",
        "success_threshold",
    }:
        raise BehavioralOutcomeError("rubric has an unexpected schema")
    if value.get("judge_kind") != "exact_canonical_json":
        raise BehavioralOutcomeError("judge_kind must be exact_canonical_json")
    if value.get("judge_version") != "exact-canonical-json-v1":
        raise BehavioralOutcomeError("judge_version is unsupported")
    threshold = value.get("success_threshold")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise BehavioralOutcomeError("success_threshold must be numeric")
    if float(threshold) != 1.0:
        raise BehavioralOutcomeError("exact judge success_threshold must be 1.0")
    expected = deepcopy(value.get("expected_answer"))
    _walk_public(expected, "rubric.expected_answer")
    if len(_canonical_bytes(expected)) > 4096:
        raise BehavioralOutcomeError("expected answer exceeds its byte budget")
    return {
        "judge_kind": "exact_canonical_json",
        "judge_version": "exact-canonical-json-v1",
        "expected_answer": expected,
        "success_threshold": 1.0,
    }


def _directory(root: Path, run_id: str, outcome_id: str) -> Path:
    return root.resolve() / ".continual" / "runs" / run_id / "behavioral-outcomes" / outcome_id


def _read_record(path: Path, record_type: str, digest_field: str, store: Store) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise BehavioralOutcomeError(f"missing or malformed {record_type}") from exc
    if not isinstance(value, dict) or value.get("record_type") != record_type:
        raise BehavioralOutcomeError(f"malformed {record_type}")
    body = deepcopy(value)
    supplied = body.pop(digest_field, None)
    if supplied != store.stable_digest(body, length=64):
        raise BehavioralOutcomeError(f"tampered {record_type}")
    return value


def _write_once(path: Path, value: Mapping[str, Any], digest_field: str) -> dict[str, Any]:
    store = Store(path.parent)
    if path.exists():
        current = _read_record(path, str(value["record_type"]), digest_field, store)
        if current != value:
            raise BehavioralOutcomeError(f"immutable {value['record_type']} conflict")
        return current
    encoded = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
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


def prepare_behavioral_outcome(
    root: Path,
    *,
    run_id: str,
    state: Mapping[str, Any],
    task: Mapping[str, Any],
    rubric: Mapping[str, Any],
    executor_binding: str,
    model_identity: str,
    now: str | None = None,
    max_response_bytes: int = 4096,
) -> dict[str, Any]:
    """Freeze a bounded public task and deterministic judge before execution."""

    root = root.resolve()
    store = Store(root)
    current = now or store.utc_now()
    authority = _authority(state, store, now=current)
    exact_task = _task(task)
    exact_rubric = _rubric(rubric)
    if isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int):
        raise BehavioralOutcomeError("max_response_bytes must be an integer")
    if max_response_bytes < 1 or max_response_bytes > 16384:
        raise BehavioralOutcomeError("max_response_bytes is outside the safe bound")
    executor = _text(executor_binding, "executor_binding")
    model = _text(model_identity, "model_identity")
    frozen = {
        "run_id": run_id,
        "task": exact_task,
        "rubric": exact_rubric,
        "budget": {"max_response_bytes": max_response_bytes},
        "executor_binding": executor,
        "model_identity": model,
        "authority": _stable_authority(authority),
        "claim_scope": CLAIM_SCOPE,
    }
    outcome_id = "behavioral-outcome-" + store.stable_digest(frozen, length=24)
    path = _directory(root, run_id, outcome_id) / "request.json"
    if path.exists():
        existing = _read_record(
            path, "behavioral_outcome_request", "request_digest", store
        )
        existing_frozen = {
            key: deepcopy(existing[key])
            for key in frozen
        }
        if existing_frozen != frozen:
            raise BehavioralOutcomeError("immutable behavioral outcome request conflict")
        return existing
    body = {
        "schema_version": 1,
        "record_type": "behavioral_outcome_request",
        "outcome_id": outcome_id,
        **frozen,
        "prepared_authority": authority,
        "requested_at": current,
    }
    body["task_digest"] = store.stable_digest(exact_task, length=64)
    body["rubric_digest"] = store.stable_digest(exact_rubric, length=64)
    body["request_digest"] = store.stable_digest(body, length=64)
    return _write_once(path, body, "request_digest")


def behavioral_child_binding(request: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact small binding that must be frozen in the child unit."""

    return {
        "outcome_id": _text(request.get("outcome_id"), "outcome_id"),
        "request_digest": _digest(request.get("request_digest"), "request_digest"),
        "task_digest": _digest(request.get("task_digest"), "task_digest"),
        "rubric_digest": _digest(request.get("rubric_digest"), "rubric_digest"),
        "response_pointer": ["result", "behavioral_answer"],
    }


def _load_request(root: Path, run_id: str, outcome_id: str) -> tuple[Store, dict[str, Any]]:
    store = Store(root)
    request = _read_record(
        _directory(root, run_id, outcome_id) / "request.json",
        "behavioral_outcome_request",
        "request_digest",
        store,
    )
    if request.get("run_id") != run_id or request.get("outcome_id") != outcome_id:
        raise BehavioralOutcomeError("behavioral outcome request identity mismatch")
    if request.get("task_digest") != store.stable_digest(_task(request.get("task", {})), length=64):
        raise BehavioralOutcomeError("behavioral outcome task digest mismatch")
    if request.get("rubric_digest") != store.stable_digest(_rubric(request.get("rubric", {})), length=64):
        raise BehavioralOutcomeError("behavioral outcome rubric digest mismatch")
    if request.get("claim_scope") != CLAIM_SCOPE:
        raise BehavioralOutcomeError("behavioral outcome claim scope mismatch")
    return store, request


def _ledger_path(root: Path, run_id: str) -> Path:
    return root / ".continual" / "runs" / run_id / "behavioral-outcomes" / "ledger.json"


def _updated_ledger(
    root: Path, run_id: str, receipt: Mapping[str, Any], store: Store
) -> dict[str, Any]:
    path = _ledger_path(root, run_id)
    if path.exists():
        ledger = _read_record(path, "behavioral_outcome_ledger", "ledger_digest", store)
    else:
        ledger = {
            "schema_version": 1,
            "record_type": "behavioral_outcome_ledger",
            "run_id": run_id,
            "claim_scope": CLAIM_SCOPE,
            "internal_observation_count": 0,
            "receipts": [],
            "updated_at": receipt["observed_at"],
        }
    if ledger.get("claim_scope") != CLAIM_SCOPE or ledger.get("run_id") != run_id:
        raise BehavioralOutcomeError("behavioral outcome ledger scope mismatch")
    receipts = ledger.get("receipts")
    count = ledger.get("internal_observation_count")
    if not isinstance(receipts, list) or count != len(receipts):
        raise BehavioralOutcomeError("behavioral outcome ledger count mismatch")
    entry = {
        "outcome_id": receipt["outcome_id"],
        "receipt_digest": receipt["receipt_digest"],
        "passed": receipt["judgment"]["passed"],
        "score": receipt["judgment"]["score"],
        "claim_scope": CLAIM_SCOPE,
    }
    matches = [item for item in receipts if item.get("outcome_id") == entry["outcome_id"]]
    if matches:
        if matches != [entry]:
            raise BehavioralOutcomeError("behavioral outcome ledger replay conflict")
        return ledger
    next_ledger = deepcopy(ledger)
    next_ledger.pop("ledger_digest", None)
    next_ledger["receipts"].append(entry)
    next_ledger["receipts"] = sorted(
        next_ledger["receipts"], key=lambda item: item["outcome_id"]
    )
    next_ledger["internal_observation_count"] = len(next_ledger["receipts"])
    next_ledger["updated_at"] = receipt["observed_at"]
    next_ledger["ledger_digest"] = store.stable_digest(next_ledger, length=64)
    return next_ledger


def record_behavioral_outcome_from_work_invocation(
    root: Path,
    *,
    run_id: str,
    outcome_id: str,
    request_digest: str,
    work_invocation_id: str,
    state: Mapping[str, Any],
    now: str | None = None,
) -> dict[str, Any]:
    """Project one immutable Work response, judge it, and issue a scoped receipt."""

    root = root.resolve()
    store, request = _load_request(root, run_id, outcome_id)
    if request["request_digest"] != _digest(request_digest, "request_digest"):
        raise BehavioralOutcomeError("behavioral outcome request digest mismatch")
    if not isinstance(work_invocation_id, str) or _INVOCATION.fullmatch(work_invocation_id) is None:
        raise BehavioralOutcomeError("invalid Work invocation_id")
    observed_at = now or store.utc_now()
    current_authority = _authority(state, store, now=observed_at)
    if _stable_authority(current_authority) != request["authority"]:
        raise BehavioralOutcomeError("behavioral outcome authority changed")
    directory = _directory(root, run_id, outcome_id)
    existing_receipt_path = directory / "receipt.json"
    receipt_authority = current_authority
    if existing_receipt_path.exists():
        existing_receipt = _read_record(
            existing_receipt_path,
            "behavioral_outcome_receipt",
            "receipt_digest",
            store,
        )
        existing_authority = existing_receipt.get("authority")
        if not isinstance(existing_authority, Mapping) or _stable_authority(
            existing_authority
        ) != request["authority"]:
            raise BehavioralOutcomeError("stored behavioral outcome authority mismatch")
        # A replay may occur after a harmless heartbeat refresh. Preserve the
        # original receipt clock and bytes while requiring the same live fence.
        observed_at = _text(existing_receipt.get("observed_at"), "observed_at")
        receipt_authority = deepcopy(dict(existing_authority))
    try:
        work = verified_work_invocation(root, work_invocation_id)
    except WorkSessionError as exc:
        raise BehavioralOutcomeError(f"invalid bound Work invocation: {exc}") from exc
    work_request = work["request"]
    work_response = work["response"]
    if (
        work_request.get("run_id") != run_id
        or work_request.get("component") != "execute"
        or work_request.get("executor_binding") != request["executor_binding"]
        or work_request.get("model_identity") != request["model_identity"]
    ):
        raise BehavioralOutcomeError("bound Work request identity mismatch")
    payload = work_request.get("payload")
    unit = payload.get("execution_unit") if isinstance(payload, Mapping) else None
    expected_binding = behavioral_child_binding(request)
    if not isinstance(unit, Mapping) or unit.get("behavioral_outcome") != expected_binding:
        raise BehavioralOutcomeError("child Work unit lacks the exact precommitted binding")
    requested = _timestamp(request.get("requested_at"), "requested_at")
    created = _timestamp(work_request.get("created_at"), "Work request created_at")
    received = _timestamp(work_response.get("received_at"), "Work response received_at")
    observed = _timestamp(observed_at, "observed_at")
    if created < requested or received < created or observed < received:
        raise BehavioralOutcomeError("behavioral outcome timestamps are out of order")
    answer = work["output"].get("result", {}).get("behavioral_answer")
    _walk_public(answer)
    answer_bytes = _canonical_bytes(answer)
    if len(answer_bytes) > request["budget"]["max_response_bytes"]:
        raise BehavioralOutcomeError("behavioral answer exceeds its byte budget")
    answer_digest = store.stable_digest(answer, length=64)
    response = {
        "schema_version": 1,
        "record_type": "behavioral_outcome_response",
        "run_id": run_id,
        "outcome_id": outcome_id,
        "request_digest": request["request_digest"],
        "executor_binding": request["executor_binding"],
        "model_identity": request["model_identity"],
        "work_invocation_id": work_invocation_id,
        "work_request_digest": work_request["request_digest"],
        "work_response_digest": work_response["response_digest"],
        "work_output_digest": work_response["output_digest"],
        "answer": deepcopy(answer),
        "answer_digest": answer_digest,
        "received_at": work_response["received_at"],
    }
    response["response_record_digest"] = store.stable_digest(response, length=64)
    expected = request["rubric"]["expected_answer"]
    score = 1.0 if answer_bytes == _canonical_bytes(expected) else 0.0
    judgment = {
        "schema_version": 1,
        "record_type": "behavioral_outcome_judgment",
        "run_id": run_id,
        "outcome_id": outcome_id,
        "request_digest": request["request_digest"],
        "response_record_digest": response["response_record_digest"],
        "rubric_digest": request["rubric_digest"],
        "judge_kind": request["rubric"]["judge_kind"],
        "judge_version": request["rubric"]["judge_version"],
        "success_threshold": request["rubric"]["success_threshold"],
        "expected_answer_digest": store.stable_digest(expected, length=64),
        "actual_answer_digest": answer_digest,
        "score": score,
        "passed": score >= request["rubric"]["success_threshold"],
        "judged_at": observed_at,
    }
    judgment["judgment_digest"] = store.stable_digest(judgment, length=64)
    receipt = {
        "schema_version": 1,
        "record_type": "behavioral_outcome_receipt",
        "run_id": run_id,
        "outcome_id": outcome_id,
        "request_digest": request["request_digest"],
        "task_digest": request["task_digest"],
        "rubric_digest": request["rubric_digest"],
        "response_record_digest": response["response_record_digest"],
        "judgment_digest": judgment["judgment_digest"],
        "work_invocation": {
            "invocation_id": work_invocation_id,
            "request_digest": work_request["request_digest"],
            "response_digest": work_response["response_digest"],
            "output_digest": work_response["output_digest"],
        },
        "authority": receipt_authority,
        "timestamps": {
            "task_precommitted_at": request["requested_at"],
            "work_request_created_at": work_request["created_at"],
            "work_response_received_at": work_response["received_at"],
            "judged_at": observed_at,
        },
        "judgment": {
            "judge_kind": judgment["judge_kind"],
            "judge_version": judgment["judge_version"],
            "score": judgment["score"],
            "success_threshold": judgment["success_threshold"],
            "passed": judgment["passed"],
        },
        "claim_scope": CLAIM_SCOPE,
        "evidence_class": "internal_provider_behavioral_observation",
        "unknowns": [
            "model identity is not independently attested",
            "task is one sandboxed deterministic case and does not establish breadth",
            "provider and judge are operated inside the project boundary",
        ],
        "observed_at": observed_at,
    }
    receipt["receipt_digest"] = store.stable_digest(receipt, length=64)
    # Validate the entire transition before the first write. Existing immutable
    # records are also checked here so a rejected replay cannot alter the ledger.
    for name, record, digest_field, record_type in (
        ("response.json", response, "response_record_digest", "behavioral_outcome_response"),
        ("judgment.json", judgment, "judgment_digest", "behavioral_outcome_judgment"),
        ("receipt.json", receipt, "receipt_digest", "behavioral_outcome_receipt"),
    ):
        path = directory / name
        if path.exists():
            existing = _read_record(path, record_type, digest_field, store)
            if existing != record:
                raise BehavioralOutcomeError(f"immutable {record_type} conflict")
    ledger = _updated_ledger(root, run_id, receipt, store)
    _write_once(directory / "response.json", response, "response_record_digest")
    _write_once(directory / "judgment.json", judgment, "judgment_digest")
    _write_once(directory / "receipt.json", receipt, "receipt_digest")
    ledger_path = _ledger_path(root, run_id)
    if ledger_path.exists():
        current = _read_record(
            ledger_path, "behavioral_outcome_ledger", "ledger_digest", store
        )
        if current != ledger:
            store.atomic_json(ledger_path, ledger)
    else:
        store.atomic_json(ledger_path, ledger)
    return {
        "request": deepcopy(request),
        "response": response,
        "judgment": judgment,
        "receipt": receipt,
        "ledger": ledger,
    }


def verify_behavioral_outcome(root: Path, *, run_id: str, outcome_id: str) -> dict[str, Any]:
    """Read one completed outcome and verify every immutable cross-binding."""

    root = root.resolve()
    store, request = _load_request(root, run_id, outcome_id)
    directory = _directory(root, run_id, outcome_id)
    response = _read_record(
        directory / "response.json",
        "behavioral_outcome_response",
        "response_record_digest",
        store,
    )
    judgment = _read_record(
        directory / "judgment.json",
        "behavioral_outcome_judgment",
        "judgment_digest",
        store,
    )
    receipt = _read_record(
        directory / "receipt.json",
        "behavioral_outcome_receipt",
        "receipt_digest",
        store,
    )
    if response.get("request_digest") != request["request_digest"]:
        raise BehavioralOutcomeError("response request cross-binding mismatch")
    if judgment.get("request_digest") != request["request_digest"]:
        raise BehavioralOutcomeError("judgment request cross-binding mismatch")
    if judgment.get("response_record_digest") != response["response_record_digest"]:
        raise BehavioralOutcomeError("judgment response cross-binding mismatch")
    if receipt.get("request_digest") != request["request_digest"]:
        raise BehavioralOutcomeError("receipt request cross-binding mismatch")
    if receipt.get("response_record_digest") != response["response_record_digest"]:
        raise BehavioralOutcomeError("receipt response cross-binding mismatch")
    if receipt.get("judgment_digest") != judgment["judgment_digest"]:
        raise BehavioralOutcomeError("receipt judgment cross-binding mismatch")
    if receipt.get("claim_scope") != CLAIM_SCOPE:
        raise BehavioralOutcomeError("receipt claim scope mismatch")
    ledger = _read_record(
        _ledger_path(root, run_id),
        "behavioral_outcome_ledger",
        "ledger_digest",
        store,
    )
    matches = [
        item for item in ledger.get("receipts", []) if item.get("outcome_id") == outcome_id
    ]
    if len(matches) != 1 or matches[0].get("receipt_digest") != receipt["receipt_digest"]:
        raise BehavioralOutcomeError("receipt is not exactly indexed by the ledger")
    return {
        "request": request,
        "response": response,
        "judgment": judgment,
        "receipt": receipt,
        "ledger": ledger,
    }
