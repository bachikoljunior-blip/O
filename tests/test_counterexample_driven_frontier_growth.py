from __future__ import annotations

import json
from pathlib import Path

from agi.counterexample_driven_frontier_growth import (
    run_counterexample_driven_frontier_growth,
)


def test_counterexample_driven_frontier_growth_adds_and_transfers_new_behavior(
    runtime_repo: Path,
) -> None:
    report = run_counterexample_driven_frontier_growth(
        runtime_repo,
        "counterexample-driven-frontier-growth-test",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "counterexample-driven-frontier-growth-v1"
    assert report["frontier_seed_reused_before_and_after_learning"] is True
    assert report["frontier_cardinality_increased"] is True
    assert report["after_frontier_behavior_count"] > report["before_frontier_behavior_count"]
    assert report["new_behavior_count"] >= 1
    assert report["counterexample_schedule_precommitted_before_support_checks"] is True
    assert report["acquisition_target_failed_closed_before_learning"] is True
    assert report["new_candidate_negative_evidence_retained"] is True
    assert report["untouched_generated_control_gap_failed_closed"] is True
    assert report["prior_candidate_state_unchanged"] is True
    assert report["prior_trial_state_unchanged"] is True
    assert report["runtime_budget_unchanged"] is True
    assert report["heldback_challenge_shape_unchanged"] is True
    assert report["selected_new_target"]["learned_candidate_in_hidden_route"] is True
    assert report["solver_candidate_ids_supplied_by_caller"] is False
    assert report["new_candidate_rediscovered_by_behavior_only_solver"] is True
    assert report["new_behavior_transfer_fresh_engine_runs_unique"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_state_unchanged"] is True
    assert report["post_growth_counterfactual_control"]["failed_closed"] is True
    assert report["live_model_invocation_required"] is False

    transfer = report["new_behavior_transfer"]
    assert transfer["generator_hidden_candidate_ids_supplied_to_solver"] is False
    assert transfer["solver_candidate_ids_supplied_by_caller"] is False
    assert transfer["solver_selected_chain_depth"] >= 2
    assert report["new_candidate_id"] in transfer["generator_hidden_candidate_ids"]
    assert report["new_candidate_id"] in transfer["solver_selected_candidate_ids"]
    assert all(
        item["output"] == item["expected"]
        for item in transfer["heldback_challenge_outputs"]
    )

    evidence = list(
        (
            runtime_repo
            / ".continual"
            / "evidence"
            / "counterexample-driven-frontier-growth"
        ).glob("growth-*.json")
    )
    assert len(evidence) == 1
    persisted = json.loads(evidence[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["frontier_cardinality_increased"] is True
    assert persisted["post_growth_counterfactual_control"]["failed_closed"] is True
    assert "does not establish open-domain target generation" in persisted["claim_boundary"]
