from __future__ import annotations

import json
from pathlib import Path

from agi.seed_committed_target_generator import run_seed_committed_target_generator


def test_seed_committed_target_semantics_are_rediscovered_without_candidate_ids(
    runtime_repo: Path,
) -> None:
    report = run_seed_committed_target_generator(
        runtime_repo,
        "seed-committed-generated-target-semantics",
        max_target_attempts=4,
        required_distinct_semantics=2,
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "seed-committed-skill-graph-target-generator-v1"
    assert report["named_fixed_goal_family_used"] is False
    assert report["target_semantics_source"] == "seed-ranked verified durable skill graph"
    assert report["target_plan_precommitted_before_solver_execution"] is True
    assert 2 <= report["target_attempt_count"] <= report["max_target_attempts"] == 4
    assert report["required_distinct_semantics"] == 2
    assert report["distinct_semantic_fingerprint_count"] >= 2
    assert report["eligible_semantic_target_count"] >= report["target_attempt_count"]
    assert report["generation_search_nodes"] <= report["generation_search_node_budget"]
    assert report["generator_hidden_candidate_ids_supplied_to_solver"] is False
    assert report["all_solver_calls_candidate_id_free"] is True
    assert report["all_generated_routes_multistage"] is True
    assert report["all_fresh_engine_runs_unique"] is True
    assert report["all_candidate_state_unchanged"] is True
    assert report["all_trial_state_unchanged"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_state_unchanged"] is True
    assert report["unsupported_gap_failed_closed"] is True
    assert report["live_model_invocation_required"] is False

    records = report["target_records"]
    assert len(records) == report["target_attempt_count"]
    assert len({item["behavior_fingerprint"] for item in records}) >= 2
    assert len({item["target_commitment"] for item in records}) == len(records)
    for record in records:
        assert record["generator_hidden_candidate_ids"]
        assert record["generator_hidden_depth"] >= 2
        assert record["generator_hidden_candidate_ids_supplied_to_solver"] is False
        assert record["solver_candidate_ids_supplied_by_caller"] is False
        assert record["solver_selected_candidate_ids"]
        assert record["solver_selected_chain_depth"] >= 2
        assert record["solver_search_nodes_visited"] <= record["solver_search_node_budget"]
        assert (
            record["solver_behavior_evaluations"]
            <= record["solver_behavior_evaluation_budget"]
        )
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
        / "seed-committed-target-generator"
    )
    files = list(evidence_dir.glob("generated-targets-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
    assert persisted["named_fixed_goal_family_used"] is False
    assert "do not establish independent production evidence or AGI" in persisted["claim_boundary"]
