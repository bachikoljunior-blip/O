from __future__ import annotations

from pathlib import Path

from agi.memory_first_macro_materialization import run_memory_first_macro_materialization


def test_memory_first_resolution_materializes_and_reuses_skill_across_fresh_candidates(
    runtime_repo: Path,
) -> None:
    report = run_memory_first_macro_materialization(
        runtime_repo,
        "memory-first-materialization-test-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "memory-first-macro-materialization-v1"
    assert report["source_candidate_identity_sets_disjoint"] is True
    assert report["first_initial_matching_route_count"] == 3
    assert report["first_route_counts"] == [3, 2, 1]
    assert report["first_oracle_query_count"] == 2
    assert report["durable_counterexample_count"] == 2
    assert report["later_route_count_after_memory"] == 1
    assert report["later_oracle_query_count"] == 0
    assert report["compiled_program_nodes"] >= 2
    assert report["compiled_program_depth"] >= 2
    assert report["baseline_before_compiled_regression_failed_closed"] is True
    assert report["adverse_compiled_regression_rejected"] is True
    assert report["compiled_candidate_promoted"] is True
    assert report["fresh_engine_run_count"] == 15
    assert report["fresh_challenge_case_count"] == 5
    assert report["all_fresh_outputs_equal"] is True
    assert report["first_source_candidate_trial_state_unchanged"] is True
    assert report["later_source_candidate_trial_state_unchanged"] is True
    assert report["compiled_candidate_trial_state_unchanged"] is True
