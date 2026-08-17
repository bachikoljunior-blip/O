from __future__ import annotations

from datetime import datetime, timezone

from agi.watchdog_lease import evaluate_watchdog_lease


NOW = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)


def _completion_state(status: str = "verified_agi") -> dict:
    return {
        "status": status,
        "time_budget_enabled": False,
        "soft_stop": None,
        "hard_stop": None,
    }


def test_completion_marker_without_external_gate_proof_is_recovered_not_trusted() -> None:
    decision = evaluate_watchdog_lease(_completion_state(), now=NOW)

    assert decision.action == "recover_unverified_completion"
    assert decision.safe_to_mutate is True


def test_only_separately_verified_external_goal_can_stop_recovery() -> None:
    decision = evaluate_watchdog_lease(
        _completion_state("goal_complete"),
        now=NOW,
        verified_goal=True,
    )

    assert decision.action == "goal_complete"
    assert decision.safe_to_mutate is False
