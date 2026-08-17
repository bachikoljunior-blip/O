from __future__ import annotations

from pathlib import Path

from agi.materialized_postcommit_transfer import run_materialized_postcommit_transfer


def test_materialized_capabilities_improve_postcommit_durable_challenges_without_new_trials() -> None:
    report = run_materialized_postcommit_transfer(Path(__file__).parents[1], minimum_candidates=3)

    assert report["passed"] is True
    assert report["candidate_count"] >= 3
    assert len(report["results"]) == report["candidate_count"]
    assert set(report["all_baseline_scores"]) == {0.0}
    assert set(report["all_candidate_scores"]) == {1.0}
    assert all(item["challenge_count"] == 3 for item in report["results"])
    assert all(item["durable_run_persisted"] is True for item in report["results"])
    assert report["earliest_candidate_retained_after_later_challenges"] is True
    assert report["candidate_trial_state_unchanged"] is True
    assert report["live_model_invocation_required"] is False
