from __future__ import annotations

import json
from pathlib import Path

from agi.cumulative_counterexample_frontier_growth import (
    run_cumulative_counterexample_frontier_growth,
)


def test_cumulative_counterexample_frontier_growth_retains_prior_learned_behaviors(
    runtime_repo: Path,
) -> None:
    report = run_cumulative_counterexample_frontier_growth(
        runtime_repo,
        "cumulative-counterexample-frontier-growth-test",
        rounds=2,
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "cumulative-counterexample-frontier-growth-v1"
    assert report["round_count"] == 2
    assert report["frontier_seed_reused_across_all_rounds"] is True
    assert report["frontier_strictly_grew_each_round"] is True
    assert len(report["frontier_behavior_counts"]) == 3
    assert all(
        later > earlier
        for earlier, later in zip(
            report["frontier_behavior_counts"][:-1],
            report["frontier_behavior_counts"][1:],
            strict=True,
        )
    )
    assert report["learned_candidate_ids_unique"] is True
    assert len(report["learned_candidate_ids"]) == 2
    assert report["all_selected_behaviors_retained_in_final_frontier"] is True
    assert report["all_final_retained_targets_replayed"] is True
    assert report["all_round_counterexamples_retained"] is True
    assert report["all_round_control_gaps_failed_closed"] is True
    assert report["all_round_counterfactual_controls_failed_closed"] is True
    assert report["all_prior_candidate_state_unchanged"] is True
    assert report["all_prior_trial_state_unchanged"] is True
    assert report["runtime_budget_unchanged"] is True
    assert report["heldback_challenge_shape_unchanged"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_state_unchanged"] is True
    assert report["live_model_invocation_required"] is False

    assert len(report["round_reports"]) == 2
    assert report["round_reports"][0]["retained_target_replays"] == []
    assert len(report["round_reports"][1]["retained_target_replays"]) == 1
    for round_report in report["round_reports"]:
        assert round_report["frontier_cardinality_increased"] is True
        assert round_report["new_behavior_count"] >= 1
        assert round_report["acquisition_target_failed_closed_before_learning"] is True
        assert round_report["new_candidate_negative_evidence_retained"] is True
        assert round_report["untouched_generated_control_gap_failed_closed"] is True
        assert round_report["new_candidate_rediscovered_by_behavior_only_solver"] is True
        assert round_report["prior_selected_frontier_behaviors_retained"] is True
        assert round_report["post_growth_counterfactual_control"]["failed_closed"] is True

    assert len(report["final_retained_target_replays"]) == 2
    for replay in report["final_retained_target_replays"]:
        assert replay["solver_candidate_ids_supplied_by_caller"] is False
        assert replay["solver_selected_chain_depth"] >= 2
        assert replay["fresh_engine_runs_unique"] is True
        assert all(
            item["output"] == item["expected"]
            for item in replay["heldback_challenge_outputs"]
        )

    evidence = list(
        (
            runtime_repo
            / ".continual"
            / "evidence"
            / "cumulative-counterexample-frontier-growth"
        ).glob("cumulative-*.json")
    )
    assert len(evidence) == 1
    persisted = json.loads(evidence[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["frontier_strictly_grew_each_round"] is True
    assert persisted["all_final_retained_targets_replayed"] is True
    assert "does not establish open-domain self-improvement" in persisted["claim_boundary"]
