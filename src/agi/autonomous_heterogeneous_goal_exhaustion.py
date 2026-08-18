from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import execute_program
from agi.autonomous_heterogeneous_curriculum import (
    run_autonomous_heterogeneous_curriculum,
)
from agi.autonomous_heterogeneous_goal_acquisition import (
    _classify_goals,
    _goal_specs,
    _library_attempt,
)
from agi.durable_state_rehydration import _run_one, _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.transactional_multisession_commit import _commit_verified_candidate
from agi.verified_macro_library_discovery import synthesize_from_verified_macro_library


class AutonomousHeterogeneousExhaustionError(RuntimeError):
    pass


def _assert_state(
    root: Path,
    candidate_ids: list[str],
    tree: dict[str, str],
    trials: dict[str, dict[str, str]],
    *,
    label: str,
) -> None:
    if _tree_snapshot(root, candidate_ids) != tree:
        raise AutonomousHeterogeneousExhaustionError(
            f"{label} changed Candidate bytes"
        )
    if _trial_snapshot(root, candidate_ids) != trials:
        raise AutonomousHeterogeneousExhaustionError(
            f"{label} changed Candidate trial ledgers"
        )


def run_autonomous_heterogeneous_goal_exhaustion(
    root: Path,
    seed: str,
) -> dict[str, Any]:
    """Continue autonomous durable learning until the bounded heterogeneous goal set is exhausted.

    The first two rounds are provided by the multi-round autonomous curriculum. A fresh state-only
    decision root then reclassifies the same repository-authored string/sequence/object goal set using
    only the verified durable library and no caller Candidate IDs. If any unsupported behavior remains,
    one additional fresh learner selects from exactly that observed unsupported set, bounded-synthesizes
    and protected-regression-verifies the missing skill, and transactionally commits it without changing
    any earlier Candidate bytes or trials. A final fresh resolver must report zero unsupported goals and
    replay every autonomously acquired goal on a held-back challenge without spending new learning trials.

    This is internal bounded closed-loop curriculum evidence only. It does not generalize the goal
    generator or evaluator and is not independent production evidence for an AGI claim.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    first_two = run_autonomous_heterogeneous_curriculum(
        root,
        f"{seed}:first-two-rounds",
    )
    if not first_two.get("passed"):
        raise AutonomousHeterogeneousExhaustionError(
            "first two autonomous curriculum rounds did not pass"
        )

    specs = _goal_specs()
    specs_by_name = {str(spec["name"]): spec for spec in specs}
    selected_goals = [
        str(first_two["round1_selected_goal"]),
        str(first_two["round2_selected_goal"]),
    ]
    selected_candidates = [
        str(first_two["round1_selected_candidate_id"]),
        str(first_two["round2_selected_candidate_id"]),
    ]
    if len(set(selected_goals)) != 2 or len(set(selected_candidates)) != 2:
        raise AutonomousHeterogeneousExhaustionError(
            "first two rounds did not retain distinct autonomous acquisitions"
        )

    protected_ids = _all_candidate_ids(root)
    protected_tree = _tree_snapshot(root, protected_ids)
    protected_trials = _trial_snapshot(root, protected_ids)

    with tempfile.TemporaryDirectory(prefix="agi-heterogeneous-exhaustion-decision-") as temporary:
        decision_root = Path(temporary).resolve()
        _copy_persistent_state(root, decision_root)
        _assert_state(
            decision_root,
            protected_ids,
            protected_tree,
            protected_trials,
            label="exhaustion decision restart",
        )
        pre_exhaustion_classification = _classify_goals(decision_root, specs)
        if any(
            item.get("candidate_ids_supplied_by_caller") is True
            for item in pre_exhaustion_classification
        ):
            raise AutonomousHeterogeneousExhaustionError(
                "exhaustion classification relied on caller Candidate IDs"
            )
        remaining = sorted(
            str(item["goal"])
            for item in pre_exhaustion_classification
            if not item["supported"]
        )
        if set(remaining) != set(first_two["final_unsupported_goals"]):
            raise AutonomousHeterogeneousExhaustionError(
                "fresh exhaustion restart diverged from prior durable unsupported set"
            )
        if any(goal in remaining for goal in selected_goals):
            raise AutonomousHeterogeneousExhaustionError(
                "an earlier autonomous acquisition was forgotten before exhaustion"
            )

    additional_round_executed = bool(remaining)
    additional_goal: str | None = None
    additional_candidate_id: str | None = None
    additional_scope: str | None = None
    additional_commit: dict[str, Any] | None = None
    additional_negative_evidence_retained = False
    selection_commitment: str | None = None

    if remaining:
        selection_commitment = _digest(
            {
                "seed": seed,
                "phase": "heterogeneous-goal-exhaustion-next-choice-v1",
                "remaining_unsupported_goals": remaining,
                "classification": pre_exhaustion_classification,
            }
        )
        additional_goal = remaining[
            int(selection_commitment[:8], 16) % len(remaining)
        ]
        if additional_goal in selected_goals:
            raise AutonomousHeterogeneousExhaustionError(
                "exhaustion round reselected an already acquired goal"
            )
        spec = specs_by_name[additional_goal]

        with tempfile.TemporaryDirectory(prefix="agi-heterogeneous-exhaustion-learner-") as temporary:
            learner_root = Path(temporary).resolve()
            _copy_persistent_state(root, learner_root)
            _assert_state(
                learner_root,
                protected_ids,
                protected_tree,
                protected_trials,
                label="exhaustion learner restart",
            )
            if _library_attempt(learner_root, spec)["supported"]:
                raise AutonomousHeterogeneousExhaustionError(
                    "selected exhaustion goal was supported before learning"
                )
            acquired = _promote_task(
                learner_root,
                seed=f"{seed}:exhaustion:{additional_goal}",
                spec=spec,
            )
            additional_candidate_id = str(acquired["candidate_id"])
            additional_scope = str(acquired["scope"])
            if additional_candidate_id in protected_ids:
                raise AutonomousHeterogeneousExhaustionError(
                    "exhaustion learning reused an existing Candidate identity"
                )
            additional_negative_evidence_retained = bool(
                acquired.get("negative_evidence_retained")
            )
            if not additional_negative_evidence_retained:
                raise AutonomousHeterogeneousExhaustionError(
                    "exhaustion Candidate did not retain protected negative evidence"
                )
            if not _trial_snapshot(learner_root, [additional_candidate_id]).get(
                additional_candidate_id
            ):
                raise AutonomousHeterogeneousExhaustionError(
                    "exhaustion Candidate lacks durable regression trials"
                )
            _assert_state(
                learner_root,
                protected_ids,
                protected_tree,
                protected_trials,
                label="exhaustion learning",
            )
            additional_commit = _commit_verified_candidate(
                learner_root,
                root,
                candidate_id=additional_candidate_id,
                scope=additional_scope,
            )

        _assert_state(
            root,
            protected_ids,
            protected_tree,
            protected_trials,
            label="exhaustion transactional commit",
        )
        selected_goals.append(additional_goal)
        selected_candidates.append(additional_candidate_id)

    all_ids = _all_candidate_ids(root)
    all_tree = _tree_snapshot(root, all_ids)
    all_trials = _trial_snapshot(root, all_ids)
    retained_challenges: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="agi-heterogeneous-exhaustion-final-") as temporary:
        final_root = Path(temporary).resolve()
        _copy_persistent_state(root, final_root)
        _assert_state(
            final_root,
            all_ids,
            all_tree,
            all_trials,
            label="final exhaustion restart",
        )
        final_classification = _classify_goals(final_root, specs)
        if any(
            item.get("candidate_ids_supplied_by_caller") is True
            for item in final_classification
        ):
            raise AutonomousHeterogeneousExhaustionError(
                "final exhaustion classification relied on caller Candidate IDs"
            )
        final_unsupported = sorted(
            str(item["goal"])
            for item in final_classification
            if not item["supported"]
        )
        if final_unsupported:
            raise AutonomousHeterogeneousExhaustionError(
                "bounded autonomous curriculum did not exhaust the heterogeneous goal set"
            )

        for goal, candidate_id in zip(selected_goals, selected_candidates, strict=True):
            spec = specs_by_name[goal]
            result = synthesize_from_verified_macro_library(
                final_root,
                input_domain=str(spec["input_domain"]),
                output_domain=str(spec["output_domain"]),
                examples=spec["examples"],
                max_depth=3,
                max_candidates=64,
            )
            if result["candidate_ids_supplied_by_caller"]:
                raise AutonomousHeterogeneousExhaustionError(
                    f"retained exhaustion replay relied on caller IDs: {goal}"
                )
            selected_ids = [str(value) for value in result["selected_candidate_ids"]]
            if candidate_id not in selected_ids:
                raise AutonomousHeterogeneousExhaustionError(
                    f"retained exhaustion replay omitted acquired Candidate: {goal}"
                )
            if execute_program(result["compiled_program"], spec["challenge"]) != spec[
                "challenge_expected"
            ]:
                raise AutonomousHeterogeneousExhaustionError(
                    f"retained exhaustion skill failed fresh challenge: {goal}"
                )
            retained_challenges.append(
                {
                    "goal": goal,
                    "candidate_id": candidate_id,
                    "selected_candidate_ids": selected_ids,
                    "selected_chain_depth": int(result["selected_chain_depth"]),
                    "fresh_engine_run": _run_one(
                        final_root,
                        candidate_id=candidate_id,
                        value=spec["challenge"],
                        expected=spec["challenge_expected"],
                    ),
                    "challenge_sha256": _digest(spec["challenge"]),
                }
            )
        _assert_state(
            final_root,
            all_ids,
            all_tree,
            all_trials,
            label="final exhaustion replay",
        )

    _assert_state(
        root,
        all_ids,
        all_tree,
        all_trials,
        label="source after exhaustion replay",
    )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "autonomous-heterogeneous-goal-exhaustion-v1",
        "first_two_rounds_digest": str(first_two["digest"]),
        "pre_exhaustion_classification": pre_exhaustion_classification,
        "pre_exhaustion_unsupported_goals": remaining,
        "pre_exhaustion_unsupported_count": len(remaining),
        "additional_round_executed": additional_round_executed,
        "additional_selection_commitment": selection_commitment,
        "additional_selected_goal": additional_goal,
        "additional_selected_candidate_id": additional_candidate_id,
        "additional_selected_scope": additional_scope,
        "additional_negative_evidence_retained": additional_negative_evidence_retained,
        "additional_transactional_commit": additional_commit,
        "autonomous_round_count": 2 + int(additional_round_executed),
        "autonomously_selected_goals": selected_goals,
        "autonomously_selected_candidate_ids": selected_candidates,
        "selected_goals_distinct": len(set(selected_goals)) == len(selected_goals),
        "selected_candidate_ids_distinct": len(set(selected_candidates))
        == len(selected_candidates),
        "caller_supplied_candidate_ids": False,
        "final_classification": final_classification,
        "final_unsupported_goals": final_unsupported,
        "final_unsupported_count": len(final_unsupported),
        "bounded_goal_set_exhausted": len(final_unsupported) == 0,
        "retained_challenges": retained_challenges,
        "protected_state_unchanged": _tree_snapshot(root, protected_ids) == protected_tree,
        "protected_trials_unchanged": _trial_snapshot(root, protected_ids) == protected_trials,
        "all_state_unchanged_after_fresh_replay": _tree_snapshot(root, all_ids) == all_tree,
        "all_trials_unchanged_after_fresh_replay": _trial_snapshot(root, all_ids) == all_trials,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded closed-loop curriculum evidence only. The system repeatedly classified a "
            "repository-authored heterogeneous goal set against its durable verified library, acquired "
            "remaining fail-closed skills without caller Candidate IDs, exhausted that fixed bounded set, "
            "and replayed retained gains after restart without changing learning evidence. The goal set, "
            "selection rule, examples, synthesis grammar, regression, search, scoring, and evaluator are "
            "not independent production evidence and do not establish AGI."
        ),
    }
    if not all(
        (
            report["selected_goals_distinct"],
            report["selected_candidate_ids_distinct"],
            report["bounded_goal_set_exhausted"],
            report["protected_state_unchanged"],
            report["protected_trials_unchanged"],
            report["all_state_unchanged_after_fresh_replay"],
            report["all_trials_unchanged_after_fresh_replay"],
        )
    ):
        raise AutonomousHeterogeneousExhaustionError(
            "autonomous heterogeneous exhaustion aggregate invariant failed"
        )

    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "autonomous-heterogeneous-goal-exhaustion"
        / f"exhaustion-{_digest(seed)[:16]}.json",
        report,
    )
    return report
