from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from agi.watchdog_lease import evaluate_watchdog_lease


ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_watchdog_state_disables_time_budget_and_suppresses_fresh_duplicate() -> None:
    state = json.loads(
        (ROOT / "agi" / "HOURLY_EXECUTION_STATE.json").read_text(encoding="utf-8")
    )

    assert state["mode"] == "watchdog_repository_only_same_task_chat"
    assert state["lease_mode"] == "watchdog_continuation"
    assert state["time_budget_enabled"] is False
    assert state["soft_stop"] is None
    assert state["hard_stop"] is None
    assert state["status"] == "running"

    heartbeat = datetime.fromisoformat(state["heartbeat_at"])
    decision = evaluate_watchdog_lease(
        state,
        now=heartbeat + timedelta(seconds=60),
    )
    assert decision.action == "suppress_duplicate"
    assert decision.safe_to_mutate is False
    assert decision.policy_compliant is True
