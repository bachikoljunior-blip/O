from __future__ import annotations

import copy
import tempfile
from pathlib import Path
from typing import Any, Mapping

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import execute_program
from agi.crossdomain_failure_derived_target import (
    _attempt,
    _examples,
    _expand_support_inputs,
    _precommit_schedule,
    _solve_and_replay,
)
from agi.cumulative_crossdomain_failure_retention import _reconstruct_targets
from agi.durable_state_rehydration import _tree_snapshot
from agi.evidence_frontier_exhaustion import run_evidence_frontier_exhaustion
from agi.evidence_frontier_objective_selection import _assert_prior_unchanged, _load_round_report
from agi.generated_crossdomain_cegis import generated_crossdomain_target
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.transactional_multisession_commit import _commit_verified_candidate


class EvidenceFrontierExpansionError(RuntimeError):
    pass


def _selected_round_seed(base: Mapping[str, Any], semantic_signature: str) -> str:
    states = base.get("post_first_frontier_state")
    if not isinstance(states, list):
        raise EvidenceFrontierExpansionError("exhausted frontier lacks persisted round state")
    matches = [
        str(item["round_seed"])
        for item in states
        if isinstance(item, Mapping)
        and str(item.get("semantic_signature")) == semantic_signature
    ]
    if len(matches) != 1:
        raise EvidenceFrontierExpansionError(
            "exhausted frontier does not identify exactly one round for selected semantics"
        )
    return matches[0]


def _challenge_inputs(round_seed: str) -> tuple[Any, ...]:
    values = generated_crossdomain_target(f"{round_seed}:source-cegis")[4]
    result = tuple(copy.deepcopy(value) for value in values)
    if len(result) < 2:
        raise EvidenceFrontierExpansionError(
            "persisted heterogeneous round lacks a multi-instance held-back challenge"
        )
    return result


def _semantic_history_commitment(
    *,
    frontier_commitment: str,
    first_signature: str,
    second_signature: str,
    second_control: Mapping[str, Any],
) -> str:
    """Bind target planning to stable behavior semantics, never audit-time storage bytes."""
    if not frontier_commitment or not first_signature or not second_signature:
        raise EvidenceFrontierExpansionError("frontier semantics are incomplete")
    return _digest(
        {
            "frontier_commitment": frontier_commitment,
            "first_selected_semantic_signature": first_signature,
            "second_selected_semantic_signature": second_signature,
            "second_control_program": second_control["program"],
            "phase": "post-exhaustion-evidence-derived-semantic-history-v1",
        }
    )


def run_evidence_frontier_expansion(root: Path, seed: str) -> dict[str, Any]:
    """Derive a novel bounded objective after the finite heterogeneous control frontier is learned.

    The prerequisite takes two learning steps from durable fail-closed heterogeneous controls. This
    milestone then refuses to treat that finite control list as the end of the curriculum. It binds a
    deterministic bounded target-derivation schedule to the persisted exhaustion semantics and the
    second learned control program, precommits that schedule before any support check, and learns the
    first still-fail-closed behavior that is semantically outside both exhausted controls. Audit-time
    storage digests remain evidence bindings but never influence target choice. All prior Candidate
    bytes/trials are immutable and both evidence-selected behaviors must still be rediscovered
    behaviorally from fresh state after the new learning step. A second derived behavior remains an
    untouched fail-closed control.

    This is internal bounded continual-learning evidence, not independent production evidence or AGI.
    """
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    base = run_evidence_frontier_exhaustion(root, f"{seed}:base")
    if not base.get("passed"):
        raise EvidenceFrontierExpansionError("evidence-frontier exhaustion prerequisite did not pass")
    if not base.get("first_candidate_rediscovered_after_round_two"):
        raise EvidenceFrontierExpansionError("first evidence-selected behavior was not retained")
    if not base.get("second_candidate_rediscovered_without_caller_ids"):
        raise EvidenceFrontierExpansionError("second evidence-selected behavior was not retained")

    first_signature = str(base["first_selected_semantic_signature"])
    second_signature = str(base["second_selected_semantic_signature"])
    if first_signature == second_signature:
        raise EvidenceFrontierExpansionError("exhausted frontier collapsed to one behavior")
    exhausted_signatures = {first_signature, second_signature}

    first_seed = _selected_round_seed(base, first_signature)
    second_seed = str(base["second_selected_round_seed"])
    first_report = _load_round_report(root, first_seed)
    second_report = _load_round_report(root, second_seed)
    first_control = _reconstruct_targets(root, first_report)["control"]
    second_control = _reconstruct_targets(root, second_report)["control"]
    if str(first_control["semantic_signature"]) != first_signature:
        raise EvidenceFrontierExpansionError("first exhausted control reconstruction changed")
    if str(second_control["semantic_signature"]) != second_signature:
        raise EvidenceFrontierExpansionError("second exhausted control reconstruction changed")

    history_digest = _semantic_history_commitment(
        frontier_commitment=str(base["frontier_commitment"]),
        first_signature=first_signature,
        second_signature=second_signature,
        second_control=second_control,
    )
    persisted_inputs = tuple(
        copy.deepcopy(item["input"]) for item in second_control["support_examples"]
    )
    support_inputs = _expand_support_inputs(
        second_control["program"],
        persisted_inputs,
        history_digest=history_digest,
    )
    raw_schedule = _precommit_schedule(
        second_control["program"],
        history_digest=history_digest,
        support_inputs=support_inputs,
    )
    novel_targets = [
        copy.deepcopy(target)
        for target in raw_schedule["target_schedule"]
        if str(target["semantic_signature"]) not in exhausted_signatures
    ]
    if len(novel_targets) < 2:
        raise EvidenceFrontierExpansionError(
            "exhausted evidence produced fewer than two semantically novel bounded targets"
        )
    expansion_commitment = _digest(
        {
            "history_digest": history_digest,
            "exhausted_semantic_signatures": sorted(exhausted_signatures),
            "targets": novel_targets,
            "phase": "post-exhaustion-evidence-derived-precommit-v1",
        }
    )

    attempts: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    for target in novel_targets:
        attempt = _attempt(root, target)
        attempts.append(
            {
                "semantic_signature": str(target["semantic_signature"]),
                "input_domain": str(target["input_domain"]),
                "output_domain": str(target["output_domain"]),
                "program_nodes": int(target["program_nodes"]),
                "supported_before_learning": bool(attempt["supported"]),
                "candidate_ids_supplied_by_caller": bool(
                    attempt["candidate_ids_supplied_by_caller"]
                ),
            }
        )
        if not attempt["supported"]:
            unsupported.append(target)
        if len(unsupported) >= 2:
            break
    if len(unsupported) < 2:
        raise EvidenceFrontierExpansionError(
            "precommitted post-exhaustion schedule exposed fewer than two fail-closed novel behaviors"
        )
    target, control = unsupported[:2]

    prior_ids = _all_candidate_ids(root)
    first_candidate_id = str(base["first_candidate_id"])
    second_candidate_id = str(base["second_candidate_id"])
    if first_candidate_id not in prior_ids or second_candidate_id not in prior_ids:
        raise EvidenceFrontierExpansionError(
            "evidence-selected Candidate disappeared before frontier expansion"
        )
    prior_tree = _tree_snapshot(root, prior_ids)
    prior_trials = _trial_snapshot(root, prior_ids)
    if not all(prior_trials.get(candidate_id) for candidate_id in prior_ids):
        raise EvidenceFrontierExpansionError(
            "prior capability graph contains a Candidate without durable trial evidence"
        )

    challenges = _challenge_inputs(second_seed)
    expected = tuple(execute_program(target["program"], value) for value in challenges)
    spec = {
        "name": f"evidence-frontier-expanded-{str(target['semantic_signature'])[:12]}",
        "input_domain": str(target["input_domain"]),
        "output_domain": str(target["output_domain"]),
        "examples": _examples(target),
        "probe": challenges[0],
        "expected": expected[0],
        "challenge": challenges[1],
        "challenge_expected": expected[1],
        "max_nodes": int(target["program_nodes"]),
    }

    with tempfile.TemporaryDirectory(prefix="agi-evidence-frontier-expand-learn-") as temporary:
        learner = Path(temporary).resolve()
        _copy_persistent_state(root, learner)
        _assert_prior_unchanged(
            learner,
            prior_ids,
            prior_tree,
            prior_trials,
            label="post-exhaustion fresh learner reconstruction",
        )
        if _attempt(learner, target)["supported"]:
            raise EvidenceFrontierExpansionError(
                "precommitted post-exhaustion objective became supported before learning"
            )
        learned = _promote_task(
            learner,
            seed=f"{seed}:expanded-learn",
            spec=spec,
        )
        candidate_id = str(learned["candidate_id"])
        if candidate_id in prior_ids:
            raise EvidenceFrontierExpansionError("frontier expansion reused a prior Candidate identity")
        if not learned.get("negative_evidence_retained"):
            raise EvidenceFrontierExpansionError(
                "frontier expansion discarded its pre-learning negative evidence"
            )
        for value, expected_value in zip(challenges, expected, strict=True):
            if execute_program(learned["program"], value) != expected_value:
                raise EvidenceFrontierExpansionError(
                    "expanded Candidate failed the sealed held-back behavior"
                )
        _assert_prior_unchanged(
            learner,
            prior_ids,
            prior_tree,
            prior_trials,
            label="post-exhaustion learning",
        )
        commit = _commit_verified_candidate(
            learner,
            root,
            candidate_id=candidate_id,
            scope=str(learned["scope"]),
        )

    _assert_prior_unchanged(
        root,
        prior_ids,
        prior_tree,
        prior_trials,
        label="post-exhaustion transactional commit",
    )
    final_ids = _all_candidate_ids(root)
    final_tree = _tree_snapshot(root, final_ids)
    final_trials = _trial_snapshot(root, final_ids)
    if candidate_id not in final_ids or not final_trials.get(candidate_id):
        raise EvidenceFrontierExpansionError("expanded Candidate lacks durable trial evidence")

    with tempfile.TemporaryDirectory(prefix="agi-evidence-frontier-expand-resolver-") as temporary:
        resolver = Path(temporary).resolve()
        _copy_persistent_state(root, resolver)
        if _tree_snapshot(resolver, final_ids) != final_tree:
            raise EvidenceFrontierExpansionError("fresh resolver changed durable Candidate bytes")
        if _trial_snapshot(resolver, final_ids) != final_trials:
            raise EvidenceFrontierExpansionError("fresh resolver changed durable Candidate trials")

        first_replay = _solve_and_replay(
            resolver,
            first_control,
            required_candidate_id=first_candidate_id,
            challenge_inputs=_challenge_inputs(first_seed),
        )
        second_replay = _solve_and_replay(
            resolver,
            second_control,
            required_candidate_id=second_candidate_id,
            challenge_inputs=_challenge_inputs(second_seed),
        )
        expanded_replay = _solve_and_replay(
            resolver,
            target,
            required_candidate_id=candidate_id,
            challenge_inputs=challenges,
        )
        if _attempt(resolver, control)["supported"]:
            raise EvidenceFrontierExpansionError(
                "expanded learning overgeneralized the untouched derived control"
            )
        if _tree_snapshot(resolver, final_ids) != final_tree:
            raise EvidenceFrontierExpansionError("fresh behavioral replay changed Candidate bytes")
        if _trial_snapshot(resolver, final_ids) != final_trials:
            raise EvidenceFrontierExpansionError("fresh behavioral replay changed Candidate trials")

    _assert_prior_unchanged(
        root,
        prior_ids,
        prior_tree,
        prior_trials,
        label="post-exhaustion post-replay source",
    )
    if _tree_snapshot(root, final_ids) != final_tree or _trial_snapshot(root, final_ids) != final_trials:
        raise EvidenceFrontierExpansionError("post-replay final durable state changed")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "evidence-frontier-expansion-v1",
        "seed": seed,
        "exhaustion_digest": str(base["digest"]),
        "target_generation_source": "persisted_exhausted_heterogeneous_evidence",
        "finite_exhausted_control_replayed_as_new_objective": False,
        "fresh_global_target_generator_used_for_objective_choice": False,
        "target_selection_uses_audit_storage_digest": False,
        "expansion_schedule_precommitted_before_support_checks": True,
        "history_digest": history_digest,
        "raw_derived_schedule_commitment": str(raw_schedule["schedule_commitment"]),
        "expansion_commitment": expansion_commitment,
        "exhausted_semantic_signatures": sorted(exhausted_signatures),
        "support_attempts": attempts,
        "expanded_semantic_signature": str(target["semantic_signature"]),
        "expanded_target_absent_from_exhausted_frontier": str(target["semantic_signature"])
        not in exhausted_signatures,
        "expanded_target_failed_closed_before_learning": True,
        "derived_control_semantic_signature": str(control["semantic_signature"]),
        "derived_control_failed_closed_before_learning": True,
        "new_candidate_id": candidate_id,
        "new_candidate_negative_evidence_retained": True,
        "transactional_commit": commit,
        "prior_candidate_state_unchanged": True,
        "prior_trial_state_unchanged": True,
        "first_candidate_id": first_candidate_id,
        "second_candidate_id": second_candidate_id,
        "first_selected_behavior_replay": first_replay,
        "second_selected_behavior_replay": second_replay,
        "expanded_behavior_replay": expanded_replay,
        "first_candidate_rediscovered_after_expansion": first_candidate_id
        in {str(value) for value in first_replay["solver_selected_candidate_ids"]},
        "second_candidate_rediscovered_after_expansion": second_candidate_id
        in {str(value) for value in second_replay["solver_selected_candidate_ids"]},
        "expanded_candidate_rediscovered_without_caller_ids": candidate_id
        in {str(value) for value in expanded_replay["solver_selected_candidate_ids"]},
        "all_replays_avoided_caller_candidate_ids": all(
            replay["solver_candidate_ids_supplied_by_caller"] is False
            for replay in (first_replay, second_replay, expanded_replay)
        ),
        "derived_control_gap_remained_failed_closed": True,
        "source_candidate_state_unchanged": True,
        "source_trial_state_unchanged": True,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded post-exhaustion continual-learning evidence only. The new target is "
            "derived from persisted exhausted heterogeneous failure semantics rather than selecting "
            "another item from the finite two-control frontier, but the derivation grammar, regression, "
            "evaluator, probes and scoring remain repository-authored. This is not open-domain autonomous "
            "objective invention, independent production evidence, or AGI."
        ),
    }
    required = (
        report["expanded_target_absent_from_exhausted_frontier"],
        report["expanded_target_failed_closed_before_learning"],
        report["derived_control_failed_closed_before_learning"],
        report["new_candidate_negative_evidence_retained"],
        report["prior_candidate_state_unchanged"],
        report["prior_trial_state_unchanged"],
        report["first_candidate_rediscovered_after_expansion"],
        report["second_candidate_rediscovered_after_expansion"],
        report["expanded_candidate_rediscovered_without_caller_ids"],
        report["all_replays_avoided_caller_candidate_ids"],
        report["derived_control_gap_remained_failed_closed"],
    )
    if not all(required):
        raise EvidenceFrontierExpansionError("post-exhaustion frontier expansion invariant failed")
    report["digest"] = _digest(report)
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "evidence-frontier-expansion"
        / f"evidence-frontier-expansion-{_digest(seed)[:16]}.json",
        report,
    )
    return report
