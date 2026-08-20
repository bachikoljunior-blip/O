from __future__ import annotations

import json
from pathlib import Path

from agi.durable_state_rehydration import _tree_snapshot
from agi.generated_cross_round_functional_retention import (
    run_generated_cross_round_functional_retention,
)
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot


def test_fresh_replay_retains_both_crash_recovered_generated_skills(
    runtime_repo: Path,
) -> None:
    campaign_id = "generated-cross-round-retention-test"
    seeds = (
        "multi-round-generated-goal-recovery-seed:round-1",
        "multi-round-generated-goal-recovery-seed:round-2",
    )

    report = run_generated_cross_round_functional_retention(
        runtime_repo,
        campaign_id,
        seeds=seeds,
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "generated-cross-round-functional-retention-v1"
    assert report["round_count"] == 2
    assert report["distinct_candidate_per_round"] is True
    assert report["all_child_phase_attempts_once"] is True
    assert report["all_observed_crashes_reconciled_without_relearning"] is True
    assert report["initial_candidate_state_unchanged"] is True
    assert report["initial_trial_ledgers_unchanged"] is True
    assert report["fresh_execution_used_for_retention"] is True
    assert report["all_learned_skills_functionally_replayed"] is True
    assert report["remaining_fail_closed_count"] >= 1
    assert len(report["learned_candidate_ids"]) == 2
    assert len(set(report["learned_candidate_ids"])) == 2
    assert len(report["fresh_replay"]) == 2
    assert all(item["caller_supplied_candidate_ids"] is False for item in report["fresh_replay"])
    assert all(item["actual"] == item["expected"] for item in report["fresh_replay"])
    assert all(set(item["child_phase_attempts"].values()) == {1} for item in report["rounds"])
    assert all(item["negative_evidence_retained"] is True for item in report["rounds"])

    evidence_path = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "generated-cross-round-functional-retention"
        / f"{campaign_id}.json"
    )
    persisted = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert "does not establish AGI" in persisted["claim_boundary"]

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
