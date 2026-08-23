from __future__ import annotations

import copy
import json
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
from agi.durable_state_rehydration import _tree_snapshot
from agi.evidence_frontier_expansion import _challenge_inputs
from agi.evidence_frontier_iterated_expansion import (
    _load_json,
    _reconstruct_first_expansion,
    run_evidence_frontier_iterated_expansion,
)
from agi.evidence_frontier_objective_selection import _assert_prior_unchanged
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.transactional_multisession_commit import _commit_verified_candidate


class RecursiveEvidenceFrontierGrowthError(RuntimeError):
    pass


def _first_expansion_report(root: Path, expansion_seed: str) -> dict[str, Any]:
    path = (
        root
        / ".continual"
        / "evidence"
        / "evidence-frontier-expansion"
        / f"evidence-frontier-expansion-{_digest(expansion_seed)[:16]}.json"
    )
    if not path.is_file():
        raise RecursiveEvidenceFrontierGrowthError(
            "iterated prerequisite did not retain its first-expansion evidence"
        )
    report = _load_json(path)
    if not report.get("passed"):
        raise RecursiveEvidenceFrontierGrowthError("first-expansion prerequisite did not pass")
    return report


def _recursive_history_commitment(
    *,
    iterated_report: Mapping[str, Any],
    learned_target: Mapping[str, Any],
) -> str:
    """Bind another growth step only to stable persisted behavior semantics.

    The v1 commitment folded in the whole iterated report digest, whose input embeds
    per-process values (uuid4 engine run ids from the behavior replays and timestamped
    trial-ledger bytes in the transactional snapshot), so identical seeds and trees
    produced different commitments on every process run. Because this commitment salts
    support-input rotation, variant-constant selection, and the schedule ordering that a
    first-two-unsupported scan consumes, target selection was a per-process lottery.
    v2 binds only fields that are pure functions of behavior semantics: the campaign
    seed, the learned capability identities, the expansion semantic signatures, and the
    learned target's own program and signature.
    """
    required = {
        "iterated_seed": iterated_report.get("seed"),
        "iterated_campaign_kind": iterated_report.get("campaign_kind"),
        "first_candidate_id": iterated_report.get("first_candidate_id"),
        "second_candidate_id": iterated_report.get("second_candidate_id"),
        "first_expansion_candidate_id": iterated_report.get("first_expansion_candidate_id"),
        "second_expansion_candidate_id": iterated_report.get("second_expansion_candidate_id"),
        "second_expansion_semantic_signature": iterated_report.get(
            "second_expansion_semantic_signature"
        ),
        "learned_target_semantic_signature": learned_target.get("semantic_signature"),
        "learned_target_program": learned_target.get("program"),
    }
    if any(value is None for value in required.values()):
        raise RecursiveEvidenceFrontierGrowthError(
            "iterated evidence lacks stable recursive-growth commitments"
        )
    return _digest({**required, "phase": "recursive-evidence-frontier-history-v2"})


def run_recursive_evidence_frontier_growth(root: Path, seed: str) -> dict[str, Any]:
    """Derive a third post-exhaustion growth step from the newly learned behavior itself.

    The prerequisite already learns two evidence-selected controls plus two successive post-exhaustion
    behaviors. This milestone does not consume another item from the first expansion's finite schedule.
    Instead it binds a fresh bounded derivation schedule to the *second newly learned behavior*, checks
    that the schedule is semantically novel and still exposes fail-closed gaps, learns exactly one of
    those gaps through the unchanged regression/transactional path, leaves a second gap untouched, and
    proves from fresh state that all five accumulated capabilities can still be rediscovered without
    caller-supplied Candidate IDs.

    The derivation grammar and evaluator remain repository-authored and bounded. This is internal
    continual-learning evidence, not open-domain autonomous objective invention, independent production
    evidence, or an AGI claim.
    """
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    iterated_seed = f"{seed}:iterated"
    iterated = run_evidence_frontier_iterated_expansion(root, iterated_seed)
    if not iterated.get("passed"):
        raise RecursiveEvidenceFrontierGrowthError("iterated frontier prerequisite did not pass")
    if not iterated.get("all_four_capabilities_rediscovered"):
        raise RecursiveEvidenceFrontierGrowthError(
            "iterated frontier did not retain all four prerequisite capabilities"
        )

    expansion_seed = f"{iterated_seed}:first-expansion"
    expansion = _first_expansion_report(root, expansion_seed)
    reconstructed = _reconstruct_first_expansion(
        root,
        expansion_seed=expansion_seed,
        expansion_report=expansion,
    )
    base = reconstructed["base"]
    first_control = reconstructed["first_control"]
    second_control = reconstructed["second_control"]
    first_expanded_target = reconstructed["expanded_target"]
    second_expanded_target = reconstructed["untouched_control"]
    first_seed = str(reconstructed["first_seed"])
    second_seed = str(reconstructed["second_seed"])

    if str(second_expanded_target["semantic_signature"]) != str(
        iterated["second_expansion_semantic_signature"]
    ):
        raise RecursiveEvidenceFrontierGrowthError(
            "second learned behavior cannot be reconstructed from persisted evidence"
        )

    prior_semantic_signatures = {
        str(first_control["semantic_signature"]),
        str(second_control["semantic_signature"]),
        str(first_expanded_target["semantic_signature"]),
        str(second_expanded_target["semantic_signature"]),
    }
    if len(prior_semantic_signatures) != 4:
        raise RecursiveEvidenceFrontierGrowthError(
            "recursive prerequisite collapsed distinct learned behavior semantics"
        )

    history_digest = _recursive_history_commitment(
        iterated_report=iterated,
        learned_target=second_expanded_target,
    )
    persisted_inputs = tuple(
        copy.deepcopy(item["input"])
        for item in second_expanded_target["support_examples"]
    )
    support_inputs = _expand_support_inputs(
        second_expanded_target["program"],
        persisted_inputs,
        history_digest=history_digest,
    )
    raw_schedule = _precommit_schedule(
        second_expanded_target["program"],
        history_digest=history_digest,
        support_inputs=support_inputs,
    )
    novel_targets = [
        copy.deepcopy(target)
        for target in raw_schedule["target_schedule"]
        if str(target["semantic_signature"]) not in prior_semantic_signatures
    ]
    if len(novel_targets) < 2:
        raise RecursiveEvidenceFrontierGrowthError(
            "newly learned behavior produced fewer than two semantically novel bounded targets"
        )

    recursive_commitment = _digest(
        {
            "history_digest": history_digest,
            "prior_semantic_signatures": sorted(prior_semantic_signatures),
            "raw_schedule_commitment": raw_schedule["schedule_commitment"],
            "targets": novel_targets,
            "phase": "recursive-evidence-frontier-precommit-v1",
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
        raise RecursiveEvidenceFrontierGrowthError(
            "precommitted recursive schedule exposed fewer than two fail-closed novel behaviors"
        )
    target, control = unsupported[:2]

    prior_ids = _all_candidate_ids(root)
    first_candidate_id = str(iterated["first_candidate_id"])
    second_candidate_id = str(iterated["second_candidate_id"])
    first_expansion_candidate_id = str(iterated["first_expansion_candidate_id"])
    second_expansion_candidate_id = str(iterated["second_expansion_candidate_id"])
    required_prior_ids = {
        first_candidate_id,
        second_candidate_id,
        first_expansion_candidate_id,
        second_expansion_candidate_id,
    }
    if not required_prior_ids.issubset(prior_ids):
        raise RecursiveEvidenceFrontierGrowthError(
            "one or more accumulated prerequisite Candidates disappeared before recursive growth"
        )
    prior_tree = _tree_snapshot(root, prior_ids)
    prior_trials = _trial_snapshot(root, prior_ids)
    if not all(prior_trials.get(candidate_id) for candidate_id in prior_ids):
        raise RecursiveEvidenceFrontierGrowthError(
            "pre-recursive capability graph contains a Candidate without durable trial evidence"
        )

    challenges = _challenge_inputs(second_seed)
    expected = tuple(execute_program(target["program"], value) for value in challenges)
    spec = {
        "name": f"recursive-evidence-frontier-{str(target['semantic_signature'])[:12]}",
        "input_domain": str(target["input_domain"]),
        "output_domain": str(target["output_domain"]),
        "examples": _examples(target),
        "probe": challenges[0],
        "expected": expected[0],
        "challenge": challenges[1],
        "challenge_expected": expected[1],
        "max_nodes": int(target["program_nodes"]),
    }

    with tempfile.TemporaryDirectory(prefix="agi-recursive-evidence-frontier-learn-") as temporary:
        learner = Path(temporary).resolve()
        _copy_persistent_state(root, learner)
        _assert_prior_unchanged(
            learner,
            prior_ids,
            prior_tree,
            prior_trials,
            label="recursive frontier fresh learner reconstruction",
        )
        if _attempt(learner, target)["supported"]:
            raise RecursiveEvidenceFrontierGrowthError(
                "recursive target became supported before learning"
            )
        learned = _promote_task(
            learner,
            seed=f"{seed}:recursive-learn",
            spec=spec,
        )
        candidate_id = str(learned["candidate_id"])
        if candidate_id in prior_ids:
            raise RecursiveEvidenceFrontierGrowthError(
                "recursive frontier growth reused a prior Candidate identity"
            )
        if not learned.get("negative_evidence_retained"):
            raise RecursiveEvidenceFrontierGrowthError(
                "recursive frontier growth discarded its pre-learning negative evidence"
            )
        for value, expected_value in zip(challenges, expected, strict=True):
            if execute_program(learned["program"], value) != expected_value:
                raise RecursiveEvidenceFrontierGrowthError(
                    "recursive Candidate failed the sealed held-back behavior"
                )
        _assert_prior_unchanged(
            learner,
            prior_ids,
            prior_tree,
            prior_trials,
            label="recursive frontier learning",
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
        label="recursive frontier transactional commit",
    )
    final_ids = _all_candidate_ids(root)
    if len(final_ids) != len(prior_ids) + 1 or candidate_id not in final_ids:
        raise RecursiveEvidenceFrontierGrowthError(
            "recursive frontier growth did not commit exactly one new Candidate"
        )
    final_tree = _tree_snapshot(root, final_ids)
    final_trials = _trial_snapshot(root, final_ids)
    if not final_trials.get(candidate_id):
        raise RecursiveEvidenceFrontierGrowthError(
            "recursive frontier Candidate lacks durable trial evidence"
        )

    with tempfile.TemporaryDirectory(prefix="agi-recursive-evidence-frontier-resolver-") as temporary:
        resolver = Path(temporary).resolve()
        _copy_persistent_state(root, resolver)
        if _tree_snapshot(resolver, final_ids) != final_tree:
            raise RecursiveEvidenceFrontierGrowthError(
                "fresh recursive resolver changed durable Candidate bytes"
            )
        if _trial_snapshot(resolver, final_ids) != final_trials:
            raise RecursiveEvidenceFrontierGrowthError(
                "fresh recursive resolver changed durable Candidate trials"
            )

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
            challenge_inputs=challenges,
        )
        first_expansion_replay = _solve_and_replay(
            resolver,
            first_expanded_target,
            required_candidate_id=first_expansion_candidate_id,
            challenge_inputs=challenges,
        )
        second_expansion_replay = _solve_and_replay(
            resolver,
            second_expanded_target,
            required_candidate_id=second_expansion_candidate_id,
            challenge_inputs=challenges,
        )
        recursive_replay = _solve_and_replay(
            resolver,
            target,
            required_candidate_id=candidate_id,
            challenge_inputs=challenges,
        )
        control_attempt = _attempt(resolver, control)
        if control_attempt["supported"]:
            raise RecursiveEvidenceFrontierGrowthError(
                "recursive learning overgeneralized the untouched derived control"
            )
        if _tree_snapshot(resolver, final_ids) != final_tree:
            raise RecursiveEvidenceFrontierGrowthError(
                "recursive behavioral replay changed Candidate bytes"
            )
        if _trial_snapshot(resolver, final_ids) != final_trials:
            raise RecursiveEvidenceFrontierGrowthError(
                "recursive behavioral replay changed Candidate trials"
            )

    replays = (
        first_replay,
        second_replay,
        first_expansion_replay,
        second_expansion_replay,
        recursive_replay,
    )
    required_replay_ids = (
        first_candidate_id,
        second_candidate_id,
        first_expansion_candidate_id,
        second_expansion_candidate_id,
        candidate_id,
    )
    all_five_rediscovered = all(
        required in {str(value) for value in replay["solver_selected_candidate_ids"]}
        for required, replay in zip(required_replay_ids, replays, strict=True)
    )
    all_replays_without_caller_ids = all(
        replay["solver_candidate_ids_supplied_by_caller"] is False for replay in replays
    )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "recursive-evidence-frontier-growth-v1",
        "seed": seed,
        "iterated_prerequisite_digest": str(iterated["digest"]),
        "recursive_target_generation_source": "newly_learned_second_expansion_behavior",
        "finite_first_expansion_schedule_replayed_as_recursive_objective": False,
        "fresh_global_target_generator_used_for_recursive_objective": False,
        "history_digest": history_digest,
        "raw_recursive_schedule_commitment": str(raw_schedule["schedule_commitment"]),
        "recursive_schedule_precommitted_before_support_checks": True,
        "recursive_commitment": recursive_commitment,
        "prior_semantic_signatures": sorted(prior_semantic_signatures),
        "support_attempts": attempts,
        "recursive_semantic_signature": str(target["semantic_signature"]),
        "recursive_target_absent_from_prior_semantics": str(target["semantic_signature"])
        not in prior_semantic_signatures,
        "recursive_target_failed_closed_before_learning": True,
        "recursive_control_semantic_signature": str(control["semantic_signature"]),
        "recursive_control_remained_failed_closed": True,
        "new_candidate_id": candidate_id,
        "new_candidate_negative_evidence_retained": True,
        "transactional_commit": commit,
        "prior_candidate_state_unchanged": True,
        "prior_trial_state_unchanged": True,
        "first_selected_behavior_replay": first_replay,
        "second_selected_behavior_replay": second_replay,
        "first_expansion_behavior_replay": first_expansion_replay,
        "second_expansion_behavior_replay": second_expansion_replay,
        "recursive_behavior_replay": recursive_replay,
        "all_five_capabilities_rediscovered": all_five_rediscovered,
        "all_replays_avoided_caller_candidate_ids": all_replays_without_caller_ids,
        "source_candidate_state_unchanged": True,
        "source_trial_state_unchanged": True,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded recursive evidence-driven continual-learning evidence only. A new "
            "precommitted target schedule is derived from the newly learned second-expansion behavior "
            "rather than replaying another objective from the first finite post-exhaustion schedule, "
            "and all five accumulated capabilities must be rediscovered from fresh state without caller "
            "Candidate IDs. The derivation grammar, synthesis, regression, evaluator, probes and scoring "
            "remain repository-authored; this is not open-domain autonomous objective invention, "
            "independent production evidence, or AGI."
        ),
    }
    required = (
        report["recursive_target_absent_from_prior_semantics"],
        report["recursive_target_failed_closed_before_learning"],
        report["recursive_control_remained_failed_closed"],
        report["new_candidate_negative_evidence_retained"],
        report["prior_candidate_state_unchanged"],
        report["prior_trial_state_unchanged"],
        report["all_five_capabilities_rediscovered"],
        report["all_replays_avoided_caller_candidate_ids"],
    )
    if not all(required):
        raise RecursiveEvidenceFrontierGrowthError(
            "recursive evidence-frontier growth aggregate invariant failed"
        )
    report["digest"] = _digest(report)
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "recursive-evidence-frontier-growth"
        / f"recursive-evidence-frontier-growth-{_digest(seed)[:16]}.json",
        report,
    )
    return report
