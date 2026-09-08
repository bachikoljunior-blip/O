from __future__ import annotations

import json
from pathlib import Path

from agi.durable_state_rehydration import run_durable_state_rehydration


def test_accumulated_skills_rehydrate_from_persistent_state_only(
    runtime_repo: Path,
) -> None:
    report = run_durable_state_rehydration(
        runtime_repo,
        "durable-state-rehydration-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "durable-state-rehydration-v1"
    assert report["candidate_count"] == 8
    assert len(report["heterogeneous_candidate_ids"]) == 4
    assert len(report["recursive_component_candidate_ids"]) == 3
    assert report["prior_runs_copied"] is False
    assert report["prior_episodes_copied"] is False
    assert report["prior_evidence_copied"] is False
    assert report["rehydrated_root_is_distinct"] is True
    assert report["candidate_state_copy_byte_identical"] is True
    assert report["no_candidate_state_failed_closed"] is True
    assert report["candidate_state_unchanged_after_replay"] is True
    assert report["trial_ledgers_unchanged_after_replay"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_ledgers_unchanged"] is True
    assert report["fresh_engine_run_count"] == 8
    assert len(set(report["fresh_engine_runs"])) == 8
    assert len(report["replay_records"]) == 8
    assert report["live_model_invocation_required"] is False

    evidence_dir = runtime_repo / ".continual" / "evidence" / "durable-state-rehydration"
    files = list(evidence_dir.glob("rehydration-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
