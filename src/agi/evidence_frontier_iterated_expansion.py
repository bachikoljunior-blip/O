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
from agi.cumulative_crossdomain_failure_retention import _reconstruct_targets
from agi.durable_state_rehydration import _tree_snapshot
from agi.evidence_frontier_expansion import (
    _challenge_inputs,
    _selected_round_seed,
    _semantic_history_commitment,
    run_evidence_frontier_expansion,
)
from agi.evidence_frontier_objective_selection import _assert_prior_unchanged, _load_round_report
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.transactional_multisession_commit import _commit_verified_candidate


class EvidenceFrontierIteratedExpansionError(RuntimeError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise EvidenceFrontierIteratedExpansionError(f"expected object evidence at {path}")
    return value


def _load_exhaustion_report(root: Path, expansion_seed: str) -> dict[str, Any]:
    exhaustion_seed = f"{expansion_seed}:base"
    path = (
        root
        / ".continual"
        / "evidence"
        / "evidence-frontier-exhaustion"
        / f"evidence-frontier-exhaustion-{_digest(exhaustion_seed)[:16]}.json"
    )
    if not path.is_file():
        raise EvidenceFrontierIteratedExpansionError(
            "first expansion did not persist its exact exhaustion prerequisite"
        )
    report = _load_json(path)
    if not report.get("passed"):
        raise EvidenceFrontierIteratedExpansionError("persisted exhaustion prerequisite did not pass")
    return report


def _reconstruct_first_expansion(
    root: Path,
    *,
    expansion_seed: str,
    expansion_report: Mapping[str, Any],
) -> dict[str, Any]:
    base = _load_exhaustion_report(root, expansion_seed)
    first_signature = str(base["first_selected_semantic_signature"])
    second_signature = str(base["second_selected_semantic_signature"])
    exhausted_signatures = {first_signature, second_signature}
    if len(exhausted_signatures) != 2:
        raise EvidenceFrontierIteratedExpansionError("persisted exhausted frontier collapsed")

    first_seed = _selected_round_seed(base, first_signature)
    second_seed = str(base["second_selected_round_seed"])
    first_round_report = _load_round_report(root, first_seed)
    second_round_report = _load_round_report(root, second_seed)
    first_control = _reconstruct_targets(root, first_round_report)["control"]
    second_control = _reconstruct_targets(root, second_round_report)["control"]
    if str(first_control["semantic_signature"]) != first_signature:
        raise EvidenceFrontierIteratedExpansionError("first exhausted control reconstruction changed")
    if str(second_control["semantic_signature"]) != second_signature:
        raise EvidenceFrontierIteratedExpansionError("second exhausted control reconstruction changed")

    history_digest = _semantic_history_commitment(
        frontier_commitment=str(base["frontier_commitment"]),
        first_signature=first_signature,
        second_signature=second_signature,
        second_control=second_control,
    )
    if history_digest != str(expansion_report["history_digest"]):
        raise EvidenceFrontierIteratedExpansionError(
            "first expansion history commitment cannot be reconstructed"
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
    if str(raw_schedule["schedule_commitment"]) != str(
        expansion_report["raw_derived_schedule_commitment"]
    ):
        raise EvidenceFrontierIteratedExpansionError(
            "first expansion precommitted schedule cannot be reconstructed"
        )
    novel_targets = [
        copy.deepcopy(target)
        for target in raw_schedule["target_schedule"]
        if str(target["semantic_signature"]) not in exhausted_signatures
    ]
    expanded_signature = str(expansion_report["expanded_semantic_signature"])
    control_signature = str(expansion_report["derived_control_semantic_signature"])
    expanded = [
        target for target in novel_targets if str(target["semantic_signature"]) == expanded_signature
    ]
    controls = [
        target for target in novel_targets if str(target["semantic_signature"]) == control_signature
    ]
    if len(expanded) != 1 or len(controls) != 1 or expanded_signature == control_signature:
        raise EvidenceFrontierIteratedExpansionError(
            "first expansion does not identify one learned target and one untouched control"
        )
    return {
        "base": base,
        "first_seed": first_seed,
        "second_seed": second_seed,
        "first_control": first_control,
        "second_control": second_control,
        "expanded_target": expanded[0],
        "untouched_control": controls[0],
    }


def run_evidence_frontier_iterated_expansion(root: Path, seed: str) -> dict[str, Any]:
    """Consume the prior expansion's sealed control as the next objective without forgetting.

    Round one first proves the post-exhaustion expansion milestone. This campaign then reconstructs the
    exact already-precommitted untouched control from the persisted semantic evidence, verifies that the
    first expansion is still supported while that control remains fail-closed, and learns exactly that
    next objective through the unchanged regression/transactional Candidate path. Every earlier
    Candidate byte and trial must remain immutable. A fresh state-only resolver must rediscover the two
    original evidence-selected behaviors plus both successive expansion behaviors without caller-supplied
    Candidate IDs.

    This is bounded repository-authored internal continual-learning evidence, not independent evidence or
    an AGI claim.
    """
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    expansion_seed = f"{seed}:first-expansion"
    first = run_evidence_frontier_expansion(root, expansion_seed)
    if not first.get("passed"):
        raise EvidenceFrontierIteratedExpansionError("first evidence-frontier expansion did not pass")
    reconstructed = _reconstruct_first_expansion(
        root,
        expansion_seed=expansion_seed,
        expansion_report=first,
    )
    base = reconstructed["base"]
    first_control = reconstructed["first_control"]
    second_control = reconstructed["second_control"]
    expanded_target = reconstructed["expanded_target"]
    next_target = reconstructed["untouched_control"]
    first_seed = str(reconstructed["first_seed"])
    second_seed = str(reconstructed["second_seed"])

    first_expansion_candidate_id = str(first["new_candidate_id"])
    prior_ids = _all_candidate_ids(root)
    if first_expansion_candidate_id not in prior_ids:
        raise EvidenceFrontierIteratedExpansionError(
            "first expansion Candidate disappeared before iterative growth"
        )
    prior_tree = _tree_snapshot(root, prior_ids)
    prior_trials = _trial_snapshot(root, prior_ids)
    if not all(prior_trials.get(candidate_id) for candidate_id in prior_ids):
        raise EvidenceFrontierIteratedExpansionError(
            "pre-iteration capability graph contains a Candidate without durable trial evidence"
        )

    expanded_attempt = _attempt(root, expanded_target)
    if not expanded_attempt["supported"]:
        raise EvidenceFrontierIteratedExpansionError(
            "first expansion behavior was forgotten before the next learning round"
        )
    control_attempt = _attempt(root, next_target)
    if control_attempt["supported"]:
        raise EvidenceFrontierIteratedExpansionError(
            "the sealed derived control no longer falsifies the pre-iteration system"
        )
    if control_attempt["candidate_ids_supplied_by_caller"]:
        raise EvidenceFrontierIteratedExpansionError(
            "pre-learning control probe supplied Candidate IDs from the caller"
        )

    challenges = _challenge_inputs(second_seed)
    expected = tuple(execute_program(next_target["program"], value) for value in challenges)
    spec = {
        "name": f"evidence-frontier-iterated-{str(next_target['semantic_signature'])[:12]}",
        "input_domain": str(next_target["input_domain"]),
        "output_domain": str(next_target["output_domain"]),
        "examples": _examples(next_target),
        "probe": challenges[0],
        "expected": expected[0],
        "challenge": challenges[1],
        "challenge_expected": expected[1],
        "max_nodes": int(next_target["program_nodes"]),
    }

    with tempfile.TemporaryDirectory(prefix="agi-evidence-frontier-iterate-learn-") as temporary:
        learner = Path(temporary).resolve()
        _copy_persistent_state(root, learner)
        _assert_prior_unchanged(
            learner,
            prior_ids,
            prior_tree,
            prior_trials,
            label="iterated expansion fresh learner reconstruction",
        )
        if _attempt(learner, next_target)["supported"]:
            raise EvidenceFrontierIteratedExpansionError(
                "sealed next objective became supported before learning"
            )
        learned = _promote_task(
            learner,
            seed=f"{seed}:second-expansion-learn",
            spec=spec,
        )
        candidate_id = str(learned["candidate_id"])
        if candidate_id in prior_ids:
            raise EvidenceFrontierIteratedExpansionError(
                "iterated frontier expansion reused a prior Candidate identity"
            )
        if not learned.get("negative_evidence_retained"):
            raise EvidenceFrontierIteratedExpansionError(
                "iterated expansion discarded its pre-learning negative evidence"
            )
        for value, expected_value in zip(challenges, expected, strict=True):
            if execute_program(learned["program"], value) != expected_value:
                raise EvidenceFrontierIteratedExpansionError(
                    "iterated expansion Candidate failed the sealed held-back behavior"
                )
        _assert_prior_unchanged(
            learner,
            prior_ids,
            prior_tree,
            prior_trials,
            label="iterated expansion learning",
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
        label="iterated expansion transactional commit",
    )
    final_ids = _all_candidate_ids(root)
    if len(final_ids) != len(prior_ids) + 1 or candidate_id not in final_ids:
        raise EvidenceFrontierIteratedExpansionError(
            "iterated expansion did not commit exactly one new Candidate"
        )
    final_tree = _tree_snapshot(root, final_ids)
    final_trials = _trial_snapshot(root, final_ids)
    if not final_trials.get(candidate_id):
        raise EvidenceFrontierIteratedExpansionError(
            "iterated expansion Candidate lacks durable trial evidence"
        )

    first_candidate_id = str(base["first_candidate_id"])
    second_candidate_id = str(base["second_candidate_id"])
    with tempfile.TemporaryDirectory(prefix="agi-evidence-frontier-iterate-resolver-") as temporary:
        resolver = Path(temporary).resolve()
        _copy_persistent_state(root, resolver)
        if _tree_snapshot(resolver, final_ids) != final_tree:
            raise EvidenceFrontierIteratedExpansionError(
                "fresh iterated resolver changed durable Candidate bytes"
            )
        if _trial_snapshot(resolver, final_ids) != final_trials:
            raise EvidenceFrontierIteratedExpansionError(
                "fresh iterated resolver changed durable Candidate trials"
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
            expanded_target,
            required_candidate_id=first_expansion_candidate_id,
            challenge_inputs=challenges,
        )
        second_expansion_replay = _solve_and_replay(
            resolver,
            next_target,
            required_candidate_id=candidate_id,
            challenge_inputs=challenges,
        )
        if _tree_snapshot(resolver, final_ids) != final_tree:
            raise EvidenceFrontierIteratedExpansionError(
                "iterated behavioral replay changed Candidate bytes"
            )
        if _trial_snapshot(resolver, final_ids) != final_trials:
            raise EvidenceFrontierIteratedExpansionError(
                "iterated behavioral replay changed Candidate trials"
            )

    replays = (
        first_replay,
        second_replay,
        first_expansion_replay,
        second_expansion_replay,
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "evidence-frontier-iterated-expansion-v1",
        "seed": seed,
        "first_expansion_digest": str(first["digest"]),
        "second_objective_source": "persisted_first_expansion_untouched_control",
        "fresh_global_target_generator_used_for_second_objective": False,
        "first_expansion_behavior_supported_before_second_learning": True,
        "second_expansion_semantic_signature": str(next_target["semantic_signature"]),
        "second_expansion_failed_closed_before_learning": True,
        "second_expansion_candidate_id": candidate_id,
        "second_expansion_negative_evidence_retained": True,
        "transactional_commit": commit,
        "prior_candidate_state_unchanged": True,
        "prior_trial_state_unchanged": True,
        "first_candidate_id": first_candidate_id,
        "second_candidate_id": second_candidate_id,
        "first_expansion_candidate_id": first_expansion_candidate_id,
        "first_selected_behavior_replay": first_replay,
        "second_selected_behavior_replay": second_replay,
        "first_expansion_behavior_replay": first_expansion_replay,
        "second_expansion_behavior_replay": second_expansion_replay,
        "all_four_capabilities_rediscovered": all(
            required in {str(value) for value in replay["solver_selected_candidate_ids"]}
            for required, replay in (
                (first_candidate_id, first_replay),
                (second_candidate_id, second_replay),
                (first_expansion_candidate_id, first_expansion_replay),
                (candidate_id, second_expansion_replay),
            )
        ),
        "all_replays_avoided_caller_candidate_ids": all(
            replay["solver_candidate_ids_supplied_by_caller"] is False for replay in replays
        ),
        "source_candidate_state_unchanged": True,
        "source_trial_state_unchanged": True,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded iterated evidence-driven continual-learning evidence only. The second "
            "objective is the previously sealed untouched control from the first persisted expansion, "
            "and all four accumulated behaviors must be rediscovered from fresh state without caller "
            "Candidate IDs. The derivation grammar, synthesis, regression, evaluator, probes and scoring "
            "remain repository-authored; this is not open-domain autonomous objective invention, "
            "independent production evidence, or AGI."
        ),
    }
    required = (
        report["fresh_global_target_generator_used_for_second_objective"] is False,
        report["first_expansion_behavior_supported_before_second_learning"],
        report["second_expansion_failed_closed_before_learning"],
        report["second_expansion_negative_evidence_retained"],
        report["prior_candidate_state_unchanged"],
        report["prior_trial_state_unchanged"],
        report["all_four_capabilities_rediscovered"],
        report["all_replays_avoided_caller_candidate_ids"],
    )
    if not all(required):
        raise EvidenceFrontierIteratedExpansionError(
            "iterated evidence-frontier expansion aggregate invariant failed"
        )
    report["digest"] = _digest(report)
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "evidence-frontier-iterated-expansion"
        / f"evidence-frontier-iterated-expansion-{_digest(seed)[:16]}.json",
        report,
    )
    return report
