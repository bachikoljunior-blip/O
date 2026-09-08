from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .work_session import (
    WorkSessionError,
    verified_work_invocation,
    verified_work_request,
)


_INVOCATION_ID = re.compile(r"^invoke-[0-9a-f]{24}$")
_ANSWERED_CLAIM = re.compile(
    r"^(invoke-[0-9a-f]{24})(?:\s+\([^()\r\n]+\))?$"
)
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SAFE_UNIT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_DEFAULT_STATE_PATH = Path("agi/WORK_EXECUTION_STATE.json")


def _issue(
    issues: list[dict[str, Any]],
    code: str,
    path: str,
    message: str,
    *,
    invocation_id: str | None = None,
) -> None:
    item: dict[str, Any] = {"code": code, "path": path, "message": message}
    if invocation_id is not None:
        item["invocation_id"] = invocation_id
    issues.append(item)


def _safe_reference(root: Path, reference: str) -> Path | None:
    candidate = Path(reference)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    resolved = (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        return None
    return resolved


def _load_state(
    root: Path,
    *,
    state_path: Path,
    state: Mapping[str, Any] | None,
    issues: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    if state is not None:
        return deepcopy(dict(state)), "<provided-state>"
    path = state_path if state_path.is_absolute() else root / state_path
    state_ref = path.resolve().as_posix()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _issue(issues, "STATE_MISSING", "state", f"state file does not exist: {path}")
        return None, state_ref
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _issue(issues, "STATE_MALFORMED", "state", f"state file is unreadable or invalid JSON: {exc}")
        return None, state_ref
    if not isinstance(raw, dict):
        _issue(issues, "STATE_MALFORMED", "state", "state must be a JSON object")
        return None, state_ref
    return raw, state_ref


def _check_request_binding(
    request: Mapping[str, Any],
    *,
    state: Mapping[str, Any],
    run_id: str | None,
    path: str,
    invocation_id: str,
    issues: list[dict[str, Any]],
) -> None:
    if run_id is not None and request.get("run_id") != run_id:
        _issue(
            issues,
            "WORK_RUN_BINDING_MISMATCH",
            path,
            f"request run_id does not match primary native run {run_id}",
            invocation_id=invocation_id,
        )
    primary = state.get("primary_native_run")
    if not isinstance(primary, Mapping):
        return
    for field in ("executor_binding", "model_identity"):
        expected = primary.get(field)
        if isinstance(expected, str) and request.get(field) != expected:
            _issue(
                issues,
                f"WORK_{field.upper()}_MISMATCH",
                path,
                f"request {field} does not match primary native run",
                invocation_id=invocation_id,
            )


def _verify_completed(
    root: Path,
    invocation_id: str,
    *,
    state: Mapping[str, Any],
    run_id: str | None,
    path: str,
    issues: list[dict[str, Any]],
    verified: list[dict[str, Any]],
) -> None:
    try:
        record = verified_work_invocation(root, invocation_id)
    except WorkSessionError as exc:
        _issue(
            issues,
            "COMPLETED_WORK_INVOCATION_INVALID",
            path,
            str(exc),
            invocation_id=invocation_id,
        )
        return
    request = record["request"]
    _check_request_binding(
        request,
        state=state,
        run_id=run_id,
        path=path,
        invocation_id=invocation_id,
        issues=issues,
    )
    verified.append(
        {
            "invocation_id": invocation_id,
            "kind": "completed",
            "component": request.get("component"),
            "request_digest": request.get("request_digest"),
            "response_digest": record["response"].get("response_digest"),
        }
    )


def verify_work_checkpoint_integrity(
    root: Path,
    *,
    state_path: Path = _DEFAULT_STATE_PATH,
    state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify a durable Work checkpoint without mutating repository records.

    The report is intentionally scoped to the supplied state and checked
    repository snapshot.  A failure says nothing about other checkpoints,
    mechanisms, configurations, or agent families.
    """

    root = root.resolve()
    issues: list[dict[str, Any]] = []
    verified: list[dict[str, Any]] = []
    raw_state, state_ref = _load_state(
        root,
        state_path=state_path,
        state=state,
        issues=issues,
    )
    if raw_state is None:
        return {
            "schema_version": 1,
            "valid": False,
            "state_ref": state_ref,
            "verified_references": [],
            "issues": issues,
            "claim_boundary": "this supplied checkpoint and checked repository snapshot only",
        }

    exact = raw_state.get("exact_continuation")
    primary = raw_state.get("primary_native_run")
    if not isinstance(exact, Mapping):
        _issue(
            issues,
            "EXACT_CONTINUATION_MALFORMED",
            "exact_continuation",
            "exact_continuation must be an object",
        )
        exact = {}
    if not isinstance(primary, Mapping):
        _issue(
            issues,
            "PRIMARY_NATIVE_RUN_MALFORMED",
            "primary_native_run",
            "primary_native_run must be an object",
        )
        primary = {}

    run_id = primary.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        _issue(
            issues,
            "PRIMARY_RUN_ID_MALFORMED",
            "primary_native_run.run_id",
            "primary native run_id must be a non-empty string",
        )
        run_id = None
    elif raw_state.get("active_run_id") != run_id:
        _issue(
            issues,
            "ACTIVE_RUN_MISMATCH",
            "active_run_id",
            "active_run_id does not match primary_native_run.run_id",
        )

    snapshot_ref = exact.get("run_snapshot_ref")
    if not isinstance(snapshot_ref, str) or not snapshot_ref:
        _issue(
            issues,
            "SNAPSHOT_REF_MALFORMED",
            "exact_continuation.run_snapshot_ref",
            "run_snapshot_ref must be a non-empty relative path",
        )
    else:
        snapshot_path = _safe_reference(root, snapshot_ref)
        if snapshot_path is None:
            _issue(
                issues,
                "SNAPSHOT_REF_UNSAFE",
                "exact_continuation.run_snapshot_ref",
                "run_snapshot_ref escapes the repository",
            )
        else:
            try:
                snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                _issue(
                    issues,
                    "SNAPSHOT_MISSING",
                    "exact_continuation.run_snapshot_ref",
                    f"referenced snapshot does not exist: {snapshot_ref}",
                )
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                _issue(
                    issues,
                    "SNAPSHOT_MALFORMED",
                    "exact_continuation.run_snapshot_ref",
                    f"referenced snapshot is unreadable or invalid JSON: {exc}",
                )
            else:
                if not isinstance(snapshot, dict):
                    _issue(
                        issues,
                        "SNAPSHOT_MALFORMED",
                        "exact_continuation.run_snapshot_ref",
                        "referenced snapshot must be a JSON object",
                    )
                elif run_id is not None and snapshot.get("run_id") != run_id:
                    _issue(
                        issues,
                        "SNAPSHOT_RUN_MISMATCH",
                        "exact_continuation.run_snapshot_ref",
                        "snapshot run_id does not match primary native run",
                    )
                else:
                    verified.append(
                        {
                            "kind": "run_snapshot",
                            "path": snapshot_ref,
                            "run_id": snapshot.get("run_id"),
                            "revision": snapshot.get("revision"),
                            "phase": snapshot.get("phase"),
                        }
                    )
                    if snapshot.get("phase") == "unit_pending":
                        unit_id = snapshot.get("current_unit")
                        unit_path_key = "run_snapshot.current_unit"
                        if not isinstance(unit_id, str) or not _SAFE_UNIT_ID.fullmatch(unit_id):
                            _issue(
                                issues,
                                "EXECUTION_UNIT_ID_MALFORMED",
                                unit_path_key,
                                "unit_pending snapshot current_unit must be a safe unit id",
                            )
                        elif run_id is None:
                            _issue(
                                issues,
                                "EXECUTION_UNIT_RUN_UNKNOWN",
                                unit_path_key,
                                "cannot resolve current execution unit without primary run_id",
                            )
                        else:
                            unit_ref = (
                                f".continual/runs/{run_id}/execution-units/"
                                f"{unit_id}.json"
                            )
                            unit_path = root / unit_ref
                            try:
                                unit = json.loads(unit_path.read_text(encoding="utf-8"))
                            except FileNotFoundError:
                                _issue(
                                    issues,
                                    "EXECUTION_UNIT_MISSING",
                                    unit_path_key,
                                    f"referenced execution unit does not exist: {unit_ref}",
                                )
                            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                                _issue(
                                    issues,
                                    "EXECUTION_UNIT_MALFORMED",
                                    unit_path_key,
                                    f"referenced execution unit is unreadable or invalid JSON: {exc}",
                                )
                            else:
                                if not isinstance(unit, dict) or unit.get("unit_id") != unit_id:
                                    _issue(
                                        issues,
                                        "EXECUTION_UNIT_ID_MISMATCH",
                                        unit_path_key,
                                        "execution unit identity does not match snapshot current_unit",
                                    )
                                else:
                                    verified.append(
                                        {
                                            "kind": "execution_unit",
                                            "path": unit_ref,
                                            "unit_id": unit_id,
                                            "component": unit.get("component"),
                                        }
                                    )

    if exact.get("snapshot_branch") != "main":
        _issue(
            issues,
            "SNAPSHOT_BRANCH_NOT_MAIN",
            "exact_continuation.snapshot_branch",
            "durable continuation must name main",
        )
    head = exact.get("snapshot_head_sha")
    if not isinstance(head, str) or not _COMMIT_SHA.fullmatch(head):
        _issue(
            issues,
            "SNAPSHOT_HEAD_MALFORMED",
            "exact_continuation.snapshot_head_sha",
            "snapshot_head_sha must be a full lowercase commit SHA",
        )

    pending_id = exact.get("pending_work_invocation_id")
    pending_ref = exact.get("pending_request_ref")
    if pending_id is None and pending_ref is not None:
        _issue(
            issues,
            "PENDING_WORK_PAIR_MISMATCH",
            "exact_continuation.pending_request_ref",
            "pending_request_ref is set while pending_work_invocation_id is null",
        )
    elif pending_id is not None:
        if not isinstance(pending_id, str) or not _INVOCATION_ID.fullmatch(pending_id):
            _issue(
                issues,
                "PENDING_WORK_ID_MALFORMED",
                "exact_continuation.pending_work_invocation_id",
                "pending Work invocation_id is malformed",
            )
        else:
            expected_ref = (
                f".continual/work-model/invocations/{pending_id}/request.json"
            )
            if pending_ref != expected_ref:
                _issue(
                    issues,
                    "PENDING_REQUEST_REF_MISMATCH",
                    "exact_continuation.pending_request_ref",
                    f"pending request reference must equal {expected_ref}",
                    invocation_id=pending_id,
                )
            try:
                request = verified_work_request(root, pending_id)
            except WorkSessionError as exc:
                _issue(
                    issues,
                    "PENDING_WORK_REQUEST_INVALID",
                    "exact_continuation.pending_work_invocation_id",
                    str(exc),
                    invocation_id=pending_id,
                )
            else:
                _check_request_binding(
                    request,
                    state=raw_state,
                    run_id=run_id,
                    path="exact_continuation.pending_work_invocation_id",
                    invocation_id=pending_id,
                    issues=issues,
                )
                verified.append(
                    {
                        "invocation_id": pending_id,
                        "kind": "pending_work_request",
                        "component": request.get("component"),
                        "request_digest": request.get("request_digest"),
                    }
                )

    native_id = exact.get("pending_native_invocation_id")
    if native_id is not None:
        path = "exact_continuation.pending_native_invocation_id"
        if not isinstance(native_id, str) or not _INVOCATION_ID.fullmatch(native_id):
            _issue(issues, "PENDING_NATIVE_ID_MALFORMED", path, "pending native invocation_id is malformed")
        elif run_id is None:
            _issue(issues, "PENDING_NATIVE_RUN_UNKNOWN", path, "cannot resolve pending native invocation without run_id", invocation_id=native_id)
        else:
            native_ref = f".continual/runs/{run_id}/invocations/{native_id}.json"
            native_path = root / native_ref
            try:
                native = json.loads(native_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                _issue(issues, "PENDING_NATIVE_INVOCATION_MISSING", path, f"pending native invocation does not exist: {native_ref}", invocation_id=native_id)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                _issue(issues, "PENDING_NATIVE_INVOCATION_MALFORMED", path, f"pending native invocation is unreadable or invalid JSON: {exc}", invocation_id=native_id)
            else:
                if not isinstance(native, dict) or native.get("invocation_id") != native_id:
                    _issue(issues, "PENDING_NATIVE_INVOCATION_MALFORMED", path, "pending native invocation identity is inconsistent", invocation_id=native_id)
                elif native.get("status") != "awaiting_work_model":
                    _issue(issues, "PENDING_NATIVE_STATUS_MISMATCH", path, "pending native invocation is not awaiting_work_model", invocation_id=native_id)
                elif pending_id is not None and native.get("work_invocation_id") != pending_id:
                    _issue(issues, "PENDING_NATIVE_WORK_MISMATCH", path, "native invocation does not bind the pending Work invocation", invocation_id=native_id)
                elif pending_ref is not None and native.get("work_request_ref") != pending_ref:
                    _issue(issues, "PENDING_NATIVE_REQUEST_REF_MISMATCH", path, "native invocation does not bind the pending request reference", invocation_id=native_id)
                else:
                    verified.append({"kind": "pending_native_invocation", "invocation_id": native_id, "path": native_ref})

    completed_claims: list[tuple[str, str]] = []
    completed_id = exact.get("completed_work_invocation_id")
    if completed_id is not None:
        if not isinstance(completed_id, str) or not _INVOCATION_ID.fullmatch(completed_id):
            _issue(
                issues,
                "COMPLETED_WORK_ID_MALFORMED",
                "exact_continuation.completed_work_invocation_id",
                "completed Work invocation_id is malformed",
            )
        else:
            completed_claims.append(
                (completed_id, "exact_continuation.completed_work_invocation_id")
            )

    answered = primary.get("answered_invocations")
    if not isinstance(answered, list):
        _issue(
            issues,
            "ANSWERED_INVOCATIONS_MALFORMED",
            "primary_native_run.answered_invocations",
            "answered_invocations must be an array",
        )
    else:
        for index, claim in enumerate(answered):
            path = f"primary_native_run.answered_invocations[{index}]"
            match = _ANSWERED_CLAIM.fullmatch(claim) if isinstance(claim, str) else None
            if match is None:
                _issue(
                    issues,
                    "ANSWERED_INVOCATION_CLAIM_MALFORMED",
                    path,
                    "claim must contain exactly one invocation_id and an optional parenthesized label",
                )
            else:
                completed_claims.append((match.group(1), path))

    checked_completed: set[str] = set()
    for invocation_id, path in completed_claims:
        if invocation_id in checked_completed:
            continue
        checked_completed.add(invocation_id)
        _verify_completed(
            root,
            invocation_id,
            state=raw_state,
            run_id=run_id,
            path=path,
            issues=issues,
            verified=verified,
        )

    return {
        "schema_version": 1,
        "valid": not issues,
        "state_ref": state_ref,
        "run_id": run_id,
        "verified_references": verified,
        "issues": issues,
        "claim_boundary": "this supplied checkpoint and checked repository snapshot only",
    }
