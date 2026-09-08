from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


_EXECUTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,191}$")
_CHECKPOINT_STATUSES = {"checkpointed", "interrupted", "released"}
_FORCED_BOUNDARY_KINDS = {
    "automation_task_run_boundary",
    "platform_process_boundary",
    "provider_runtime_boundary",
    "runtime_budget_exhausted",
}
_EXTERNAL_RECEIPT_SOURCES = {
    "automation_runtime",
    "platform_runtime",
    "provider_runtime",
}


class WorkContinuityGuardError(ValueError):
    """Raised when a proposed continuity transition cannot be interpreted safely."""


@dataclass(frozen=True)
class WorkContinuityDecision:
    allowed: bool
    action: str
    reason: str
    execution_id: str | None
    lease_generation: int | None
    current_status: str | None
    proposed_status: str | None
    safe_work_exists: bool | None
    forced_boundary_verified: bool

    def descriptor(self) -> dict[str, Any]:
        return asdict(self)


def _text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkContinuityGuardError(f"{field} must be a non-empty string")
    return value.strip()


def _status(state: Mapping[str, Any], *, field: str) -> str:
    return _text(state.get("status"), field=f"{field}.status").lower()


def _generation(state: Mapping[str, Any], *, field: str) -> int:
    value = state.get("lease_generation")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise WorkContinuityGuardError(
            f"{field}.lease_generation must be a non-negative integer"
        )
    return value


def _execution_id(state: Mapping[str, Any], *, field: str) -> str:
    value = _text(state.get("execution_id"), field=f"{field}.execution_id")
    if not _EXECUTION_ID.fullmatch(value):
        raise WorkContinuityGuardError(f"{field}.execution_id has an invalid format")
    return value


def _timestamp(value: Any, *, field: str) -> datetime:
    text = _text(value, field=field)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise WorkContinuityGuardError(f"{field} must be valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WorkContinuityGuardError(f"{field} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _now(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise WorkContinuityGuardError("now must be timezone-aware")
        return value.astimezone(timezone.utc)
    return _timestamp(value, field="now")


def _decision(
    *,
    allowed: bool,
    action: str,
    reason: str,
    execution_id: str | None = None,
    lease_generation: int | None = None,
    current_status: str | None = None,
    proposed_status: str | None = None,
    safe_work_exists: bool | None = None,
    forced_boundary_verified: bool = False,
) -> WorkContinuityDecision:
    return WorkContinuityDecision(
        allowed=allowed,
        action=action,
        reason=reason,
        execution_id=execution_id,
        lease_generation=lease_generation,
        current_status=current_status,
        proposed_status=proposed_status,
        safe_work_exists=safe_work_exists,
        forced_boundary_verified=forced_boundary_verified,
    )


def _denied(reason: str, **common: Any) -> WorkContinuityDecision:
    return _decision(
        allowed=False,
        action="continue_running_or_select_next_unit",
        reason=reason,
        **common,
    )


def _continuous_authorization_enabled(authorization: Mapping[str, Any]) -> bool:
    if authorization.get("status") != "active":
        return False
    scope = authorization.get("scope")
    if scope not in {
        "primary_o_and_successor_recovery_execution",
        "primary_o_execution",
    }:
        return False
    rules = authorization.get("authorization")
    if not isinstance(rules, Mapping):
        return False
    required = (
        "primary_execution_may_not_stop_by_discretion_while_safe_executable_work_exists",
        "root_must_select_a_next_safe_falsifiable_unit_when_objective_unmet",
        "checkpoint_is_continuation_not_permission_wait",
    )
    return all(rules.get(key) is True for key in required)


def _verify_forced_boundary_receipt(
    receipt: Mapping[str, Any] | None,
    *,
    termination: Mapping[str, Any],
    execution_id: str,
    lease_generation: int,
    now: datetime,
    max_receipt_age_seconds: int,
    max_future_skew_seconds: int,
) -> tuple[bool, str]:
    if not isinstance(receipt, Mapping):
        return False, "an external runtime boundary receipt is required"
    if receipt.get("schema_version") != 1:
        return False, "forced-boundary receipt schema_version must equal 1"
    receipt_id = receipt.get("receipt_id")
    if not isinstance(receipt_id, str) or not receipt_id.strip():
        return False, "forced-boundary receipt_id is missing"
    if termination.get("forced_boundary_receipt_id") != receipt_id:
        return False, "termination does not bind the supplied forced-boundary receipt"
    kind = receipt.get("kind")
    if kind not in _FORCED_BOUNDARY_KINDS:
        return False, "forced-boundary receipt kind is not an accepted runtime boundary"
    if termination.get("kind") != kind:
        return False, "termination kind does not match the forced-boundary receipt"
    if receipt.get("source") not in _EXTERNAL_RECEIPT_SOURCES:
        return False, "forced-boundary receipt source is not an external runtime"
    if receipt.get("verified") is not True:
        return False, "forced-boundary receipt is not verified"
    if receipt.get("external_to_primary_model") is not True:
        return False, "forced-boundary receipt is not attested as external to the primary model"
    if receipt.get("execution_id") != execution_id:
        return False, "forced-boundary receipt execution_id mismatch"
    if receipt.get("lease_generation") != lease_generation:
        return False, "forced-boundary receipt lease_generation mismatch"
    try:
        observed_at = _timestamp(receipt.get("observed_at"), field="receipt.observed_at")
    except WorkContinuityGuardError as exc:
        return False, str(exc)
    age = (now - observed_at).total_seconds()
    if age < -max_future_skew_seconds:
        return False, "forced-boundary receipt is implausibly far in the future"
    if age > max_receipt_age_seconds:
        return False, "forced-boundary receipt is too old for this transition"
    return True, "external runtime boundary receipt verified"


def _verify_no_safe_work_proof(termination: Mapping[str, Any]) -> tuple[bool, str]:
    if termination.get("kind") != "no_safe_executable_work":
        return False, "safe_work_exists=false requires kind no_safe_executable_work"
    blocking = termination.get("blocking_facts")
    alternatives = termination.get("attempted_alternatives")
    receipts = termination.get("blocking_receipts")
    resume = termination.get("exact_resume_condition")
    if not isinstance(blocking, list) or not blocking or not all(
        isinstance(item, str) and item.strip() for item in blocking
    ):
        return False, "no-safe-work proof requires non-empty blocking_facts"
    if not isinstance(alternatives, list) or not alternatives or not all(
        isinstance(item, str) and item.strip() for item in alternatives
    ):
        return False, "no-safe-work proof requires attempted_alternatives"
    if not isinstance(receipts, list) or not receipts or not all(
        isinstance(item, Mapping)
        and isinstance(item.get("source_ref"), str)
        and item.get("source_ref").strip()
        and item.get("externally_observable") is True
        for item in receipts
    ):
        return False, "no-safe-work proof requires externally observable blocking receipts"
    if not isinstance(resume, Mapping):
        return False, "no-safe-work proof requires a machine-checkable exact_resume_condition"
    if not isinstance(resume.get("kind"), str) or not resume.get("kind", "").strip():
        return False, "exact_resume_condition.kind is required"
    if not isinstance(resume.get("source_ref"), str) or not resume.get(
        "source_ref", ""
    ).strip():
        return False, "exact_resume_condition.source_ref is required"
    return True, "externally bound no-safe-work proof verified"


def evaluate_work_checkpoint_transition(
    current_state: Mapping[str, Any],
    proposed_state: Mapping[str, Any],
    continuous_authorization: Mapping[str, Any],
    *,
    forced_boundary_receipt: Mapping[str, Any] | None = None,
    now: datetime | str | None = None,
    max_receipt_age_seconds: int = 300,
    max_future_skew_seconds: int = 120,
) -> WorkContinuityDecision:
    """Mechanically reject discretionary primary-Work checkpoint transitions.

    A model-authored ``termination.kind=forced_*`` string is not evidence.  When safe work still
    exists, a checkpoint/interruption/release is allowed only with a fresh, execution-bound receipt
    produced by an external runtime.  If no safe work exists, the transition requires externally
    observable blockers, attempted alternatives, and a machine-checkable resume condition.

    The guard does not weaken single-writer, fence, CAS, idempotency, executor-binding, secret, or
    claim-boundary rules.  A denied decision means the current execution must remain running and
    immediately continue the current unit or select another safe falsifiable unit.
    """

    if not isinstance(current_state, Mapping) or not isinstance(proposed_state, Mapping):
        return _denied("current_state and proposed_state must be objects")
    if not isinstance(continuous_authorization, Mapping):
        return _denied("continuous_authorization must be an object")
    if (
        not isinstance(max_receipt_age_seconds, int)
        or isinstance(max_receipt_age_seconds, bool)
        or not 1 <= max_receipt_age_seconds <= 3_600
    ):
        raise WorkContinuityGuardError(
            "max_receipt_age_seconds must be an integer from 1 through 3600"
        )
    if (
        not isinstance(max_future_skew_seconds, int)
        or isinstance(max_future_skew_seconds, bool)
        or not 0 <= max_future_skew_seconds <= 3_600
    ):
        raise WorkContinuityGuardError(
            "max_future_skew_seconds must be an integer from 0 through 3600"
        )

    try:
        current_status = _status(current_state, field="current_state")
        proposed_status = _status(proposed_state, field="proposed_state")
        execution_id = _execution_id(current_state, field="current_state")
        proposed_execution_id = _execution_id(proposed_state, field="proposed_state")
        lease_generation = _generation(current_state, field="current_state")
        proposed_generation = _generation(proposed_state, field="proposed_state")
        now_utc = _now(now)
    except WorkContinuityGuardError as exc:
        return _denied(str(exc))

    common = {
        "execution_id": execution_id,
        "lease_generation": lease_generation,
        "current_status": current_status,
        "proposed_status": proposed_status,
    }
    if not _continuous_authorization_enabled(continuous_authorization):
        return _denied("standing continuous-execution authorization is missing or inactive", **common)
    if current_status != "running":
        return _denied("checkpoint transition must start from a running primary state", **common)
    if proposed_status not in _CHECKPOINT_STATUSES:
        return _denied("proposed status is not a checkpoint/interruption/release transition", **common)
    if proposed_execution_id != execution_id or proposed_generation != lease_generation:
        return _denied("checkpoint transition changed execution_id or lease_generation", **common)
    for field in ("fence_token", "owner_kind"):
        if proposed_state.get(field) != current_state.get(field):
            return _denied(f"checkpoint transition changed {field}", **common)

    termination = proposed_state.get("termination")
    if not isinstance(termination, Mapping):
        return _denied("proposed checkpoint requires a structured termination object", **common)
    if termination.get("normal_completion") is not False:
        return _denied("checkpoint transition must not claim normal completion", **common)
    if termination.get("voluntary") is not False:
        return _denied("voluntary or unspecified checkpoint termination is forbidden", **common)
    safe_work_exists = termination.get("safe_work_exists")
    common["safe_work_exists"] = (
        safe_work_exists if isinstance(safe_work_exists, bool) else None
    )
    if not isinstance(safe_work_exists, bool):
        return _denied("termination.safe_work_exists must be explicit boolean", **common)

    if safe_work_exists:
        verified, reason = _verify_forced_boundary_receipt(
            forced_boundary_receipt,
            termination=termination,
            execution_id=execution_id,
            lease_generation=lease_generation,
            now=now_utc,
            max_receipt_age_seconds=max_receipt_age_seconds,
            max_future_skew_seconds=max_future_skew_seconds,
        )
        if not verified:
            return _denied(
                f"safe work remains; self-declared forced termination is insufficient: {reason}",
                **common,
            )
        return _decision(
            allowed=True,
            action="persist_forced_boundary_checkpoint",
            reason=reason,
            forced_boundary_verified=True,
            **common,
        )

    verified, reason = _verify_no_safe_work_proof(termination)
    if not verified:
        return _denied(reason, **common)
    return _decision(
        allowed=True,
        action="persist_externally_blocked_checkpoint",
        reason=reason,
        **common,
    )


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkContinuityGuardError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise WorkContinuityGuardError(f"{path} must contain a JSON object")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m agi.work_continuity_guard",
        description="Authorize or reject a proposed primary-Work checkpoint transition.",
    )
    parser.add_argument("--current-state", type=Path, required=True)
    parser.add_argument("--proposed-state", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--forced-boundary-receipt", type=Path)
    parser.add_argument("--now")
    args = parser.parse_args()

    receipt = (
        _read_json(args.forced_boundary_receipt)
        if args.forced_boundary_receipt is not None
        else None
    )
    decision = evaluate_work_checkpoint_transition(
        _read_json(args.current_state),
        _read_json(args.proposed_state),
        _read_json(args.authorization),
        forced_boundary_receipt=receipt,
        now=args.now,
    )
    print(json.dumps(decision.descriptor(), ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if decision.allowed else 2)


if __name__ == "__main__":
    main()
