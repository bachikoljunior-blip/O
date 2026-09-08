from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from agi.acquired_programs import execute_program
from agi.autonomous_heterogeneous_goal_acquisition import _classify_goals
from agi.autonomous_heterogeneous_goal_exhaustion import (
    run_autonomous_heterogeneous_goal_exhaustion,
)
from agi.durable_state_rehydration import _tree_snapshot
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.resumable_seeded_generated_goal_round import (
    SimulatedGeneratedGoalInterruption,
    _receipt_path,
    run_resumable_seeded_generated_goal_round,
)
from agi.seeded_generated_goal_acquisition import _generated_goal_specs
from agi.verified_macro_library_discovery import synthesize_from_verified_macro_library


def test_two_recovered_generated_skills_both_work_after_one_final_state_only_restart(
    runtime_repo: Path,
) -> None:
    seed = "functional-generated-skill-no-forgetting-seed"
    prerequisite = run_autonomous_heterogeneous_goal_exhaustion(
        runtime_repo,
        seed + ":fixed-prerequisite",
    )
    assert prerequisite["passed"] is True
    assert prerequisite["bounded_goal_set_exhausted"] is True

    learned: list[dict[str, str]] = []
    for index in (1, 2):
        round_seed = f"{seed}:round-{index}"
        campaign_id = f"functional-generated-skill-round-{index}"
        selected = run_resumable_seeded_generated_goal_round(
            runtime_repo,
            campaign_id,
            seed=round_seed,
            max_new_phases=1,
        )
        selected_goal = str(selected["phases"]["select"]["metadata"]["selected_goal"])

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
        reconciled = run_resumable_seeded_generated_goal_round(
            runtime_repo,
            campaign_id,
            seed=round_seed,
            max_new_phases=1,
        )
        assert reconciled["last_reconciled_count"] == 1
        assert reconciled["phases"]["learn_commit"]["attempts"] == 1

        finished = run_resumable_seeded_generated_goal_round(
            runtime_repo,
            campaign_id,
            seed=round_seed,
        )
        assert finished["passed"] is True
        learned.append(
            {
                "seed": round_seed,
                "selected_goal": selected_goal,
                "candidate_id": candidate_id,
            }
        )

    assert learned[0]["candidate_id"] != learned[1]["candidate_id"]
    all_ids = _all_candidate_ids(runtime_repo)
    source_tree = _tree_snapshot(runtime_repo, all_ids)
    source_trials = _trial_snapshot(runtime_repo, all_ids)

    with tempfile.TemporaryDirectory(prefix="agi-generated-functional-retention-") as temporary:
        resolver_root = Path(temporary).resolve()
        _copy_persistent_state(runtime_repo, resolver_root)
        assert _tree_snapshot(resolver_root, all_ids) == source_tree
        assert _trial_snapshot(resolver_root, all_ids) == source_trials

        for item in learned:
            spec = next(
                spec
                for spec in _generated_goal_specs(item["seed"])
                if spec["name"] == item["selected_goal"]
            )
            result = synthesize_from_verified_macro_library(
                resolver_root,
                input_domain=str(spec["input_domain"]),
                output_domain=str(spec["output_domain"]),
                examples=spec["examples"],
                max_depth=3,
                max_candidates=64,
            )
            assert result["candidate_ids_supplied_by_caller"] is False
            assert item["candidate_id"] in result["selected_candidate_ids"]
            assert execute_program(result["compiled_program"], spec["challenge"]) == spec[
                "challenge_expected"
            ]

            classification = _classify_goals(
                resolver_root,
                _generated_goal_specs(item["seed"]),
            )
            by_goal = {str(entry["goal"]): entry for entry in classification}
            assert by_goal[item["selected_goal"]]["supported"] is True
            assert not any(
                entry.get("candidate_ids_supplied_by_caller") is True
                for entry in classification
            )

        unrelated = _classify_goals(
            resolver_root,
            _generated_goal_specs(seed + ":unrelated-fresh-gap"),
        )
        assert not any(
            entry.get("candidate_ids_supplied_by_caller") is True
            for entry in unrelated
        )
        assert any(entry["supported"] is False for entry in unrelated)
        assert _tree_snapshot(resolver_root, all_ids) == source_tree
        assert _trial_snapshot(resolver_root, all_ids) == source_trials

    assert _tree_snapshot(runtime_repo, all_ids) == source_tree
    assert _trial_snapshot(runtime_repo, all_ids) == source_trials
