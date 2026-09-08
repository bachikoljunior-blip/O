from __future__ import annotations

from pathlib import Path

from agi.durable_state_rehydration import _tree_snapshot
from agi.generated_cross_round_functional_retention import (
    run_generated_cross_round_functional_retention,
)
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot


def test_four_crash_recovered_generated_rounds_remain_functionally_replayable(
    runtime_repo: Path,
) -> None:
    """Falsify whether retained generated-skill growth survives a fourth distinct round."""
    campaign_id = "generated-cross-round-four-round-retention-test"
    seeds = (
        "multi-round-generated-goal-recovery-seed:round-1",
        "multi-round-generated-goal-recovery-seed:round-2",
        "multi-round-generated-goal-recovery-seed:round-3",
        "multi-round-generated-goal-recovery-seed:round-4",
    )

    report = run_generated_cross_round_functional_retention(
        runtime_repo,
        campaign_id,
        seeds=seeds,
    )

    assert report["passed"] is True
    assert report["round_count"] == 4
    assert report["distinct_candidate_per_round"] is True
    assert len(report["learned_candidate_ids"]) == 4
    assert len(set(report["learned_candidate_ids"])) == 4
    assert report["all_child_phase_attempts_once"] is True
    assert report["all_observed_crashes_reconciled_without_relearning"] is True
    assert report["initial_candidate_state_unchanged"] is True
    assert report["initial_trial_ledgers_unchanged"] is True
    assert report["fresh_execution_used_for_retention"] is True
    assert report["all_learned_skills_functionally_replayed"] is True
    assert len(report["fresh_replay"]) == 4
    assert report["remaining_fail_closed_count"] >= 1
    assert all(item["caller_supplied_candidate_ids"] is False for item in report["fresh_replay"])
    assert all(item["actual"] == item["expected"] for item in report["fresh_replay"])

    final_ids = _all_candidate_ids(runtime_repo)
    final_tree = _tree_snapshot(runtime_repo, final_ids)
    final_trials = _trial_snapshot(runtime_repo, final_ids)
    replay = run_generated_cross_round_functional_retention(
        runtime_repo,
        campaign_id,
        seeds=seeds,
    )
    assert replay == report
    assert _all_candidate_ids(runtime_repo) == final_ids
    assert _tree_snapshot(runtime_repo, final_ids) == final_tree
    assert _trial_snapshot(runtime_repo, final_ids) == final_trials
    assert "does not establish AGI" in report["claim_boundary"]
