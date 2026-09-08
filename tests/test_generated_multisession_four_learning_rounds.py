from __future__ import annotations

from pathlib import Path

from agi.generated_multisession_functional_retention import (
    run_generated_multisession_functional_retention,
)


def test_four_generated_learning_sessions_retain_all_skills_state_only(
    runtime_repo: Path,
) -> None:
    report = run_generated_multisession_functional_retention(
        runtime_repo,
        "generated-multisession-four-learning-test",
        seeds=(
            "multi-round-generated-goal-recovery-seed:round-1",
            "multi-round-generated-goal-recovery-seed:round-2",
            "multi-round-generated-goal-recovery-seed:round-3",
            "multi-round-generated-goal-recovery-seed:round-4",
        ),
    )

    assert report["passed"] is True
    assert report["learning_session_count"] == 4
    assert report["state_only_restart_count"] == 5
    assert len(report["learned_candidate_ids"]) == 4
    assert len(set(report["learned_candidate_ids"])) == 4
    assert [(item["from_session"], item["to_session"]) for item in report["session_transfer_checks"]] == [
        (1, 2),
        (2, 3),
        (3, 4),
        (4, 5),
    ]
    assert report["all_transfers_byte_and_trial_identical"] is True
    assert report["all_observed_crashes_reconciled_without_relearning"] is True
    assert report["all_learned_skills_functionally_replayed"] is True
    assert report["all_replays_avoided_caller_candidate_ids"] is True
    assert len(report["fresh_session_replays"]) == 4
    assert report["remaining_fail_closed_count"] >= 1
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_ledgers_unchanged"] is True
    assert report["prior_runs_copied_between_sessions"] is False
    assert report["prior_episodes_copied_between_sessions"] is False
    assert report["prior_evidence_copied_between_sessions"] is False
    assert all(item["actual"] == item["expected"] for item in report["fresh_session_replays"])
    assert all(
        item["caller_supplied_candidate_ids"] is False
        for item in report["fresh_session_replays"]
    )
    assert "does not establish AGI" in report["claim_boundary"]
