from __future__ import annotations

from pathlib import Path

from agi.recursive_memory_skill_composition import run_recursive_memory_skill_composition


def test_recursive_memory_materialized_skill_is_reused_and_independently_gated(
    runtime_repo: Path,
) -> None:
    report = run_recursive_memory_skill_composition(
        runtime_repo,
        "recursive-memory-skill-test-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "recursive-memory-skill-composition-v1"
    assert report["memory_base_first_oracle_query_count"] == 2
    assert report["memory_base_later_oracle_query_count"] == 0
    assert report["memory_base_durable_counterexample_count"] == 2
    assert len(report["component_candidate_ids"]) == 3
    assert len(report["selected_candidate_ids"]) == 2
    assert report["selected_candidate_ids"][0] == report["memory_base_candidate_id"]
    assert report["selected_depth"] == 2
    assert report["expanded_prefix_count"] >= 2
    assert report["evaluated_complete_route_count"] >= 2
    assert report["compiled_program_nodes"] >= 3
    assert report["compiled_program_depth"] >= 3
    assert report["baseline_before_second_generation_regression_failed_closed"] is True
    assert report["adverse_second_generation_regression_rejected"] is True
    assert report["second_generation_candidate_promoted"] is True
    assert report["fresh_engine_run_count"] == 15
    assert report["fresh_challenge_case_count"] == 5
    assert report["all_fresh_outputs_equal"] is True
    assert report["component_candidate_trial_state_unchanged"] is True
    assert report["second_generation_candidate_trial_state_unchanged"] is True
