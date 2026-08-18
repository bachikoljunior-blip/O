from __future__ import annotations

import json
from pathlib import Path

from agi.rehydrated_state_integrity_isolation import (
    run_rehydrated_state_integrity_isolation,
)


def test_local_durable_state_corruption_fails_closed_without_cross_family_damage(
    runtime_repo: Path,
) -> None:
    report = run_rehydrated_state_integrity_isolation(
        runtime_repo,
        "rehydrated-state-integrity-isolation-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "rehydrated-state-integrity-isolation-v1"
    assert len(report["unaffected_heterogeneous_candidate_ids"]) == 3
    assert len(report["recursive_component_candidate_ids"]) == 3
    assert report["missing_regression_proof_failed_closed"] is True
    assert report["unaffected_state_unchanged_after_missing_proof"] is True
    assert report["unaffected_trials_unchanged_after_missing_proof"] is True
    assert report["target_recovered_after_exact_proof_restore"] is True
    assert report["semantic_tamper_failed_closed"] is True
    assert report["unaffected_state_unchanged_after_semantic_tamper"] is True
    assert report["unaffected_trials_unchanged_after_semantic_tamper"] is True
    assert report["target_recovered_after_exact_candidate_restore"] is True
    assert report["rehydrated_exact_state_restored"] is True
    assert report["rehydrated_trial_ledgers_unchanged"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_ledgers_unchanged"] is True
    assert report["fresh_engine_run_count"] == 12
    assert len(set(report["fresh_engine_runs"])) == 12
    assert report["live_model_invocation_required"] is False

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "rehydrated-state-integrity-isolation"
    )
    files = list(evidence_dir.glob("integrity-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
