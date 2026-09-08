from __future__ import annotations

from pathlib import Path

from agi.cross_context_memory_skill_transfer import (
    run_cross_context_memory_skill_transfer,
)


def test_related_context_reuses_source_counterexamples_and_verified_skill_fail_closed(
    runtime_repo: Path,
) -> None:
    report = run_cross_context_memory_skill_transfer(
        runtime_repo,
        "cross-context-memory-skill-transfer-test-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "cross-context-memory-skill-transfer-v1"
    assert report["source_and_target_task_keys_distinct"] is True
    assert report["source_and_target_support_digests_differ"] is True
    assert report["source_counterexample_count"] == 2
    assert report["source_materialized_candidate_promoted"] is True
    assert report["source_adverse_regression_rejected"] is True
    assert report["source_fresh_engine_run_count"] == 15
    assert len(report["related_candidate_ids"]) == 3
    assert len(report["related_distractor_candidate_ids"]) == 2
    assert report["related_initial_matching_route_count"] == 3
    assert report["related_exact_counterexample_count"] == 0
    assert report["related_accepted_counterexample_count"] == 2
    assert report["related_route_counts"] == [3, 2, 1]
    assert report["related_oracle_query_count"] == 0
    assert report["related_selected_candidate_ids"] == [
        report["source_materialized_candidate_id"]
    ]
    assert report["verified_source_skill_reused_for_related_task"] is True
    assert report["incompatible_contract_failed_closed"] is True
    assert report["target_exact_bank_created"] is False
    assert report["incompatible_exact_bank_created"] is False
    assert report["source_bank_state_unchanged"] is True
    assert report["candidate_trial_state_unchanged"] is True
    assert report["related_fresh_engine_run_count"] == 15
    assert report["related_fresh_engine_run_ids_unique"] is True
    assert report["fresh_challenge_case_count"] == 5
    assert report["all_fresh_outputs_equal"] is True
