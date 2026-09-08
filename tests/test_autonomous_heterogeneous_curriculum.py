from __future__ import annotations

import json
from pathlib import Path

from agi.autonomous_heterogeneous_curriculum import (
    run_autonomous_heterogeneous_curriculum,
)


def test_closes_two_distinct_heterogeneous_gaps_across_fresh_sessions(
    runtime_repo: Path,
) -> None:
    report = run_autonomous_heterogeneous_curriculum(
        runtime_repo,
        "autonomous-heterogeneous-curriculum-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "autonomous-heterogeneous-curriculum-v1"
    assert report["round1_initial_unsupported_count"] >= 2
    assert report["round1_remaining_unsupported_count"] >= 1
    assert report["round2_initial_unsupported_count"] == report[
        "round1_remaining_unsupported_count"
    ]
    assert set(report["round2_initial_unsupported_goals"]) == set(
        report["round1_remaining_unsupported_goals"]
    )
    assert report["round2_selected_goal"] in report[
        "round2_initial_unsupported_goals"
    ]
    assert report["round2_selected_from_unsupported_only"] is True
    assert report["round2_negative_evidence_retained"] is True
    assert report["selected_goals_distinct"] is True
    assert report["selected_candidate_ids_distinct"] is True
    assert report["caller_supplied_candidate_ids"] is False
    assert report["final_unsupported_count"] < report[
        "round2_initial_unsupported_count"
    ]
    assert report["unsupported_count_strictly_decreased"] is True
    assert report["round1_selected_goal"] not in report["final_unsupported_goals"]
    assert report["round2_selected_goal"] not in report["final_unsupported_goals"]
    assert {item["goal"] for item in report["retained_challenges"]} == {
        report["round1_selected_goal"],
        report["round2_selected_goal"],
    }
    assert all(item["fresh_engine_run"] for item in report["retained_challenges"])
    assert report["protected_state_unchanged"] is True
    assert report["protected_trials_unchanged"] is True
    assert report["all_state_unchanged_after_fresh_replay"] is True
    assert report["all_trials_unchanged_after_fresh_replay"] is True
    assert report["live_model_invocation_required"] is False

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "autonomous-heterogeneous-curriculum"
    )
    files = list(evidence_dir.glob("curriculum-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["round2_selected_goal"] == report["round2_selected_goal"]
    assert "does not establish AGI" in persisted["claim_boundary"]
