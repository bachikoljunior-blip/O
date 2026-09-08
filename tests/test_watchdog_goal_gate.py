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


def test_completion_marker_without_user_completion_authority_is_recovered() -> None:
    decision = evaluate_watchdog_lease(_completion_state(), now=NOW)

    assert decision.action == "recover_unverified_completion"
    assert decision.safe_to_mutate is True


def test_repository_gate_alone_does_not_stop_recovery() -> None:
    decision = evaluate_watchdog_lease(
        _completion_state("goal_complete"),
        now=NOW,
        verified_goal=True,
    )

    assert decision.action == "recover_unverified_completion"
    assert decision.safe_to_mutate is True


def test_user_objective_or_explicit_stop_can_stop_recovery() -> None:
    objective = evaluate_watchdog_lease(
        _completion_state("goal_complete"),
        now=NOW,
        user_objective_met=True,
    )
    stopped = evaluate_watchdog_lease(
        _completion_state("goal_complete"),
        now=NOW,
        explicit_user_stop=True,
    )

    assert objective.action == "goal_complete"
    assert objective.safe_to_mutate is False
    assert stopped.action == "user_stopped"
    assert stopped.safe_to_mutate is False
