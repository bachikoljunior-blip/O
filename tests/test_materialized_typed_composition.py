from __future__ import annotations

from pathlib import Path

from agi.materialized_typed_composition import run_materialized_typed_composition


def test_distinct_materialized_capabilities_compose_and_survive_restart() -> None:
    report = run_materialized_typed_composition(Path(__file__).parents[1])

    assert report["passed"] is True
    assert len(report["candidate_ids"]) == 2
    assert len(set(report["candidate_ids"])) == 2
    assert len(set(report["scopes"])) == 2
    assert len(report["domain_chain"]) == 3
    assert report["baseline_without_candidates_failed_closed"] is True
    assert len(set(report["durable_run_ids"])) == 2
    assert report["durable_runs_persisted"] is True
    assert report["fresh_engine_composition_matched"] is True
    assert report["candidate_trial_state_unchanged"] is True
    assert report["live_model_invocation_required"] is False
