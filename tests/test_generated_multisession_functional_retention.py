from __future__ import annotations

import json
from pathlib import Path

from agi.generated_multisession_functional_retention import (
    run_generated_multisession_functional_retention,
)


def test_generated_skills_survive_separate_state_only_learning_sessions(
    runtime_repo: Path,
) -> None:
    campaign_id = "generated-multisession-retention-test"
    seeds = (
        "multi-round-generated-goal-recovery-seed:round-1",
        "multi-round-generated-goal-recovery-seed:round-2",
    )

    report = run_generated_multisession_functional_retention(
        runtime_repo,
        campaign_id,
        seeds=seeds,
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "generated-multisession-functional-retention-v1"
    assert report["state_only_restart_count"] == 3
    assert report["learning_session_count"] == 2
    assert len(report["learned_candidate_ids"]) == 2
    assert len(set(report["learned_candidate_ids"])) == 2
    assert report["all_transfers_byte_and_trial_identical"] is True
    assert report["all_observed_crashes_reconciled_without_relearning"] is True
    assert report["all_learned_skills_functionally_replayed"] is True
    assert report["all_replays_avoided_caller_candidate_ids"] is True
    assert report["remaining_fail_closed_count"] >= 1
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_ledgers_unchanged"] is True
    assert report["prior_runs_copied_between_sessions"] is False
    assert report["prior_episodes_copied_between_sessions"] is False
    assert report["prior_evidence_copied_between_sessions"] is False
    assert all(
        item["candidate_bytes_identical"] and item["trial_ledgers_identical"]
        for item in report["session_transfer_checks"]
    )
    assert all(item["actual"] == item["expected"] for item in report["fresh_session_replays"])
    assert all(
        item["caller_supplied_candidate_ids"] is False
        for item in report["fresh_session_replays"]
    )

    evidence_path = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "generated-multisession-functional-retention"
        / f"{campaign_id}.json"
    )
    persisted = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert "does not establish AGI" in persisted["claim_boundary"]
