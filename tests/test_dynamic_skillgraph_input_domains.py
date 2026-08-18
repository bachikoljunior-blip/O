from __future__ import annotations

import json
from pathlib import Path

from agi.dynamic_skillgraph_input_domains import (
    _observed_generator_input_domains,
    run_dynamic_skillgraph_input_domains,
)


def test_observed_generator_input_domains_come_from_verified_items() -> None:
    items = (
        {"candidate_id": "n", "input_domain": "numeric", "output_domain": "numeric"},
        {"candidate_id": "s", "input_domain": "string", "output_domain": "string"},
        {"candidate_id": "q", "input_domain": "sequence", "output_domain": "numeric"},
        {"candidate_id": "o", "input_domain": "object", "output_domain": "boolean"},
    )
    assert _observed_generator_input_domains(items) == ("numeric", "sequence", "string")
    assert _observed_generator_input_domains(items[:1]) == ("numeric",)


def test_dynamic_skillgraph_exercises_string_source_and_fresh_transfer(
    runtime_repo: Path,
) -> None:
    report = run_dynamic_skillgraph_input_domains(
        runtime_repo,
        "dynamic-skillgraph-input-domain-test",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "dynamic-skillgraph-input-domain-targets-v1"
    assert set(report["observed_generator_input_domains"]) == {
        "numeric",
        "string",
        "sequence",
    }
    assert report["observed_input_domain_count"] == 3
    assert report["fixed_numeric_sequence_start_set_used"] is False
    assert report["target_plan_precommitted_before_solver_execution"] is True
    assert set(report["target_input_domains"]) == set(report["observed_generator_input_domains"])
    assert report["target_record_count"] == 3
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

    for record in report["target_records"]:
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

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "dynamic-skillgraph-input-domains"
    )
    files = list(evidence_dir.glob("dynamic-inputs-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["string_source_target_exercised"] is True
    assert "does not establish open-domain target generation" in persisted["claim_boundary"]
