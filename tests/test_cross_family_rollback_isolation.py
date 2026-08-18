from __future__ import annotations

import json
from pathlib import Path

from agi.cross_family_rollback_isolation import (
    run_cross_family_rollback_isolation,
)


def test_one_family_scope_can_roll_back_without_damaging_other_skills(
    runtime_repo: Path,
) -> None:
    report = run_cross_family_rollback_isolation(
        runtime_repo,
        "cross-family-rollback-isolation-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "cross-family-rollback-isolation-v1"
    assert report["rollback_adopted"] is False
    assert report["revoked_scope_failed_closed"] is True
    assert report["stable_trials_unchanged_during_rollback"] is True
    assert report["recovery_adopted"] is True
    assert report["stable_trials_unchanged_after_recovery"] is True
    assert report["rollback_negative_evidence_retained"] is True
    assert report["new_target_trial_record_count"] == 1
    assert len(report["unaffected_heterogeneous_candidate_ids"]) == 3
    assert len(report["recursive_component_candidate_ids"]) == 3
    assert report["fresh_engine_run_count"] == 10
    assert len(set(report["fresh_engine_runs"])) == 10
    assert report["live_model_invocation_required"] is False

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "cross-family-rollback-isolation"
    )
    files = list(evidence_dir.glob("rollback-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
