from __future__ import annotations

from agi.longhorizon import (
    LongHorizonAction,
    ReferenceLongHorizonAgent,
    run_long_horizon,
)


def test_reference_long_horizon_requires_restart_rollback_and_retention():
    creations = 0

    def factory():
        nonlocal creations
        creations += 1
        return ReferenceLongHorizonAgent()

    report = run_long_horizon(factory)
    assert report.passed is True
    assert report.injected_failures == 1
    assert report.restarts >= 1
    assert report.rollback_verified is True
    assert report.retention_verified is True
    assert report.protected_regression_verified is True
    assert creations >= 2
    assert "retain" in report.completed_phases
    assert "regression" in report.completed_phases


def test_agent_that_refuses_rollback_cannot_pass():
    class RefuseRollback(ReferenceLongHorizonAgent):
        def act(self, observation):
            if observation.failure:
                return LongHorizonAction("finish")
            return super().act(observation)

    report = run_long_horizon(RefuseRollback, max_turns=10)
    assert report.passed is False
    assert report.injected_failures == 1
    assert report.rollback_verified is False


def test_short_external_budget_is_rejected():
    try:
        run_long_horizon(ReferenceLongHorizonAgent, max_turns=7)
    except ValueError as exc:
        assert "at least 8" in str(exc)
    else:
        raise AssertionError("expected ValueError")
