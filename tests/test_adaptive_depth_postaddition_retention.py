from __future__ import annotations

import json
from pathlib import Path

from agi.adaptive_depth_postaddition_retention import (
    run_adaptive_depth_postaddition_retention,
)


def test_adaptive_route_survives_later_capability_and_fresh_restart(
    runtime_repo: Path,
) -> None:
    report = run_adaptive_depth_postaddition_retention(
        runtime_repo,
        "adaptive-depth-postaddition-retention-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "adaptive-depth-postaddition-retention-v1"
    assert report["caller_supplied_candidate_ids"] is False
    assert report["initial_selected_chain_depth"] >= 2
    assert report["initial_shortest_type_chain_depth"] < report["initial_selected_chain_depth"]
    assert report["later_candidate_input_domain"] == "object"
    assert report["later_candidate_output_domain"] == "boolean"
    assert report["later_candidate_negative_evidence_retained"] is True
    assert report["prior_candidate_state_unchanged_after_addition"] is True
    assert report["prior_trial_state_unchanged_after_addition"] is True

    assert report["replay_selected_candidate_ids"] == report["initial_selected_candidate_ids"]
    assert report["replay_selected_chain_depth"] == report["initial_selected_chain_depth"]
    assert report["retained_route_identity_unchanged"] is True
    assert report["retained_minimal_depth_unchanged"] is True
    assert report["later_candidate_not_selected"] is True
    assert report["later_candidate_id"] not in report["replay_selected_candidate_ids"]
    assert report["replay_depths_attempted"] == list(
        range(1, report["replay_selected_chain_depth"] + 1)
    )
    assert report["replay_search_nodes_visited"] <= report["replay_search_node_budget"]
    assert (
        report["replay_behavior_evaluations"]
        <= report["replay_behavior_evaluation_budget"]
    )

    assert report["unsupported_gap_failed_closed"] is True
    assert report["challenge_case_count"] == 2
    assert report["fresh_engine_runs_unique"] is True
    assert len(set(report["fresh_engine_runs"])) == 2
    assert all(item["output"] == item["expected"] for item in report["challenge_outputs"])
    assert report["fresh_candidate_state_unchanged"] is True
    assert report["fresh_trial_state_unchanged"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_state_unchanged"] is True
    assert report["prior_runs_copied"] is False
    assert report["prior_episodes_copied"] is False
    assert report["prior_evidence_copied"] is False
    assert report["live_model_invocation_required"] is False

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "adaptive-depth-postaddition-retention"
    )
    files = list(evidence_dir.glob("retention-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
    assert "do not establish independent production" in persisted["claim_boundary"]
