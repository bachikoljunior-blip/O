from __future__ import annotations

from pathlib import Path

from agi.materialized_retention_matrix import run_materialized_retention_matrix


def test_all_materialized_acquired_capabilities_survive_fresh_engines_without_new_trials() -> None:
    root = Path(__file__).parents[1]
    report = run_materialized_retention_matrix(root, minimum_candidates=3)

    assert report["passed"] is True
    assert report["candidate_count"] >= 3
    assert len(report["candidate_ids"]) == report["candidate_count"]
    assert len(set(report["candidate_ids"])) == report["candidate_count"]
    assert len(set(report["scopes"])) == report["candidate_count"]
    assert report["all_baselines_failed_closed"] is True
    assert report["all_fresh_engine_replays_matched"] is True
    assert report["all_candidate_trial_state_unchanged"] is True
    assert report["earliest_candidate_survived_after_later_replays"] is True
    assert report["live_model_invocation_required"] is False
    assert {"string", "sequence"}.issubset(set(report["input_domains"]))
    assert all(item["trial_file_count"] >= 1 for item in report["results"])
    assert all(item["candidate_trial_state_unchanged"] is True for item in report["results"])
