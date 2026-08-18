from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.autonomous_heterogeneous_goal_acquisition import (
    _classify_goals,
    _goal_specs,
    _library_attempt,
)
from agi.durable_state_rehydration import _run_one, _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.transactional_multisession_commit import (
    _commit_verified_candidate,
    run_transactional_multisession_commit,
)
from agi.verified_macro_library_discovery import synthesize_from_verified_macro_library


class IterativeHeterogeneousGoalError(RuntimeError):
    pass


def _require_state_unchanged(
    root: Path,
    candidate_ids: list[str],
    tree_before: dict[str, str],
    trials_before: dict[str, dict[str, str]],
    *,
    label: str,
) -> None:
    if _tree_snapshot(root, candidate_ids) != tree_before:
        raise IterativeHeterogeneousGoalError(
            f"{label} changed protected Candidate bytes"
        )
    if _trial_snapshot(root, candidate_ids) != trials_before:
        raise IterativeHeterogeneousGoalError(
            f"{label} changed protected Candidate trial ledgers"
        )


def run_iterative_heterogeneous_goal_curriculum(root: Path, seed: str) -> dict[str, Any]:
    """Repeatedly discover and close heterogeneous durable-library gaps until the bounded set is solved.

    The curriculum begins from the transactionally retained capability base and the same string, sequence,
    and object behavior goals used by the one-gap autonomous acquisition milestone. Every round starts from
    a fresh Candidate/system-only decision root, discovers support using the exact-scope verified macro
    library without caller Candidate IDs, and chooses one currently unsupported goal using a commitment
    over the observed gap set and round number. The chosen behavior is learned in a separate fresh learner,
    protected by exact-scope regression, and transactionally admitted to the durable root.

    After each round, another fresh resolver must rediscover every previously acquired goal skill directly,
    while every Candidate byte tree and trial ledger that existed before the round remains unchanged. The
    loop continues until no goal in the bounded heterogeneous set fails closed. A final fresh state-only
    root must behavior-select every acquired goal without caller IDs and execute disjoint post-selection
    challenges through distinct fresh Engines without spending more Candidate trials.

    This is internal bounded multi-round continual self-improvement evidence. The goal set, choice rule,
    synthesis grammar, examples, regression, search, and evaluator remain repository-authored, so it is not
    open-world autonomy or independent production AGI evidence.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    base = run_transactional_multisession_commit(
        root,
        f"{seed}:iterative-heterogeneous-base",
    )
    if not base.get("passed"):
        raise IterativeHeterogeneousGoalError(
            "transactional capability base did not pass"
        )

    specs = _goal_specs()
    specs_by_name = {str(spec["name"]): spec for spec in specs}
    base_ids = _all_candidate_ids(root)
    base_tree = _tree_snapshot(root, base_ids)
    base_trials = _trial_snapshot(root, base_ids)

    with tempfile.TemporaryDirectory(prefix="agi-iterative-goal-initial-") as temporary:
        initial_root = Path(temporary).resolve()
        _copy_persistent_state(root, initial_root)
        _require_state_unchanged(
            initial_root,
            base_ids,
            base_tree,
            base_trials,
            label="initial goal-classification restart",
        )
        initial_classification = _classify_goals(initial_root, specs)
    initial_unsupported = sorted(
        str(item["goal"])
        for item in initial_classification
        if not item["supported"]
    )
    if len(initial_unsupported) < 2:
        raise IterativeHeterogeneousGoalError(
            "iterative goal set did not expose multiple initial capability gaps"
        )

    acquired_by_goal: dict[str, str] = {}
    scopes_by_goal: dict[str, str] = {}
    rounds: list[dict[str, Any]] = []
    max_rounds = len(specs)

    for round_index in range(max_rounds):
        round_prior_ids = _all_candidate_ids(root)
        round_prior_tree = _tree_snapshot(root, round_prior_ids)
        round_prior_trials = _trial_snapshot(root, round_prior_ids)

        with tempfile.TemporaryDirectory(
            prefix=f"agi-iterative-goal-decision-{round_index}-"
        ) as temporary:
            decision_root = Path(temporary).resolve()
            _copy_persistent_state(root, decision_root)
            _require_state_unchanged(
                decision_root,
                round_prior_ids,
                round_prior_tree,
                round_prior_trials,
                label=f"round {round_index} decision restart",
            )
            classification = _classify_goals(decision_root, specs)
            by_goal = {str(item["goal"]): item for item in classification}
            for goal_name, candidate_id in acquired_by_goal.items():
                item = by_goal[goal_name]
                if not item["supported"]:
                    raise IterativeHeterogeneousGoalError(
                        f"round {round_index} forgot previously acquired goal {goal_name}"
                    )
                if item.get("candidate_ids_supplied_by_caller") is not False:
                    raise IterativeHeterogeneousGoalError(
                        "previous-goal retrieval relied on caller Candidate IDs"
                    )
                if item.get("selected_candidate_ids") != [candidate_id]:
                    raise IterativeHeterogeneousGoalError(
                        f"round {round_index} did not directly rediscover acquired goal {goal_name}"
                    )

            unsupported = sorted(
                str(item["goal"])
                for item in classification
                if not item["supported"]
            )
            if not unsupported:
                break
            selection_commitment = _digest(
                {
                    "seed": seed,
                    "round_index": round_index,
                    "phase": "iterative-heterogeneous-gap-choice-v1",
                    "unsupported_goals": unsupported,
                    "classification": classification,
                }
            )
            selected_name = unsupported[
                int(selection_commitment[:8], 16) % len(unsupported)
            ]

        selected_spec = specs_by_name[selected_name]
        with tempfile.TemporaryDirectory(
            prefix=f"agi-iterative-goal-learner-{round_index}-"
        ) as temporary:
            learner_root = Path(temporary).resolve()
            _copy_persistent_state(root, learner_root)
            _require_state_unchanged(
                learner_root,
                round_prior_ids,
                round_prior_tree,
                round_prior_trials,
                label=f"round {round_index} learner restart",
            )
            if _library_attempt(learner_root, selected_spec)["supported"]:
                raise IterativeHeterogeneousGoalError(
                    "selected goal became supported before learning"
                )

            acquired = _promote_task(
                learner_root,
                seed=f"{seed}:round:{round_index}:goal:{selected_name}",
                spec=selected_spec,
            )
            candidate_id = str(acquired["candidate_id"])
            scope = str(acquired["scope"])
            if candidate_id in round_prior_ids:
                raise IterativeHeterogeneousGoalError(
                    "iterative acquisition reused a prior Candidate identity"
                )
            if not acquired.get("negative_evidence_retained"):
                raise IterativeHeterogeneousGoalError(
                    "iterative acquisition did not retain protected negative evidence"
                )
            candidate_trials = _trial_snapshot(learner_root, [candidate_id])
            if not candidate_trials.get(candidate_id):
                raise IterativeHeterogeneousGoalError(
                    "iterative acquisition lacks durable Candidate trials"
                )
            _require_state_unchanged(
                learner_root,
                round_prior_ids,
                round_prior_tree,
                round_prior_trials,
                label=f"round {round_index} learning",
            )
            commit = _commit_verified_candidate(
                learner_root,
                root,
                candidate_id=candidate_id,
                scope=scope,
            )

        _require_state_unchanged(
            root,
            round_prior_ids,
            round_prior_tree,
            round_prior_trials,
            label=f"round {round_index} transactional commit",
        )
        acquired_by_goal[selected_name] = candidate_id
        scopes_by_goal[selected_name] = scope

        enlarged_ids = _all_candidate_ids(root)
        enlarged_tree = _tree_snapshot(root, enlarged_ids)
        enlarged_trials = _trial_snapshot(root, enlarged_ids)
        with tempfile.TemporaryDirectory(
            prefix=f"agi-iterative-goal-postcommit-{round_index}-"
        ) as temporary:
            verifier_root = Path(temporary).resolve()
            _copy_persistent_state(root, verifier_root)
            _require_state_unchanged(
                verifier_root,
                enlarged_ids,
                enlarged_tree,
                enlarged_trials,
                label=f"round {round_index} post-commit restart",
            )
            post = _classify_goals(verifier_root, specs)
            post_by_goal = {str(item["goal"]): item for item in post}
            for goal_name, expected_id in acquired_by_goal.items():
                item = post_by_goal[goal_name]
                if not item["supported"]:
                    raise IterativeHeterogeneousGoalError(
                        f"round {round_index} did not retain goal {goal_name}"
                    )
                if item.get("candidate_ids_supplied_by_caller") is not False:
                    raise IterativeHeterogeneousGoalError(
                        "post-commit goal retrieval relied on caller Candidate IDs"
                    )
                if item.get("selected_candidate_ids") != [expected_id]:
                    raise IterativeHeterogeneousGoalError(
                        f"round {round_index} selected wrong Candidate for goal {goal_name}"
                    )
            remaining = sorted(
                str(item["goal"])
                for item in post
                if not item["supported"]
            )
            if selected_name in remaining:
                raise IterativeHeterogeneousGoalError(
                    "selected goal remained unsupported after durable learning"
                )
            if len(remaining) >= len(unsupported):
                raise IterativeHeterogeneousGoalError(
                    "round did not reduce the observed unsupported goal set"
                )

        rounds.append(
            {
                "round_index": round_index,
                "classification": classification,
                "unsupported_before": unsupported,
                "selection_commitment": selection_commitment,
                "selected_goal": selected_name,
                "candidate_id": candidate_id,
                "scope": scope,
                "negative_evidence_retained": bool(acquired["negative_evidence_retained"]),
                "transactional_commit": commit,
                "unsupported_after": remaining,
                "protected_candidate_count": len(round_prior_ids),
            }
        )

    if len(rounds) < 2:
        raise IterativeHeterogeneousGoalError(
            "iterative curriculum completed without multiple acquisition rounds"
        )

    final_ids = _all_candidate_ids(root)
    final_tree = _tree_snapshot(root, final_ids)
    final_trials = _trial_snapshot(root, final_ids)
    with tempfile.TemporaryDirectory(prefix="agi-iterative-goal-final-") as temporary:
        final_root = Path(temporary).resolve()
        _copy_persistent_state(root, final_root)
        prior_runs_copied = (final_root / ".continual" / "runs").exists()
        prior_episodes_copied = (final_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (final_root / ".continual" / "evidence").exists()
        _require_state_unchanged(
            final_root,
            final_ids,
            final_tree,
            final_trials,
            label="final heterogeneous curriculum restart",
        )

        final_classification = _classify_goals(final_root, specs)
        final_by_goal = {str(item["goal"]): item for item in final_classification}
        remaining_unsupported = sorted(
            str(item["goal"])
            for item in final_classification
            if not item["supported"]
        )
        if remaining_unsupported:
            raise IterativeHeterogeneousGoalError(
                "bounded heterogeneous curriculum stopped with executable gaps"
            )
        for goal_name, candidate_id in acquired_by_goal.items():
            item = final_by_goal[goal_name]
            if item.get("candidate_ids_supplied_by_caller") is not False:
                raise IterativeHeterogeneousGoalError(
                    "final goal retrieval relied on caller Candidate IDs"
                )
            if item.get("selected_candidate_ids") != [candidate_id]:
                raise IterativeHeterogeneousGoalError(
                    f"final resolver forgot acquired goal {goal_name}"
                )

        challenge_runs: list[str] = []
        challenge_records: list[dict[str, Any]] = []
        for goal_name, candidate_id in acquired_by_goal.items():
            spec = specs_by_name[goal_name]
            run_id = _run_one(
                final_root,
                candidate_id=candidate_id,
                value=spec["challenge"],
                expected=spec["challenge_expected"],
            )
            challenge_runs.append(run_id)
            challenge_records.append(
                {
                    "goal": goal_name,
                    "candidate_id": candidate_id,
                    "input_sha256": _digest(spec["challenge"]),
                    "expected_sha256": _digest(spec["challenge_expected"]),
                    "run_id": run_id,
                }
            )
        if len(set(challenge_runs)) != len(challenge_runs):
            raise IterativeHeterogeneousGoalError(
                "final heterogeneous challenges reused a fresh Engine run"
            )
        final_state_unchanged = _tree_snapshot(final_root, final_ids) == final_tree
        final_trials_unchanged = _trial_snapshot(final_root, final_ids) == final_trials
        if not final_state_unchanged or not final_trials_unchanged:
            raise IterativeHeterogeneousGoalError(
                "final heterogeneous challenges changed durable learning state"
            )

    source_state_unchanged = _tree_snapshot(root, final_ids) == final_tree
    source_trials_unchanged = _trial_snapshot(root, final_ids) == final_trials
    base_candidates_preserved = all(candidate_id in final_ids for candidate_id in base_ids)
    if not source_state_unchanged or not source_trials_unchanged or not base_candidates_preserved:
        raise IterativeHeterogeneousGoalError(
            "iterative curriculum lost or changed durable source capabilities"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "iterative-heterogeneous-goal-curriculum-v1",
        "transactional_base_digest": str(base["digest"]),
        "initial_classification": initial_classification,
        "initial_unsupported_goals": initial_unsupported,
        "initial_unsupported_count": len(initial_unsupported),
        "round_count": len(rounds),
        "rounds": rounds,
        "acquired_goal_candidate_ids": dict(acquired_by_goal),
        "acquired_goal_scopes": dict(scopes_by_goal),
        "all_rounds_selected_from_observed_gaps": all(
            round_item["selected_goal"] in round_item["unsupported_before"]
            for round_item in rounds
        ),
        "all_rounds_reduced_gap_count": all(
            len(round_item["unsupported_after"]) < len(round_item["unsupported_before"])
            for round_item in rounds
        ),
        "all_negative_evidence_retained": all(
            bool(round_item["negative_evidence_retained"])
            for round_item in rounds
        ),
        "final_classification": final_classification,
        "remaining_unsupported_goals": remaining_unsupported,
        "all_bounded_goals_supported_after_curriculum": not remaining_unsupported,
        "final_challenge_records": challenge_records,
        "fresh_engine_runs_unique": len(set(challenge_runs)) == len(challenge_runs),
        "final_state_unchanged": final_state_unchanged,
        "final_trials_unchanged": final_trials_unchanged,
        "source_state_unchanged": source_state_unchanged,
        "source_trials_unchanged": source_trials_unchanged,
        "base_candidates_preserved": base_candidates_preserved,
        "prior_runs_copied": prior_runs_copied,
        "prior_episodes_copied": prior_episodes_copied,
        "prior_evidence_copied": prior_evidence_copied,
        "caller_supplied_candidate_ids": False,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded multi-round continual self-improvement evidence only. The system repeatedly "
            "discovered unsupported behaviors across string, sequence, and object goals, selected only "
            "from the currently observed gap set, learned and transactionally retained one verified skill "
            "per round, preserved earlier skills, and eventually solved the entire bounded authored set "
            "after fresh restarts. The goal distribution, selection rule, examples, synthesis grammar, "
            "regression, search, scoring, and evaluator remain repository-authored and therefore do not "
            "establish open-world autonomy, independent evaluation, or AGI."
        ),
    }
    if not all(
        (
            report["all_rounds_selected_from_observed_gaps"],
            report["all_rounds_reduced_gap_count"],
            report["all_negative_evidence_retained"],
            report["all_bounded_goals_supported_after_curriculum"],
            report["fresh_engine_runs_unique"],
            report["final_state_unchanged"],
            report["final_trials_unchanged"],
            report["source_state_unchanged"],
            report["source_trials_unchanged"],
            report["base_candidates_preserved"],
            not report["prior_runs_copied"],
            not report["prior_episodes_copied"],
            not report["prior_evidence_copied"],
        )
    ):
        raise IterativeHeterogeneousGoalError(
            "iterative heterogeneous goal aggregate invariant failed"
        )
    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "iterative-heterogeneous-goal-curriculum"
        / f"curriculum-{_digest(seed)[:16]}.json",
        report,
    )
    return report
