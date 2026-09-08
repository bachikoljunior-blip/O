from __future__ import annotations

import json
from pathlib import Path

import pytest

from agi.autonomous_heterogeneous_goal_exhaustion import (
    run_autonomous_heterogeneous_goal_exhaustion,
)
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.durable_state_rehydration import _tree_snapshot
from agi.resumable_seeded_generated_goal_round import (
    ResumableGeneratedGoalError,
    SimulatedGeneratedGoalInterruption,
    _receipt_path,
    run_resumable_seeded_generated_goal_round,
)


def test_recovers_post_commit_crash_without_relearning_and_rejects_tamper(
    runtime_repo: Path,
) -> None:
    seed = "resumable-generated-goal-round-seed"
    prerequisite = run_autonomous_heterogeneous_goal_exhaustion(
        runtime_repo,
        seed + ":fixed-prerequisite",
    )
    assert prerequisite["passed"] is True
    assert prerequisite["bounded_goal_set_exhausted"] is True

    campaign_id = "generated-goal-recovery-test"
    selected = run_resumable_seeded_generated_goal_round(
        runtime_repo,
        campaign_id,
        seed=seed,
        max_new_phases=1,
    )
    assert selected["completed_phases"] == ["select"]
    assert selected["phases"]["select"]["attempts"] == 1
    selected_goal = selected["phases"]["select"]["metadata"]["selected_goal"]
    assert selected_goal in selected["phases"]["select"]["metadata"]["unsupported_goals"]

    with pytest.raises(SimulatedGeneratedGoalInterruption):
        run_resumable_seeded_generated_goal_round(
            runtime_repo,
            campaign_id,
            seed=seed,
            max_new_phases=1,
            interrupt_after_learn_receipt=True,
        )

    receipt_file = _receipt_path(runtime_repo, campaign_id)
    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    candidate_id = receipt["candidate_id"]
    ids_after_side_effect = _all_candidate_ids(runtime_repo)
    tree_after_side_effect = _tree_snapshot(runtime_repo, ids_after_side_effect)
    trials_after_side_effect = _trial_snapshot(runtime_repo, ids_after_side_effect)
    assert candidate_id in ids_after_side_effect

    recovered = run_resumable_seeded_generated_goal_round(
        runtime_repo,
        campaign_id,
        seed=seed,
        max_new_phases=1,
    )
    assert recovered["completed_phases"] == ["select", "learn_commit"]
    assert recovered["phases"]["learn_commit"]["attempts"] == 1
    assert recovered["last_reconciled_count"] == 1
    assert recovered["last_advance_count"] == 0
    assert _tree_snapshot(runtime_repo, ids_after_side_effect) == tree_after_side_effect
    assert _trial_snapshot(runtime_repo, ids_after_side_effect) == trials_after_side_effect

    finished = run_resumable_seeded_generated_goal_round(
        runtime_repo,
        campaign_id,
        seed=seed,
    )
    assert finished["passed"] is True
    assert finished["status"] == "completed"
    assert finished["completed_phases"] == ["select", "learn_commit", "verify"]
    assert finished["phases"]["select"]["attempts"] == 1
    assert finished["phases"]["learn_commit"]["attempts"] == 1
    assert finished["phases"]["verify"]["attempts"] == 1
    assert finished["selection_intent_persisted_before_learning"] is True
    assert finished["post_commit_receipt_enabled"] is True
    assert finished["post_commit_pre_journal_crash_recoverable_without_relearning"] is True
    assert finished["receipt_fail_closed_on_tamper_or_state_mismatch"] is True
    verify = finished["phases"]["verify"]["metadata"]
    assert candidate_id in verify["selected_candidate_ids"]
    assert verify["fresh_engine_run"]
    assert verify["remaining_unsupported_count"] >= 1

    evidence = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "resumable-seeded-generated-goal-round"
        / f"{campaign_id}.json"
    )
    persisted = json.loads(evidence.read_text(encoding="utf-8"))
    assert persisted["digest"] == finished["digest"]
    assert "do not establish" in persisted["claim_boundary"]

    # Reuse the already exhausted fixed base instead of rerunning the heavy prerequisite. A second
    # seed creates a distinct generated-goal intent/receipt, then a receipt identity edit must fail
    # closed rather than replaying the learning side effect.
    tamper_seed = seed + ":tamper"
    tamper_campaign = "generated-goal-receipt-tamper"
    run_resumable_seeded_generated_goal_round(
        runtime_repo,
        tamper_campaign,
        seed=tamper_seed,
        max_new_phases=1,
    )
    with pytest.raises(SimulatedGeneratedGoalInterruption):
        run_resumable_seeded_generated_goal_round(
            runtime_repo,
            tamper_campaign,
            seed=tamper_seed,
            max_new_phases=1,
            interrupt_after_learn_receipt=True,
        )

    tampered_path = _receipt_path(runtime_repo, tamper_campaign)
    tampered = json.loads(tampered_path.read_text(encoding="utf-8"))
    tampered["selected_goal"] = "tampered-goal"
    tampered_path.write_text(
        json.dumps(tampered, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ResumableGeneratedGoalError, match="receipt"):
        run_resumable_seeded_generated_goal_round(
            runtime_repo,
            tamper_campaign,
            seed=tamper_seed,
            max_new_phases=1,
        )


def test_two_interrupted_generated_goal_rounds_accumulate_without_duplicate_effects(
    runtime_repo: Path,
) -> None:
    seed = "multi-round-generated-goal-recovery-seed"
    prerequisite = run_autonomous_heterogeneous_goal_exhaustion(
        runtime_repo,
        seed + ":fixed-prerequisite",
    )
    assert prerequisite["passed"] is True
    assert prerequisite["bounded_goal_set_exhausted"] is True

    round_results: list[dict[str, object]] = []
    protected_after_first: tuple[list[str], dict[str, str], dict[str, dict[str, str]]] | None = None

    for index in (1, 2):
        round_seed = f"{seed}:round-{index}"
        campaign_id = f"generated-goal-multi-round-{index}"
        selected = run_resumable_seeded_generated_goal_round(
            runtime_repo,
            campaign_id,
            seed=round_seed,
            max_new_phases=1,
        )
        assert selected["completed_phases"] == ["select"]

        with pytest.raises(SimulatedGeneratedGoalInterruption):
            run_resumable_seeded_generated_goal_round(
                runtime_repo,
                campaign_id,
                seed=round_seed,
                max_new_phases=1,
                interrupt_after_learn_receipt=True,
            )

        receipt = json.loads(_receipt_path(runtime_repo, campaign_id).read_text(encoding="utf-8"))
        candidate_id = str(receipt["candidate_id"])
        ids_after_commit = _all_candidate_ids(runtime_repo)
        tree_after_commit = _tree_snapshot(runtime_repo, ids_after_commit)
        trials_after_commit = _trial_snapshot(runtime_repo, ids_after_commit)
        assert candidate_id in ids_after_commit

        reconciled = run_resumable_seeded_generated_goal_round(
            runtime_repo,
            campaign_id,
            seed=round_seed,
            max_new_phases=1,
        )
        assert reconciled["last_reconciled_count"] == 1
        assert reconciled["last_advance_count"] == 0
        assert reconciled["phases"]["learn_commit"]["attempts"] == 1
        assert _tree_snapshot(runtime_repo, ids_after_commit) == tree_after_commit
        assert _trial_snapshot(runtime_repo, ids_after_commit) == trials_after_commit

        finished = run_resumable_seeded_generated_goal_round(
            runtime_repo,
            campaign_id,
            seed=round_seed,
        )
        assert finished["passed"] is True
        assert finished["status"] == "completed"
        assert finished["phases"]["learn_commit"]["attempts"] == 1
        assert finished["phases"]["verify"]["attempts"] == 1
        round_results.append(finished)

        if index == 1:
            first_ids = _all_candidate_ids(runtime_repo)
            protected_after_first = (
                first_ids,
                _tree_snapshot(runtime_repo, first_ids),
                _trial_snapshot(runtime_repo, first_ids),
            )
        else:
            assert protected_after_first is not None
            first_ids, first_tree, first_trials = protected_after_first
            assert _tree_snapshot(runtime_repo, first_ids) == first_tree
            assert _trial_snapshot(runtime_repo, first_ids) == first_trials

    first_candidate = str(round_results[0]["phases"]["learn_commit"]["metadata"]["candidate_id"])
    second_candidate = str(round_results[1]["phases"]["learn_commit"]["metadata"]["candidate_id"])
    assert second_candidate != first_candidate

    final_ids = _all_candidate_ids(runtime_repo)
    final_tree = _tree_snapshot(runtime_repo, final_ids)
    final_trials = _trial_snapshot(runtime_repo, final_ids)
    replay = run_resumable_seeded_generated_goal_round(
        runtime_repo,
        "generated-goal-multi-round-2",
        seed=f"{seed}:round-2",
    )
    assert replay["passed"] is True
    assert replay["phases"]["learn_commit"]["attempts"] == 1
    assert _all_candidate_ids(runtime_repo) == final_ids
    assert _tree_snapshot(runtime_repo, final_ids) == final_tree
    assert _trial_snapshot(runtime_repo, final_ids) == final_trials
