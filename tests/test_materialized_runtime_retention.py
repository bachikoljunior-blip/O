from __future__ import annotations

from pathlib import Path

from agi.materialized_runtime_replay import run_materialized_runtime_retention_replay


def test_all_materialized_candidates_survive_engine_restart_without_new_trials() -> None:
    root = Path(__file__).parents[1]
    report = run_materialized_runtime_retention_replay(root)

    assert report["passed"] is True
    assert report["capability_count"] >= 3
    assert report["distinct_scope_count"] == report["capability_count"]
    assert len(set(report["candidate_ids"])) == report["capability_count"]
    assert report["all_materialized_capabilities_replayed"] is True
    assert report["all_candidate_trial_state_unchanged"] is True
    assert report["fresh_engine_reverse_order_replay"] is True
    assert report["live_model_invocation_required"] is False

    capabilities = report["capabilities"]
    assert len(capabilities) == report["capability_count"]
    assert {item["candidate_id"] for item in capabilities} == set(report["candidate_ids"])
    for capability in capabilities:
        assert capability["baseline_without_candidate_failed_closed"] is True
        assert capability["same_run_replay_reused"] is True
        assert capability["fresh_engine_replay_matched"] is True
        assert capability["candidate_trial_file_count"] >= 1
        assert capability["execution_kind"] == "verified_learned_tool"
        assert capability["program_kind"] == "acquired_program"

    # Legacy single-capability summary remains consistent for existing callers.
    assert report["baseline_without_candidate_failed_closed"] is True
    assert report["same_run_replay_reused"] is True
    assert report["fresh_engine_replay_matched"] is True
    assert report["candidate_trial_state_unchanged"] is True
    assert report["candidate_trial_file_count"] >= 1
    assert report["execution_kind"] == "verified_learned_tool"
    assert report["program_kind"] == "acquired_program"
