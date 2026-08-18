from __future__ import annotations

import json
from pathlib import Path

from agi.dynamic_skillgraph_growth_campaign import run_dynamic_skillgraph_growth_campaign
from agi.dynamic_skillgraph_input_domains import _observed_generator_input_domains


def test_observed_generator_input_domains_come_from_verified_items() -> None:
    items = (
        {"candidate_id": "n", "input_domain": "numeric", "output_domain": "numeric"},
        {"candidate_id": "s", "input_domain": "string", "output_domain": "string"},
        {"candidate_id": "q", "input_domain": "sequence", "output_domain": "numeric"},
        {"candidate_id": "o", "input_domain": "object", "output_domain": "boolean"},
    )
    assert _observed_generator_input_domains(items) == ("numeric", "sequence", "string")
    assert _observed_generator_input_domains(items[:1]) == ("numeric",)


def test_dynamic_skillgraph_grows_missing_string_capacity_then_transfers(
    runtime_repo: Path,
) -> None:
    report = run_dynamic_skillgraph_growth_campaign(
        runtime_repo,
        "dynamic-skillgraph-input-domain-test",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "dynamic-skillgraph-growth-campaign-v1"
    assert report["failure_boundary_weakened"] is False
    assert report["string_growth_candidate_id"]
    assert report["string_growth_candidate_negative_evidence_retained"] is True
    assert report["string_growth_transactional_commit"]["atomic_directory_rename"] is True
    assert report["string_growth_transactional_commit"]["overwrite_allowed"] is False
    assert report["string_growth_control_gap_remained_failed_closed"] is True
    assert set(report["observed_generator_input_domains"]) == {
        "numeric",
        "string",
        "sequence",
    }
    assert report["fixed_numeric_sequence_start_set_used"] is False
    assert report["target_plan_precommitted_before_solver_execution"] is True
    assert report["runtime_budget_unchanged"] is True
    assert report["heldback_challenge_shape_unchanged"] is True
    assert isinstance(report["runtime_shape_rejections"], list)
    assert isinstance(report["exact_runtime_rejections"], list)
    assert report["string_source_target_exercised"] is True
    assert report["all_generator_hidden_ids_withheld"] is True
    assert report["all_solver_calls_candidate_id_free"] is True
    assert report["all_generated_routes_multistage"] is True
    assert report["all_fresh_engine_runs_unique"] is True
    assert report["all_candidate_state_unchanged"] is True
    assert report["all_trial_state_unchanged"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_state_unchanged"] is True
    assert report["unrelated_cross_source_control_failed_closed"] is True
    assert report["live_model_invocation_required"] is False

    records = report["target_records"]
    assert {record["input_domain"] for record in records} == {
        "numeric",
        "string",
        "sequence",
    }
    for record in records:
        assert record["generator_hidden_candidate_ids"]
        assert record["generator_hidden_depth"] >= 2
        assert record["generator_hidden_candidate_ids_supplied_to_solver"] is False
        assert record["solver_candidate_ids_supplied_by_caller"] is False
        assert record["solver_selected_candidate_ids"]
        assert record["solver_selected_chain_depth"] >= 2
        assert record["fresh_engine_runs_unique"] is True
        assert len(record["fresh_engine_runs"]) == 2
        assert all(
            item["output"] == item["expected"]
            for item in record["heldback_challenge_outputs"]
        )
        assert record["candidate_state_unchanged"] is True
        assert record["trial_state_unchanged"] is True
        assert record["prior_runs_copied"] is False
        assert record["prior_episodes_copied"] is False
        assert record["prior_evidence_copied"] is False

    dynamic_evidence = list(
        (
            runtime_repo
            / ".continual"
            / "evidence"
            / "dynamic-skillgraph-input-domains"
        ).glob("dynamic-inputs-*.json")
    )
    assert len(dynamic_evidence) == 1
    dynamic_persisted = json.loads(dynamic_evidence[0].read_text(encoding="utf-8"))
    assert dynamic_persisted["string_source_target_exercised"] is True
    assert dynamic_persisted["runtime_budget_unchanged"] is True
    assert dynamic_persisted["heldback_challenge_shape_unchanged"] is True

    growth_evidence = list(
        (
            runtime_repo
            / ".continual"
            / "evidence"
            / "dynamic-skillgraph-growth-campaign"
        ).glob("growth-*.json")
    )
    assert len(growth_evidence) == 1
    growth_persisted = json.loads(growth_evidence[0].read_text(encoding="utf-8"))
    assert growth_persisted["digest"] == report["digest"]
    assert growth_persisted["failure_boundary_weakened"] is False
    assert growth_persisted["runtime_budget_unchanged"] is True
    assert growth_persisted["heldback_challenge_shape_unchanged"] is True
    assert "does not establish open-domain target generation" in growth_persisted["claim_boundary"]
