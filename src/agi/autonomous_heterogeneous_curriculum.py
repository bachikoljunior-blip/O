from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import execute_program
from agi.autonomous_heterogeneous_goal_acquisition import (
    AutonomousHeterogeneousGoalError,
    _classify_goals,
    _goal_specs,
    _library_attempt,
    run_autonomous_heterogeneous_goal_acquisition,
)
from agi.durable_state_rehydration import _run_one, _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.transactional_multisession_commit import _commit_verified_candidate
from agi.verified_macro_library_discovery import synthesize_from_verified_macro_library


class AutonomousHeterogeneousCurriculumError(RuntimeError):
    pass


def _assert_candidate_state(
    root: Path,
    candidate_ids: list[str],
    tree: dict[str, str],
    trials: dict[str, dict[str, str]],
    *,
    label: str,
) -> None:
    if _tree_snapshot(root, candidate_ids) != tree:
        raise AutonomousHeterogeneousCurriculumError(
            f"{label} changed protected Candidate bytes"
        )
    if _trial_snapshot(root, candidate_ids) != trials:
        raise AutonomousHeterogeneousCurriculumError(
            f"{label} changed protected Candidate trial ledgers"
        )


def run_autonomous_heterogeneous_curriculum(root: Path, seed: str) -> dict[str, Any]:
    """Autonomously close two distinct heterogeneous durable gaps across fresh sessions.

    Round one delegates to the already verified one-gap milestone. Round two reconstructs only durable
    state into a fresh decision root, reclassifies the same heterogeneous behavior set without caller-
    supplied Candidate IDs, and chooses from the *currently* unsupported set. The selected second gap is
    learned in another fresh state-only root through bounded synthesis plus exact-scope protected
    regression, then transactionally committed without mutating any pre-existing Candidate bytes or
    trials. A final fresh resolver must rediscover and execute both autonomously acquired skills, while
    the unsupported-goal count decreases monotonically.

    This is bounded repository-authored internal development evidence. It tests iterative autonomy,
    durable capability accumulation, restart transfer, and no-forgetting; it is not independent
    production evidence and cannot establish AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    first = run_autonomous_heterogeneous_goal_acquisition(root, f"{seed}:round1")
    if not first.get("passed"):
        raise AutonomousHeterogeneousCurriculumError("round one did not pass")
    if int(first.get("remaining_unsupported_count", 0)) < 1:
        raise AutonomousHeterogeneousCurriculumError(
            "round one did not preserve a falsifiable unsupported goal"
        )

    specs = _goal_specs()
    specs_by_name = {str(spec["name"]): spec for spec in specs}
    first_goal = str(first["selected_goal"])
    first_candidate_id = str(first["selected_goal_candidate_id"])

    protected_ids = _all_candidate_ids(root)
    protected_tree = _tree_snapshot(root, protected_ids)
    protected_trials = _trial_snapshot(root, protected_ids)

    with tempfile.TemporaryDirectory(prefix="agi-autonomous-curriculum-decision-") as temporary:
        decision_root = Path(temporary).resolve()
        _copy_persistent_state(root, decision_root)
        _assert_candidate_state(
            decision_root,
            protected_ids,
            protected_tree,
            protected_trials,
            label="round-two decision restart",
        )
        second_initial_classification = _classify_goals(decision_root, specs)
        if any(
            item.get("candidate_ids_supplied_by_caller") is True
            for item in second_initial_classification
        ):
            raise AutonomousHeterogeneousCurriculumError(
                "round-two classification relied on caller Candidate IDs"
            )
        second_unsupported = sorted(
            str(item["goal"])
            for item in second_initial_classification
            if not item["supported"]
        )
        if not second_unsupported:
            raise AutonomousHeterogeneousCurriculumError(
                "round two had no unsupported goal to select"
            )
        if first_goal in second_unsupported:
            raise AutonomousHeterogeneousCurriculumError(
                "round-one acquired goal was forgotten before round two"
            )
        if set(second_unsupported) != set(first["remaining_unsupported_goals"]):
            raise AutonomousHeterogeneousCurriculumError(
                "fresh round-two unsupported set diverged from round-one durable result"
            )

    second_selection_commitment = _digest(
        {
            "seed": seed,
            "phase": "heterogeneous-unsupported-goal-choice-v2-round2",
            "unsupported_goals": second_unsupported,
            "classification": second_initial_classification,
        }
    )
    second_goal = second_unsupported[
        int(second_selection_commitment[:8], 16) % len(second_unsupported)
    ]
    if second_goal == first_goal:
        raise AutonomousHeterogeneousCurriculumError(
            "round two reselected the already acquired goal"
        )
    second_spec = specs_by_name[second_goal]

    with tempfile.TemporaryDirectory(prefix="agi-autonomous-curriculum-learner-") as temporary:
        learner_root = Path(temporary).resolve()
        _copy_persistent_state(root, learner_root)
        _assert_candidate_state(
            learner_root,
            protected_ids,
            protected_tree,
            protected_trials,
            label="round-two learner restart",
        )
        prelearning = _library_attempt(learner_root, second_spec)
        if prelearning["supported"]:
            raise AutonomousHeterogeneousCurriculumError(
                "round-two selected goal was supported before learning"
            )

        acquired = _promote_task(
            learner_root,
            seed=f"{seed}:round2:selected-goal:{second_goal}",
            spec=second_spec,
        )
        second_candidate_id = str(acquired["candidate_id"])
        second_scope = str(acquired["scope"])
        if second_candidate_id in protected_ids:
            raise AutonomousHeterogeneousCurriculumError(
                "round-two learning reused an existing Candidate identity"
            )
        if not acquired.get("negative_evidence_retained"):
            raise AutonomousHeterogeneousCurriculumError(
                "round-two Candidate did not retain protected negative evidence"
            )
        second_candidate_trials = _trial_snapshot(learner_root, [second_candidate_id])
        if not second_candidate_trials.get(second_candidate_id):
            raise AutonomousHeterogeneousCurriculumError(
                "round-two Candidate lacks durable regression trials"
            )
        _assert_candidate_state(
            learner_root,
            protected_ids,
            protected_tree,
            protected_trials,
            label="round-two learning",
        )
        second_commit = _commit_verified_candidate(
            learner_root,
            root,
            candidate_id=second_candidate_id,
            scope=second_scope,
        )

    _assert_candidate_state(
        root,
        protected_ids,
        protected_tree,
        protected_trials,
        label="round-two transactional commit",
    )

    all_ids = [*protected_ids, second_candidate_id]
    all_tree = _tree_snapshot(root, all_ids)
    all_trials = _trial_snapshot(root, all_ids)
    if not all_trials.get(second_candidate_id):
        raise AutonomousHeterogeneousCurriculumError(
            "round-two committed Candidate lost its trial ledger"
        )

    selected_pairs = (
        (first_goal, first_candidate_id),
        (second_goal, second_candidate_id),
    )
    retained_challenges: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="agi-autonomous-curriculum-resolver-") as temporary:
        resolver_root = Path(temporary).resolve()
        _copy_persistent_state(root, resolver_root)
        _assert_candidate_state(
            resolver_root,
            all_ids,
            all_tree,
            all_trials,
            label="final resolver restart",
        )

        final_classification = _classify_goals(resolver_root, specs)
        if any(
            item.get("candidate_ids_supplied_by_caller") is True
            for item in final_classification
        ):
            raise AutonomousHeterogeneousCurriculumError(
                "final classification relied on caller Candidate IDs"
            )
        final_by_goal = {str(item["goal"]): item for item in final_classification}
        for goal, candidate_id in selected_pairs:
            if not final_by_goal[goal]["supported"]:
                raise AutonomousHeterogeneousCurriculumError(
                    f"autonomously acquired goal was forgotten: {goal}"
                )
            spec = specs_by_name[goal]
            result = synthesize_from_verified_macro_library(
                resolver_root,
                input_domain=str(spec["input_domain"]),
                output_domain=str(spec["output_domain"]),
                examples=spec["examples"],
                max_depth=3,
                max_candidates=64,
            )
            if result["candidate_ids_supplied_by_caller"]:
                raise AutonomousHeterogeneousCurriculumError(
                    f"retained goal resolution relied on caller IDs: {goal}"
                )
            selected_ids = [str(value) for value in result["selected_candidate_ids"]]
            if candidate_id not in selected_ids:
                raise AutonomousHeterogeneousCurriculumError(
                    f"retained goal did not rediscover its acquired Candidate: {goal}"
                )
            if execute_program(result["compiled_program"], spec["challenge"]) != spec[
                "challenge_expected"
            ]:
                raise AutonomousHeterogeneousCurriculumError(
                    f"retained goal failed fresh challenge: {goal}"
                )
            fresh_run = _run_one(
                resolver_root,
                candidate_id=candidate_id,
                value=spec["challenge"],
                expected=spec["challenge_expected"],
            )
            retained_challenges.append(
                {
                    "goal": goal,
                    "candidate_id": candidate_id,
                    "selected_candidate_ids": selected_ids,
                    "selected_chain_depth": int(result["selected_chain_depth"]),
                    "challenge_sha256": _digest(spec["challenge"]),
                    "fresh_engine_run": fresh_run,
                }
            )

        final_unsupported = sorted(
            str(item["goal"])
            for item in final_classification
            if not item["supported"]
        )
        if second_goal in final_unsupported or first_goal in final_unsupported:
            raise AutonomousHeterogeneousCurriculumError(
                "final unsupported set contains an acquired goal"
            )
        if len(final_unsupported) >= len(second_unsupported):
            raise AutonomousHeterogeneousCurriculumError(
                "second autonomous acquisition did not reduce unsupported-goal count"
            )
        _assert_candidate_state(
            resolver_root,
            all_ids,
            all_tree,
            all_trials,
            label="final retained-skill replay",
        )

    _assert_candidate_state(
        root,
        all_ids,
        all_tree,
        all_trials,
        label="source after final replay",
    )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "autonomous-heterogeneous-curriculum-v1",
        "round1_report_digest": str(first["digest"]),
        "round1_selected_goal": first_goal,
        "round1_selected_candidate_id": first_candidate_id,
        "round1_initial_unsupported_count": int(first["initial_unsupported_count"]),
        "round1_remaining_unsupported_goals": list(first["remaining_unsupported_goals"]),
        "round1_remaining_unsupported_count": int(first["remaining_unsupported_count"]),
        "round2_initial_classification": second_initial_classification,
        "round2_initial_unsupported_goals": second_unsupported,
        "round2_initial_unsupported_count": len(second_unsupported),
        "round2_selection_commitment": second_selection_commitment,
        "round2_selected_goal": second_goal,
        "round2_selected_from_unsupported_only": second_goal in second_unsupported,
        "round2_selected_candidate_id": second_candidate_id,
        "round2_selected_scope": second_scope,
        "round2_negative_evidence_retained": bool(acquired["negative_evidence_retained"]),
        "round2_transactional_commit": second_commit,
        "selected_goals_distinct": first_goal != second_goal,
        "selected_candidate_ids_distinct": first_candidate_id != second_candidate_id,
        "caller_supplied_candidate_ids": False,
        "final_classification": final_classification,
        "final_unsupported_goals": final_unsupported,
        "final_unsupported_count": len(final_unsupported),
        "unsupported_count_strictly_decreased": len(final_unsupported) < len(second_unsupported),
        "retained_challenges": retained_challenges,
        "protected_candidate_count_before_round2": len(protected_ids),
        "all_candidate_count_after_round2": len(all_ids),
        "protected_state_unchanged": _tree_snapshot(root, protected_ids) == protected_tree,
        "protected_trials_unchanged": _trial_snapshot(root, protected_ids) == protected_trials,
        "all_state_unchanged_after_fresh_replay": _tree_snapshot(root, all_ids) == all_tree,
        "all_trials_unchanged_after_fresh_replay": _trial_snapshot(root, all_ids) == all_trials,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded autonomous curriculum evidence only. Across fresh state-only sessions, the "
            "system classified a repository-authored heterogeneous goal set from its durable verified "
            "library, selected and closed two distinct fail-closed gaps without caller Candidate IDs, "
            "retained both skills across another restart, and preserved Candidate/trial state. The goals, "
            "selection commitments, synthesis grammar, regression, search, and evaluator remain "
            "repository-authored; this is not independent production evidence and does not establish AGI."
        ),
    }
    if not all(
        (
            report["round2_selected_from_unsupported_only"],
            report["round2_negative_evidence_retained"],
            report["selected_goals_distinct"],
            report["selected_candidate_ids_distinct"],
            report["unsupported_count_strictly_decreased"],
            report["protected_state_unchanged"],
            report["protected_trials_unchanged"],
            report["all_state_unchanged_after_fresh_replay"],
            report["all_trials_unchanged_after_fresh_replay"],
        )
    ):
        raise AutonomousHeterogeneousCurriculumError(
            "autonomous heterogeneous curriculum aggregate invariant failed"
        )

    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "autonomous-heterogeneous-curriculum"
        / f"curriculum-{_digest(seed)[:16]}.json",
        report,
    )
    return report
