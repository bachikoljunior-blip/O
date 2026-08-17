from __future__ import annotations

from pathlib import Path

from agi.materialized_runtime_replay import run_materialized_runtime_retention_replay


def test_materialized_candidate_survives_engine_restart_without_new_trial() -> None:
    root = Path(__file__).parents[1]
    report = run_materialized_runtime_retention_replay(root)

    assert report["passed"] is True
    assert report["baseline_without_candidate_failed_closed"] is True
    assert report["same_run_replay_reused"] is True
    assert report["fresh_engine_replay_matched"] is True
    assert report["candidate_trial_state_unchanged"] is True
    assert report["candidate_trial_file_count"] >= 1
    assert report["execution_kind"] == "verified_learned_tool"
    assert report["program_kind"] == "acquired_program"
