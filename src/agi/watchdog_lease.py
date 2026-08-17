from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


class WatchdogLeaseError(ValueError):
    pass


@dataclass(frozen=True)
class WatchdogLeaseDecision:
    action: str
    reason: str
    status: str
    heartbeat_at: str | None
    heartbeat_age_seconds: float | None
    stale_after_seconds: int | None
    policy_compliant: bool
    safe_to_mutate: bool

    def descriptor(self) -> dict[str, Any]:
        return asdict(self)


def _parse_timestamp(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise WatchdogLeaseError(f"{field} must be a non-empty ISO-8601 timestamp")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise WatchdogLeaseError(f"{field} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WatchdogLeaseError(f"{field} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _parse_now(now: datetime | str) -> datetime:
    if isinstance(now, datetime):
        if now.tzinfo is None or now.utcoffset() is None:
            raise WatchdogLeaseError("now must be timezone-aware")
        return now.astimezone(timezone.utc)
    return _parse_timestamp(now, field="now")


def _stale_after(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 60 <= value <= 86_400:
        raise WatchdogLeaseError("stale_after_seconds must be an integer from 60 through 86400")
    return value


def _policy_compliant(state: Mapping[str, Any]) -> bool:
    return (
        state.get("time_budget_enabled") is False
        and state.get("soft_stop") is None
        and state.get("hard_stop") is None
    )


def evaluate_watchdog_lease(
    state: Mapping[str, Any],
    *,
    now: datetime | str,
    max_future_heartbeat_skew_seconds: int = 120,
) -> WatchdogLeaseDecision:
    """Classify whether a watchdog may safely recover a repository execution lease.

    The classifier is deliberately conservative. A fresh running heartbeat suppresses all competing
    mutation. A running lease becomes recoverable only when a valid heartbeat is older than the
    persisted stale threshold. Malformed running state never becomes permission to race an execution;
    it fails closed as ``unsafe_state``. Small future clock skew is tolerated, while a heartbeat too far
    in the future also fails closed.

    Hourly boundaries and derived soft/hard timestamps are intentionally ignored for liveness. Policy
    compliance only checks that active watchdog mode disables time budgeting. This keeps concurrency
    safety separate from the audit schedule and prevents a clock boundary from manufacturing a stale
    lease.
    """

    if not isinstance(state, Mapping):
        raise WatchdogLeaseError("watchdog state must be an object")
    if (
        not isinstance(max_future_heartbeat_skew_seconds, int)
        or isinstance(max_future_heartbeat_skew_seconds, bool)
        or not 0 <= max_future_heartbeat_skew_seconds <= 3600
    ):
        raise WatchdogLeaseError(
            "max_future_heartbeat_skew_seconds must be an integer from 0 through 3600"
        )

    now_utc = _parse_now(now)
    status_raw = state.get("status")
    if not isinstance(status_raw, str) or not status_raw.strip():
        return WatchdogLeaseDecision(
            action="unsafe_state",
            reason="missing or invalid lease status",
            status="",
            heartbeat_at=None,
            heartbeat_age_seconds=None,
            stale_after_seconds=None,
            policy_compliant=_policy_compliant(state),
            safe_to_mutate=False,
        )
    status = status_raw.strip().lower()
    policy_compliant = _policy_compliant(state)

    if status == "running":
        try:
            heartbeat = _parse_timestamp(state.get("heartbeat_at"), field="heartbeat_at")
            stale_after = _stale_after(state.get("stale_after_seconds"))
        except WatchdogLeaseError as exc:
            return WatchdogLeaseDecision(
                action="unsafe_state",
                reason=str(exc),
                status=status,
                heartbeat_at=(
                    str(state.get("heartbeat_at"))
                    if state.get("heartbeat_at") is not None
                    else None
                ),
                heartbeat_age_seconds=None,
                stale_after_seconds=(
                    state.get("stale_after_seconds")
                    if isinstance(state.get("stale_after_seconds"), int)
                    and not isinstance(state.get("stale_after_seconds"), bool)
                    else None
                ),
                policy_compliant=policy_compliant,
                safe_to_mutate=False,
            )

        age = (now_utc - heartbeat).total_seconds()
        if age < -max_future_heartbeat_skew_seconds:
            return WatchdogLeaseDecision(
                action="unsafe_state",
                reason="running heartbeat is implausibly far in the future",
                status=status,
                heartbeat_at=heartbeat.isoformat(),
                heartbeat_age_seconds=age,
                stale_after_seconds=stale_after,
                policy_compliant=policy_compliant,
                safe_to_mutate=False,
            )
        if age <= stale_after:
            return WatchdogLeaseDecision(
                action="suppress_duplicate",
                reason="running heartbeat is still inside the stale threshold",
                status=status,
                heartbeat_at=heartbeat.isoformat(),
                heartbeat_age_seconds=age,
                stale_after_seconds=stale_after,
                policy_compliant=policy_compliant,
                safe_to_mutate=False,
            )
        return WatchdogLeaseDecision(
            action="recover_stale",
            reason="running heartbeat is older than the persisted stale threshold",
            status=status,
            heartbeat_at=heartbeat.isoformat(),
            heartbeat_age_seconds=age,
            stale_after_seconds=stale_after,
            policy_compliant=policy_compliant,
            safe_to_mutate=True,
        )

    if status in {"released", "interrupted", "checkpointed"}:
        return WatchdogLeaseDecision(
            action="recover_released",
            reason=f"lease status {status} permits repository-only recovery",
            status=status,
            heartbeat_at=(
                str(state.get("heartbeat_at")) if state.get("heartbeat_at") is not None else None
            ),
            heartbeat_age_seconds=None,
            stale_after_seconds=(
                state.get("stale_after_seconds")
                if isinstance(state.get("stale_after_seconds"), int)
                and not isinstance(state.get("stale_after_seconds"), bool)
                else None
            ),
            policy_compliant=policy_compliant,
            safe_to_mutate=True,
        )

    if status in {"verified_agi", "goal_complete"}:
        return WatchdogLeaseDecision(
            action="goal_complete",
            reason="lease explicitly records verified goal completion",
            status=status,
            heartbeat_at=(
                str(state.get("heartbeat_at")) if state.get("heartbeat_at") is not None else None
            ),
            heartbeat_age_seconds=None,
            stale_after_seconds=(
                state.get("stale_after_seconds")
                if isinstance(state.get("stale_after_seconds"), int)
                and not isinstance(state.get("stale_after_seconds"), bool)
                else None
            ),
            policy_compliant=policy_compliant,
            safe_to_mutate=False,
        )

    return WatchdogLeaseDecision(
        action="unsafe_state",
        reason=f"unrecognized lease status: {status}",
        status=status,
        heartbeat_at=(
            str(state.get("heartbeat_at")) if state.get("heartbeat_at") is not None else None
        ),
        heartbeat_age_seconds=None,
        stale_after_seconds=(
            state.get("stale_after_seconds")
            if isinstance(state.get("stale_after_seconds"), int)
            and not isinstance(state.get("stale_after_seconds"), bool)
            else None
        ),
        policy_compliant=policy_compliant,
        safe_to_mutate=False,
    )
