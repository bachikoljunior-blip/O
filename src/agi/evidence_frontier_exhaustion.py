from __future__ import annotations

import copy
import tempfile
from pathlib import Path
from typing import Any, Mapping

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import execute_program
from agi.crossdomain_failure_derived_target import _attempt, _examples, _solve_and_replay
from agi.cumulative_crossdomain_failure_retention import _reconstruct_targets
from agi.durable_state_rehydration import _tree_snapshot
from agi.evidence_frontier_objective_selection import (
    EvidenceFrontierObjectiveSelectionError,
    _assert_prior_unchanged,
    _load_round_report,
    run_evidence_frontier_objective_selection,
)
from agi.generated_crossdomain_cegis import generated_crossdomain_target
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.transactional_multisession_commit import _commit_verified_candidate


class EvidenceFrontierExhaustionError(RuntimeError):
    pass


def _frontier_state(
    root: Path,
    frontier: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    state: list[dict[str, Any]] = []
    reconstructed: dict[str, dict[str, Any]] = {}
    for entry in frontier:
        round_seed = str(entry["round_seed"])
        report = _load_round_report(root, round_seed)
        target_state = _reconstruct_targets(root, report)
        reconstructed[round_seed] = target_state
        attempt = _attempt(root, target_state["control"])
        state.append(
            {
                "rank": str(entry["rank"]),
                "round_seed": round_seed,
                "source_input_domain": str(entry["source_input_domain"]),
                "semantic_signature": str(entry["semantic_signature"]),
                "supported": bool(attempt["supported"]),
                "candidate_ids_supplied_by_caller": bool(
                    attempt["candidate_ids_supplied_by_caller"]
                ),
            }
        )
    return state, reconstructed


def run_evidence_frontier_exhaustion(root: Path, seed: str) -> dict[str, Any]:
    """Take a second autonomous learning step from the persisted unresolved evidence frontier."""
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    first = run_evidence_frontier_objective_selection(root, f"{seed}:first")
    if not first.get("passed"):
        raise EvidenceFrontierExhaustionError(
            "first evidence-frontier objective-selection round did not pass"
        )
    frontier = [dict(item) for item in first.get("frontier", [])]
    if len(frontier) < 2:
        raise EvidenceFrontierExhaustionError(
            "repeated objective selection requires at least two persisted controls"
        )

    post_first_state, reconstructed = _frontier_state(root, frontier)
    first_signature = str(first["selected_semantic_signature"])
    first_state = next(
        (item for item in post_first_state if item["semantic_signature"] == first_signature),
        None,
    )
    if first_state is None or not first_state["supported"]:
        raise EvidenceFrontierExhaustionError(
            "first evidence-selected objective was not durably supported before round two"
        )

    frontier_commitment = _digest(
        {
            "first_frontier_commitment": str(first["frontier_commitment"]),
            "ordered_controls": [
                {
                    "rank": str(item["rank"]),
                    "round_seed": str(item["round_seed"]),
                    "semantic_signature": str(item["semantic_signature"]),
                }
                for item in frontier
            ],
            "phase": "evidence-frontier-round-two-precommit-v1",
        }
    )
    second_entry = next((item for item in post_first_state if not item["supported"]), None)
    if second_entry is None:
        raise EvidenceFrontierExhaustionError(
            "no second fail-closed objective remained after the first evidence-driven learning round"
        )
    if str(second_entry["semantic_signature"]) == first_signature:
        raise EvidenceFrontierExhaustionError(
            "round two selected the already learned first objective"
        )

    second_seed = str(second_entry["round_seed"])
    second_report = _load_round_report(root, second_seed)
    second_control = reconstructed[second_seed]["control"]
    if str(second_control["semantic_signature"]) != str(second_entry["semantic_signature"]):
        raise EvidenceFrontierExhaustionError(
            "round-two reconstructed objective changed after precommitment"
        )

    source_seed = f"{second_seed}:source-cegis"
    _source_target, _meta, _initial, _diagnostic, final_inputs = generated_crossdomain_target(
        source_seed
    )
    challenge_inputs = tuple(copy.deepcopy(value) for value in final_inputs)
    expected = tuple(execute_program(second_control["program"], value) for value in challenge_inputs)
    if len(challenge_inputs) < 2:
        raise EvidenceFrontierExhaustionError(
            "round-two persisted failure lacks a multi-instance held-back challenge"
        )

    prior_ids = _all_candidate_ids(root)
    first_candidate_id = str(first["new_candidate_id"])
    if first_candidate_id not in prior_ids:
        raise EvidenceFrontierExhaustionError(
            "round-one evidence-selected Candidate disappeared before round two"
        )
    prior_tree = _tree_snapshot(root, prior_ids)
    prior_trials = _trial_snapshot(root, prior_ids)
    if not all(prior_trials.get(candidate_id) for candidate_id in prior_ids):
        raise EvidenceFrontierExhaustionError(
            "prior capability graph contains a Candidate without durable trial evidence"
        )

    spec = {
        "name": f"evidence-frontier-r2-{str(second_entry['semantic_signature'])[:12]}",
        "input_domain": str(second_control["input_domain"]),
        "output_domain": str(second_control["output_domain"]),
        "examples": _examples(second_control),
        "probe": challenge_inputs[0],
        "expected": expected[0],
        "challenge": challenge_inputs[1],
        "challenge_expected": expected[1],
        "max_nodes": int(second_control["program_nodes"]),
    }

    with tempfile.TemporaryDirectory(prefix="agi-evidence-frontier-r2-learn-") as temporary:
        learner = Path(temporary).resolve()
        _copy_persistent_state(root, learner)
        _assert_prior_unchanged(
            learner,
            prior_ids,
            prior_tree,
            prior_trials,
            label="round-two fresh learner reconstruction",
        )
        if _attempt(learner, second_control)["supported"]:
            raise EvidenceFrontierExhaustionError(
                "precommitted round-two objective became supported before learning"
            )
        learned = _promote_task(
            learner,
            seed=f"{seed}:second",
            spec=spec,
        )
        second_candidate_id = str(learned["candidate_id"])
        if second_candidate_id in prior_ids:
            raise EvidenceFrontierExhaustionError(
                "round-two learning reused a prior Candidate identity"
            )
        if not learned.get("negative_evidence_retained"):
            raise EvidenceFrontierExhaustionError(
                "round-two learning discarded its pre-learning negative evidence"
            )
        for value, expected_value in zip(challenge_inputs, expected, strict=True):
            if execute_program(learned["program"], value) != expected_value:
                raise EvidenceFrontierExhaustionError(
                    "round-two Candidate failed its sealed challenge"
                )
        _assert_prior_unchanged(
            learner,
            prior_ids,
            prior_tree,
            prior_trials,
            label="round-two learning",
        )
        commit = _commit_verified_candidate(
            learner,
            root,
            candidate_id=second_candidate_id,
            scope=str(learned["scope"]),
        )

    _assert_prior_unchanged(
        root,
        prior_ids,
        prior_tree,
        prior_trials,
        label="round-two transactional commit",
    )
    final_ids = _all_candidate_ids(root)
    final_tree = _tree_snapshot(root, final_ids)
    final_trials = _trial_snapshot(root, final_ids)
    if second_candidate_id not in final_ids or not final_trials.get(second_candidate_id):
        raise EvidenceFrontierExhaustionError(
            "round-two Candidate lacks durable trial evidence"
        )

    first_seed = str(first["selected_round_seed"])
    first_report = _load_round_report(root, first_seed)
    first_control = _reconstruct_targets(root, first_report)["control"]
    base_replays: list[dict[str, Any]] = []
    remaining_controls: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="agi-evidence-frontier-r2-resolver-") as temporary:
        resolver = Path(temporary).resolve()
        _copy_persistent_state(root, resolver)
        if _tree_snapshot(resolver, final_ids) != final_tree:
            raise EvidenceFrontierExhaustionError(
                "round-two fresh resolver changed durable Candidate bytes"
            )
        if _trial_snapshot(resolver, final_ids) != final_trials:
            raise EvidenceFrontierExhaustionError(
                "round-two fresh resolver changed durable Candidate trials"
            )

        first_selected_replay = _solve_and_replay(
            resolver,
            first_control,
            required_candidate_id=first_candidate_id,
            challenge_inputs=tuple(
                copy.deepcopy(value)
                for value in generated_crossdomain_target(f"{first_seed}:source-cegis")[4]
            ),
        )
        second_selected_replay = _solve_and_replay(
            resolver,
            second_control,
            required_candidate_id=second_candidate_id,
            challenge_inputs=challenge_inputs,
        )

        for entry in frontier:
            round_seed = str(entry["round_seed"])
            round_report = _load_round_report(resolver, round_seed)
            round_targets = _reconstruct_targets(resolver, round_report)
            round_final_inputs = tuple(
                copy.deepcopy(value)
                for value in generated_crossdomain_target(f"{round_seed}:source-cegis")[4]
            )
            source_replay = _solve_and_replay(
                resolver,
                round_targets["base_target"],
                required_candidate_id=str(round_report["source_candidate_id"]),
                challenge_inputs=round_final_inputs,
            )
            derived_replay = _solve_and_replay(
                resolver,
                round_targets["target"],
                required_candidate_id=str(round_report["new_candidate_id"]),
                challenge_inputs=round_final_inputs,
            )
            base_replays.append(
                {
                    "round_seed": round_seed,
                    "source_candidate_id": str(round_report["source_candidate_id"]),
                    "derived_candidate_id": str(round_report["new_candidate_id"]),
                    "source_replay": source_replay,
                    "derived_replay": derived_replay,
                }
            )
            signature = str(entry["semantic_signature"])
            if signature not in {first_signature, str(second_entry["semantic_signature"])}:
                attempt = _attempt(resolver, round_targets["control"])
                if attempt["supported"]:
                    raise EvidenceFrontierExhaustionError(
                        "repeated evidence-driven learning overgeneralized an unselected control"
                    )
                remaining_controls.append(
                    {
                        "round_seed": round_seed,
                        "semantic_signature": signature,
                        "failed_closed_after_two_rounds": True,
                    }
                )

        if _tree_snapshot(resolver, final_ids) != final_tree:
            raise EvidenceFrontierExhaustionError(
                "round-two behavioral replay changed Candidate bytes"
            )
        if _trial_snapshot(resolver, final_ids) != final_trials:
            raise EvidenceFrontierExhaustionError(
                "round-two behavioral replay changed Candidate trials"
            )

    _assert_prior_unchanged(
        root,
        prior_ids,
        prior_tree,
        prior_trials,
        label="round-two post-replay source",
    )
    if _tree_snapshot(root, final_ids) != final_tree or _trial_snapshot(root, final_ids) != final_trials:
        raise EvidenceFrontierExhaustionError(
            "round-two post-replay final durable state changed"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "evidence-frontier-exhaustion-v1",
        "seed": seed,
        "first_round_digest": str(first["digest"]),
        "frontier_commitment": frontier_commitment,
        "frontier_precommitted_before_round_two_support_checks": True,
        "caller_selected_source_domain_round_two": False,
        "fresh_target_generator_used_for_round_two_objective": False,
        "post_first_frontier_state": post_first_state,
        "first_selected_semantic_signature": first_signature,
        "first_candidate_id": first_candidate_id,
        "second_selected_round_seed": second_seed,
        "second_selected_source_input_domain": str(second_report["source_input_domain"]),
        "second_selected_semantic_signature": str(second_entry["semantic_signature"]),
        "second_objective_failed_closed_before_learning": True,
        "second_candidate_id": second_candidate_id,
        "second_candidate_negative_evidence_retained": True,
        "transactional_commit": commit,
        "first_candidate_state_unchanged_during_round_two": True,
        "prior_candidate_state_unchanged": True,
        "prior_trial_state_unchanged": True,
        "first_selected_behavior_replay": first_selected_replay,
        "second_selected_behavior_replay": second_selected_replay,
        "first_candidate_rediscovered_after_round_two": first_candidate_id
        in {str(value) for value in first_selected_replay["solver_selected_candidate_ids"]},
        "second_candidate_rediscovered_without_caller_ids": second_candidate_id
        in {str(value) for value in second_selected_replay["solver_selected_candidate_ids"]},
        "base_behavior_replays": base_replays,
        "all_base_source_behaviors_retained": all(
            str(item["source_candidate_id"])
            in {str(value) for value in item["source_replay"]["solver_selected_candidate_ids"]}
            for item in base_replays
        ),
        "all_base_derived_behaviors_retained": all(
            str(item["derived_candidate_id"])
            in {str(value) for value in item["derived_replay"]["solver_selected_candidate_ids"]}
            for item in base_replays
        ),
        "remaining_controls": remaining_controls,
        "all_remaining_controls_failed_closed": all(
            bool(item["failed_closed_after_two_rounds"]) for item in remaining_controls
        ),
        "all_replays_avoided_caller_candidate_ids": (
            first_selected_replay["solver_candidate_ids_supplied_by_caller"] is False
            and second_selected_replay["solver_candidate_ids_supplied_by_caller"] is False
            and all(
                item["source_replay"]["solver_candidate_ids_supplied_by_caller"] is False
                and item["derived_replay"]["solver_candidate_ids_supplied_by_caller"] is False
                for item in base_replays
            )
        ),
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded repeated evidence-driven curriculum evidence only. Round two selects the "
            "next still-fail-closed objective from the same persisted heterogeneous evidence frontier "
            "after round-one learning, without caller-selected source domains or a fresh target generator "
            "for objective choice, while requiring functional no-forgetting and fail-closed controls. "
            "Generators, failure reconstruction, synthesis, regression, evaluator, probes and scoring "
            "remain repository-authored; this is not independent production evidence, open-domain "
            "autonomous objective invention, or AGI."
        ),
    }
    if not all(
        (
            report["frontier_precommitted_before_round_two_support_checks"],
            report["caller_selected_source_domain_round_two"] is False,
            report["fresh_target_generator_used_for_round_two_objective"] is False,
            report["second_objective_failed_closed_before_learning"],
            report["second_candidate_negative_evidence_retained"],
            report["first_candidate_state_unchanged_during_round_two"],
            report["prior_candidate_state_unchanged"],
            report["prior_trial_state_unchanged"],
            report["first_candidate_rediscovered_after_round_two"],
            report["second_candidate_rediscovered_without_caller_ids"],
            report["all_base_source_behaviors_retained"],
            report["all_base_derived_behaviors_retained"],
            report["all_remaining_controls_failed_closed"],
            report["all_replays_avoided_caller_candidate_ids"],
        )
    ):
        raise EvidenceFrontierExhaustionError(
            "repeated evidence-frontier objective-selection aggregate invariant failed"
        )
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "evidence-frontier-exhaustion"
        / f"evidence-frontier-exhaustion-{_digest(seed)[:16]}.json",
        report,
    )
    return report
