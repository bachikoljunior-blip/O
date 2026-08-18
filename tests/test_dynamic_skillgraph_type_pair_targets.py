from __future__ import annotations

import json
from pathlib import Path

from agi.dynamic_behavior_frontier import run_dynamic_behavior_frontier
from agi.dynamic_skillgraph_growth_campaign import run_dynamic_skillgraph_growth_campaign
from agi.dynamic_skillgraph_type_pair_targets import run_dynamic_skillgraph_type_pair_targets


def test_dynamic_skillgraph_type_pair_targets_cover_observed_pairs(runtime_repo: Path) -> None:
    growth = run_dynamic_skillgraph_growth_campaign(
        runtime_repo,
        "dynamic-skillgraph-input-domain-test",
    )
    assert growth["passed"] is True
    assert growth["runtime_budget_unchanged"] is True
    assert growth["heldback_challenge_shape_unchanged"] is True

    report = run_dynamic_skillgraph_type_pair_targets(
        runtime_repo,
        "dynamic-skillgraph-type-pair-test",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "dynamic-skillgraph-type-pair-targets-v1"
    assert report["observed_type_pair_count"] >= 4
    pair_keys = {item["pair_key"] for item in report["observed_type_pairs"]}
    assert len(pair_keys) == report["observed_type_pair_count"]
    assert {item["input_domain"] for item in report["observed_type_pairs"]} == {
        "numeric",
        "sequence",
        "string",
    }
    assert len({item["output_domain"] for item in report["observed_type_pairs"]}) >= 2
    assert any(
        item["input_domain"] != item["output_domain"]
        for item in report["observed_type_pairs"]
    )
    assert set(report["target_pair_keys"]) == pair_keys
    assert report["target_plan_precommitted_before_solver_execution"] is True
    assert report["cross_domain_pair_target_exercised"] is True
    assert report["all_generator_hidden_ids_withheld"] is True
    assert report["all_solver_calls_candidate_id_free"] is True
    assert report["all_generated_routes_multistage"] is True
    assert report["all_fresh_engine_runs_unique"] is True
    assert report["all_candidate_state_unchanged"] is True
    assert report["all_trial_state_unchanged"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_state_unchanged"] is True
    assert report["untouched_cross_pair_control"]["pair_key"] == "string->numeric"
    assert report["untouched_cross_pair_control"]["failed_closed"] is True
    assert report["live_model_invocation_required"] is False

    for record in report["target_records"]:
        assert record["generator_hidden_candidate_ids"]
        assert record["generator_hidden_candidate_ids_supplied_to_solver"] is False
        assert record["solver_candidate_ids_supplied_by_caller"] is False
        assert record["solver_selected_chain_depth"] >= 2
        assert record["fresh_engine_runs_unique"] is True
        assert all(
            item["output"] == item["expected"]
            for item in record["heldback_challenge_outputs"]
        )

    evidence = list(
        (
            runtime_repo
            / ".continual"
            / "evidence"
            / "dynamic-skillgraph-type-pairs"
        ).glob("type-pairs-*.json")
    )
    assert len(evidence) == 1
    persisted = json.loads(evidence[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["untouched_cross_pair_control"]["failed_closed"] is True
    assert "does not establish open-domain target generation" in persisted["claim_boundary"]

    frontier = run_dynamic_behavior_frontier(
        runtime_repo,
        "dynamic-behavior-frontier-test",
        target_count=4,
    )
    assert frontier["passed"] is True
    assert frontier["campaign_kind"] == "dynamic-behavior-frontier-v1"
    assert frontier["frontier_behavior_count"] >= frontier["selected_target_count"] >= 3
    assert frontier["fixed_domain_or_pair_quota_used"] is False
    assert frontier["target_plan_precommitted_before_solver_execution"] is True
    assert frontier["selected_distinct_input_domains"] >= 2
    assert frontier["selected_distinct_output_domains"] >= 2
    assert frontier["selected_cross_domain_behavior"] is True
    assert frontier["runtime_budget_unchanged"] is True
    assert frontier["heldback_challenge_shape_unchanged"] is True
    assert frontier["all_generator_hidden_ids_withheld"] is True
    assert frontier["all_solver_calls_candidate_id_free"] is True
    assert frontier["all_generated_routes_multistage"] is True
    assert frontier["all_fresh_engine_runs_unique"] is True
    assert frontier["all_candidate_state_unchanged"] is True
    assert frontier["all_trial_state_unchanged"] is True
    assert frontier["source_candidate_state_unchanged"] is True
    assert frontier["source_trial_state_unchanged"] is True
    assert frontier["counterfactual_control"]["failed_closed"] is True
    assert frontier["live_model_invocation_required"] is False
    for record in frontier["target_records"]:
        assert record["generator_hidden_candidate_ids_supplied_to_solver"] is False
        assert record["solver_candidate_ids_supplied_by_caller"] is False
        assert record["solver_selected_chain_depth"] >= 2
        assert record["fresh_engine_runs_unique"] is True
        assert all(
            item["output"] == item["expected"]
            for item in record["heldback_challenge_outputs"]
        )

    frontier_evidence = list(
        (
            runtime_repo
            / ".continual"
            / "evidence"
            / "dynamic-behavior-frontier"
        ).glob("frontier-*.json")
    )
    assert len(frontier_evidence) == 1
    frontier_persisted = json.loads(frontier_evidence[0].read_text(encoding="utf-8"))
    assert frontier_persisted["digest"] == frontier["digest"]
    assert frontier_persisted["fixed_domain_or_pair_quota_used"] is False
    assert frontier_persisted["counterfactual_control"]["failed_closed"] is True
    assert "does not establish open-domain task generation" in frontier_persisted["claim_boundary"]
