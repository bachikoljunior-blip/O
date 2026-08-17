from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agi.watchdog_lease import WatchdogLeaseError, evaluate_watchdog_lease


NOW = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)


def _running(*, age_seconds: int, stale_after_seconds: int = 900) -> dict:
    heartbeat = NOW - timedelta(seconds=age_seconds)
    return {
        "status": "running",
        "heartbeat_at": heartbeat.isoformat(),
        "stale_after_seconds": stale_after_seconds,
        "time_budget_enabled": False,
        "soft_stop": None,
        "hard_stop": None,
        "derived_soft_stop": "2026-08-17T16:50:00+09:00",
        "derived_hard_stop": "2026-08-17T16:55:00+09:00",
    }


def test_fresh_heartbeat_suppresses_duplicate_even_after_audit_hard_stop() -> None:
    state = _running(age_seconds=60)
    state["derived_hard_stop"] = "2026-08-17T00:00:00+09:00"

    decision = evaluate_watchdog_lease(state, now=NOW)

    assert decision.action == "suppress_duplicate"
    assert decision.safe_to_mutate is False
    assert decision.policy_compliant is True
    assert decision.heartbeat_age_seconds == 60


def test_exact_stale_threshold_is_still_owned_but_older_heartbeat_is_recoverable() -> None:
    exact = evaluate_watchdog_lease(_running(age_seconds=900), now=NOW)
    stale = evaluate_watchdog_lease(_running(age_seconds=901), now=NOW)

    assert exact.action == "suppress_duplicate"
    assert exact.safe_to_mutate is False
    assert stale.action == "recover_stale"
    assert stale.safe_to_mutate is True


def test_small_future_clock_skew_is_tolerated_but_large_future_heartbeat_fails_closed() -> None:
    small_future = _running(age_seconds=-60)
    large_future = _running(age_seconds=-121)

    tolerated = evaluate_watchdog_lease(small_future, now=NOW)
    rejected = evaluate_watchdog_lease(large_future, now=NOW)

    assert tolerated.action == "suppress_duplicate"
    assert tolerated.safe_to_mutate is False
    assert rejected.action == "unsafe_state"
    assert rejected.safe_to_mutate is False


def test_malformed_running_state_never_becomes_permission_to_race() -> None:
    state = _running(age_seconds=1_000)
    state["heartbeat_at"] = "not-a-timestamp"

    decision = evaluate_watchdog_lease(state, now=NOW)

    assert decision.action == "unsafe_state"
    assert decision.safe_to_mutate is False


def test_released_or_interrupted_lease_can_be_recovered_without_clock_boundary_logic() -> None:
    for status in ("released", "interrupted", "checkpointed"):
        state = {
            "status": status,
            "time_budget_enabled": False,
            "soft_stop": None,
            "hard_stop": None,
        }
        decision = evaluate_watchdog_lease(state, now=NOW)
        assert decision.action == "recover_released"
        assert decision.safe_to_mutate is True


def test_time_box_policy_mismatch_is_exposed_without_overriding_concurrency_safety() -> None:
    fresh = _running(age_seconds=30)
    fresh["time_budget_enabled"] = True
    fresh["soft_stop"] = "2026-08-17T07:50:00+00:00"
    fresh["hard_stop"] = "2026-08-17T07:55:00+00:00"

    decision = evaluate_watchdog_lease(fresh, now=NOW)

    assert decision.action == "suppress_duplicate"
    assert decision.safe_to_mutate is False
    assert decision.policy_compliant is False


def test_invalid_now_or_stale_threshold_configuration_fails_closed() -> None:
    with pytest.raises(WatchdogLeaseError, match="timezone-aware"):
        evaluate_watchdog_lease(_running(age_seconds=30), now=datetime(2026, 8, 17, 8, 0))

    invalid = _running(age_seconds=30)
    invalid["stale_after_seconds"] = 10
    decision = evaluate_watchdog_lease(invalid, now=NOW)
    assert decision.action == "unsafe_state"
    assert decision.safe_to_mutate is False
