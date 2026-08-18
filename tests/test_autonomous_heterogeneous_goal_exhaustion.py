from __future__ import annotations

import json
from pathlib import Path

from agi.autonomous_heterogeneous_goal_exhaustion import (
    run_autonomous_heterogeneous_goal_exhaustion,
)


def test_exhausts_bounded_heterogeneous_goal_set_without_forgetting(
    runtime_repo: Path,
) -> None:
    report = run_autonomous_heterogeneous_goal_exhaustion(
        runtime_repo,
        "autonomous-heterogeneous-exhaustion-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "autonomous-heterogeneous-goal-exhaustion-v1"
    assert report["caller_supplied_candidate_ids"] is False
    assert report["autonomous_round_count"] >= 2
    assert report["selected_goals_distinct"] is True
    assert report["selected_candidate_ids_distinct"] is True
    assert report["bounded_goal_set_exhausted"] is True
    assert report["final_unsupported_count"] == 0
    assert report["final_unsupported_goals"] == []
    assert len(report["retained_challenges"]) == len(
        report["autonomously_selected_goals"]
    )
    assert {item["goal"] for item in report["retained_challenges"]} == set(
        report["autonomously_selected_goals"]
    )
    assert all(item["fresh_engine_run"] for item in report["retained_challenges"])
    assert report["protected_state_unchanged"] is True
    assert report["protected_trials_unchanged"] is True
    assert report["all_state_unchanged_after_fresh_replay"] is True
    assert report["all_trials_unchanged_after_fresh_replay"] is True
    assert report["live_model_invocation_required"] is False

    if report["pre_exhaustion_unsupported_count"]:
        assert report["additional_round_executed"] is True
        assert report["additional_selected_goal"] in report[
            "pre_exhaustion_unsupported_goals"
        ]
        assert report["additional_negative_evidence_retained"] is True
        assert report["additional_transactional_commit"]

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "autonomous-heterogeneous-goal-exhaustion"
    )
    files = list(evidence_dir.glob("exhaustion-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["bounded_goal_set_exhausted"] is True
    assert "do not establish AGI" in persisted["claim_boundary"]
