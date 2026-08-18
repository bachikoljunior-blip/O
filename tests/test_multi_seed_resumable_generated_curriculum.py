from __future__ import annotations

import json
from pathlib import Path

import pytest

from agi.autonomous_heterogeneous_goal_exhaustion import (
    run_autonomous_heterogeneous_goal_exhaustion,
)
from agi.durable_state_rehydration import _tree_snapshot
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_seed_resumable_generated_curriculum import (
    MultiSeedGeneratedCurriculumError,
    SimulatedMultiSeedInterruption,
    run_multi_seed_resumable_generated_curriculum,
)


def test_accumulates_distinct_generated_skills_across_restarts_without_relearning(
    runtime_repo: Path,
) -> None:
    prerequisite = run_autonomous_heterogeneous_goal_exhaustion(
        runtime_repo,
        "multi-seed-resumable:fixed-prerequisite",
    )
    assert prerequisite["passed"] is True
    assert prerequisite["bounded_goal_set_exhausted"] is True

    campaign_id = "multi-seed-generated-recovery"
    seeds = (
        "multi-seed-generated-alpha",
        "multi-seed-generated-omega",
    )
    initial_ids = _all_candidate_ids(runtime_repo)
    initial_tree = _tree_snapshot(runtime_repo, initial_ids)
    initial_trials = _trial_snapshot(runtime_repo, initial_ids)

    with pytest.raises(SimulatedMultiSeedInterruption):
        run_multi_seed_resumable_generated_curriculum(
            runtime_repo,
            campaign_id,
            seeds=seeds,
            interrupt_after_child_round=0,
        )

    ids_after_child_side_effect = _all_candidate_ids(runtime_repo)
    tree_after_child_side_effect = _tree_snapshot(runtime_repo, ids_after_child_side_effect)
    trials_after_child_side_effect = _trial_snapshot(runtime_repo, ids_after_child_side_effect)
    assert len(ids_after_child_side_effect) == len(initial_ids) + 1

    recovered = run_multi_seed_resumable_generated_curriculum(
        runtime_repo,
        campaign_id,
        seeds=seeds,
        max_new_rounds=1,
    )
    assert recovered["completed_rounds"] == [0]
    assert recovered["rounds"][0]["attempts"] == 1
    assert recovered["last_reconciled_count"] == 1
    assert recovered["last_advance_count"] == 0
    assert _tree_snapshot(runtime_repo, ids_after_child_side_effect) == tree_after_child_side_effect
    assert _trial_snapshot(runtime_repo, ids_after_child_side_effect) == trials_after_child_side_effect

    finished = run_multi_seed_resumable_generated_curriculum(
        runtime_repo,
        campaign_id,
        seeds=seeds,
        max_new_rounds=1,
    )
    assert finished["passed"] is True
    assert finished["status"] == "completed"
    assert finished["completed_rounds"] == [0, 1]
    assert finished["completed_count"] == 2
    assert finished["rounds"][0]["attempts"] == 1
    assert finished["rounds"][1]["attempts"] == 1
    assert finished["parent_intent_persisted_before_child_side_effects"] is True
    assert finished["completed_child_reconciled_without_relearning"] is True
    assert finished["one_verified_candidate_per_round"] is True
    assert finished["unsupported_count_decreased_each_round"] is True
    assert finished["initial_state_unchanged"] is True
    assert finished["fresh_final_validation"]["fresh_execution"] is True
    assert finished["fresh_final_validation"]["caller_supplied_candidate_ids"] is False
    assert len(finished["learned_candidate_ids"]) == 2
    assert len(set(finished["learned_candidate_ids"])) == 2
    assert len(finished["selected_goals"]) == 2
    assert all(
        entry["metadata"]["initial_unsupported_count"]
        > entry["metadata"]["remaining_unsupported_count"] >= 1
        for entry in finished["rounds"]
    )
    assert all(
        set(entry["metadata"]["child_phase_attempts"].values()) == {1}
        for entry in finished["rounds"]
    )
    assert _tree_snapshot(runtime_repo, initial_ids) == initial_tree
    assert _trial_snapshot(runtime_repo, initial_ids) == initial_trials

    evidence_path = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "multi-seed-resumable-generated-curriculum"
        / f"{campaign_id}.json"
    )
    persisted = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert persisted["digest"] == finished["digest"]
    assert "does not establish AGI" in persisted["claim_boundary"]

    final_ids = _all_candidate_ids(runtime_repo)
    final_tree = _tree_snapshot(runtime_repo, final_ids)
    final_trials = _trial_snapshot(runtime_repo, final_ids)
    replayed = run_multi_seed_resumable_generated_curriculum(
        runtime_repo,
        campaign_id,
        seeds=seeds,
    )
    assert replayed["passed"] is True
    assert [entry["attempts"] for entry in replayed["rounds"]] == [1, 1]
    assert _tree_snapshot(runtime_repo, final_ids) == final_tree
    assert _trial_snapshot(runtime_repo, final_ids) == final_trials

    with pytest.raises(MultiSeedGeneratedCurriculumError, match="seed configuration"):
        run_multi_seed_resumable_generated_curriculum(
            runtime_repo,
            campaign_id,
            seeds=tuple(reversed(seeds)),
        )
