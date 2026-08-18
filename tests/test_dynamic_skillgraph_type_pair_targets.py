from __future__ import annotations

import json
from pathlib import Path

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
    assert {
        "numeric->numeric",
        "string->string",
        "sequence->sequence",
        "sequence->numeric",
    }.issubset(pair_keys)
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
