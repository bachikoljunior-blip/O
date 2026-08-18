from __future__ import annotations

import json
from pathlib import Path

from agi.iterative_heterogeneous_goal_curriculum import (
    run_iterative_heterogeneous_goal_curriculum,
)


def test_repeatedly_selects_real_gaps_and_retains_all_heterogeneous_gains(
    runtime_repo: Path,
) -> None:
    report = run_iterative_heterogeneous_goal_curriculum(
        runtime_repo,
        "iterative-heterogeneous-goal-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "iterative-heterogeneous-goal-curriculum-v1"
    assert report["initial_unsupported_count"] >= 2
    assert report["round_count"] >= 2
    assert report["round_count"] <= 3
    assert len(report["rounds"]) == report["round_count"]
    assert len(report["acquired_goal_candidate_ids"]) == report["round_count"]
    assert len(set(report["acquired_goal_candidate_ids"].values())) == report["round_count"]
    assert report["all_rounds_selected_from_observed_gaps"] is True
    assert report["all_rounds_reduced_gap_count"] is True
    assert report["all_negative_evidence_retained"] is True

    previous_after: set[str] | None = None
    for index, round_item in enumerate(report["rounds"]):
        assert round_item["round_index"] == index
        assert round_item["selected_goal"] in round_item["unsupported_before"]
        assert round_item["selected_goal"] not in round_item["unsupported_after"]
        assert len(round_item["unsupported_after"]) < len(round_item["unsupported_before"])
        assert round_item["negative_evidence_retained"] is True
        if previous_after is not None:
            assert set(round_item["unsupported_before"]) == previous_after
        previous_after = set(round_item["unsupported_after"])

    assert report["remaining_unsupported_goals"] == []
    assert report["all_bounded_goals_supported_after_curriculum"] is True
    final_by_goal = {
        item["goal"]: item for item in report["final_classification"]
    }
    for goal_name, candidate_id in report["acquired_goal_candidate_ids"].items():
        assert final_by_goal[goal_name]["supported"] is True
        assert final_by_goal[goal_name]["candidate_ids_supplied_by_caller"] is False
        assert final_by_goal[goal_name]["selected_candidate_ids"] == [candidate_id]
        assert final_by_goal[goal_name]["selected_chain_depth"] == 1

    assert len(report["final_challenge_records"]) == report["round_count"]
    assert report["fresh_engine_runs_unique"] is True
    assert report["final_state_unchanged"] is True
    assert report["final_trials_unchanged"] is True
    assert report["source_state_unchanged"] is True
    assert report["source_trials_unchanged"] is True
    assert report["base_candidates_preserved"] is True
    assert report["prior_runs_copied"] is False
    assert report["prior_episodes_copied"] is False
    assert report["prior_evidence_copied"] is False
    assert report["caller_supplied_candidate_ids"] is False
    assert report["live_model_invocation_required"] is False

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "iterative-heterogeneous-goal-curriculum"
    )
    files = list(evidence_dir.glob("curriculum-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["round_count"] == report["round_count"]
    assert "do not establish" in persisted["claim_boundary"]
