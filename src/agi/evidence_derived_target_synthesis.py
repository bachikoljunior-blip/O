from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program, validate_program_descriptor
from agi.adaptive_depth_composition import _challenge_inputs
from agi.durable_state_rehydration import _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.persisted_failure_curriculum import (
    _assert_unchanged,
    _examples,
    _solve_and_replay,
    _target_by_index,
    run_persisted_failure_curriculum,
)
from agi.seed_committed_gap_acquisition import (
    _library_attempt,
    _numeric_descriptor,
    _precommit_gap_schedule,
)
from agi.transactional_multisession_commit import _commit_verified_candidate


class EvidenceDerivedTargetSynthesisError(RuntimeError):
    pass


def _semantic_signature(target: Mapping[str, Any]) -> str:
    return _digest(
        {
            "input_domain": str(target["input_domain"]),
            "output_domain": str(target["output_domain"]),
            "support_examples": list(target["support_examples"]),
            "phase": "canonical-support-behavior-v1",
        }
    )


def _derived_target(expression: Mapping[str, Any], support_inputs: Sequence[int]) -> dict[str, Any]:
    descriptor = _numeric_descriptor(expression)
    meta = validate_program_descriptor(descriptor)
    examples = [
        {"input": value, "output": execute_program(descriptor, value)}
        for value in support_inputs
    ]
    if len({_digest(item["output"]) for item in examples}) <= 1:
        raise EvidenceDerivedTargetSynthesisError("derived target became constant on support inputs")
    target: dict[str, Any] = {
        "expression": dict(expression),
        "program": descriptor,
        "program_nodes": int(meta["nodes"]),
        "input_domain": "numeric",
        "output_domain": "numeric",
        "support_examples": examples,
    }
    target["semantic_signature"] = _semantic_signature(target)
    target["behavior_fingerprint"] = _digest(
        {
            "semantic_signature": target["semantic_signature"],
            "expression": target["expression"],
            "phase": "evidence-derived-target-semantics-v1",
        }
    )
    return target


def _candidate_expressions(base: Mapping[str, Any], salt: int) -> list[dict[str, Any]]:
    def const(value: int) -> dict[str, Any]:
        return {"op": "const", "domain": "numeric", "value": value}

    inp = {"op": "input"}
    magnitude = 2 + salt % 4
    return [
        {"op": "neg", "arg": dict(base)},
        {"op": "abs", "arg": dict(base)},
        {"op": "add", "left": dict(base), "right": const(magnitude)},
        {"op": "sub", "left": dict(base), "right": const(magnitude)},
        {"op": "add", "left": dict(base), "right": inp},
        {"op": "sub", "left": inp, "right": dict(base)},
        {"op": "mul", "left": dict(base), "right": const(2)},
        {"op": "mul", "left": dict(base), "right": const(-1)},
        {"op": "add", "left": {"op": "abs", "arg": dict(base)}, "right": const(magnitude)},
        {"op": "sub", "left": {"op": "neg", "arg": dict(base)}, "right": const(magnitude)},
        {"op": "mul", "left": dict(base), "right": inp},
        {"op": "add", "left": {"op": "mul", "left": dict(base), "right": const(2)}, "right": const(1)},
    ]


def _precommit_evidence_derived_schedule(
    *,
    source_target: Mapping[str, Any],
    source_evidence_digest: str,
    source_schedule_signatures: set[str],
    support_inputs: Sequence[int],
) -> dict[str, Any]:
    salt = int(source_evidence_digest[:8], 16)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for expression in _candidate_expressions(source_target["expression"], salt):
        try:
            target = _derived_target(expression, support_inputs)
        except (ValueError, TypeError, EvidenceDerivedTargetSynthesisError):
            continue
        if int(target["program_nodes"]) > 9:
            continue
        signature = str(target["semantic_signature"])
        if signature in source_schedule_signatures or signature in seen:
            continue
        seen.add(signature)
        items.append(target)
    items.sort(
        key=lambda item: _digest(
            {
                "source_evidence_digest": source_evidence_digest,
                "semantic_signature": item["semantic_signature"],
                "expression": item["expression"],
                "phase": "evidence-derived-target-rank-v1",
            }
        )
    )
    if len(items) < 2:
        raise EvidenceDerivedTargetSynthesisError(
            "persisted failure produced fewer than two novel derived target behaviors"
        )
    for index, item in enumerate(items):
        item["target_index"] = index
    commitment = _digest(
        {
            "source_evidence_digest": source_evidence_digest,
            "source_semantic_signature": _semantic_signature(source_target),
            "targets": items,
            "phase": "evidence-derived-target-schedule-v1",
        }
    )
    return {
        "target_schedule": items,
        "schedule_commitment": commitment,
        "source_semantic_signature": _semantic_signature(source_target),
    }


def run_evidence_derived_target_synthesis(root: Path, seed: str) -> dict[str, Any]:
    """Derive new bounded target behaviors from durable failure structure, not the prior finite schedule."""
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    prior = run_persisted_failure_curriculum(root, f"{seed}:persisted-curriculum")
    if not prior.get("passed"):
        raise EvidenceDerivedTargetSynthesisError("persisted-failure prerequisite did not pass")

    source_evidence_path = root / str(prior["source_evidence_path"])
    if not source_evidence_path.is_file():
        raise EvidenceDerivedTargetSynthesisError("persisted source failure evidence disappeared")
    source_evidence = json.loads(source_evidence_path.read_text(encoding="utf-8"))
    if source_evidence.get("digest") != prior["source_evidence_digest"]:
        raise EvidenceDerivedTargetSynthesisError("persisted source failure evidence digest changed")

    evidence_seed = f"{seed}:persisted-curriculum:evidence-source"
    source_schedule = _precommit_gap_schedule(root, evidence_seed, max_target_attempts=24)
    if source_schedule["schedule_commitment"] != source_evidence["target_schedule_commitment"]:
        raise EvidenceDerivedTargetSynthesisError("source failure schedule reconstruction changed")
    normalized_source_targets = [
        _target_by_index(source_schedule, int(raw["target_index"]))
        for raw in source_schedule["target_schedule"]
    ]
    source_signatures = {_semantic_signature(item) for item in normalized_source_targets}
    source_target = _target_by_index(
        source_schedule, int(prior["untouched_control_target_index"])
    )
    if source_target["behavior_fingerprint"] != prior["untouched_control_behavior_fingerprint"]:
        raise EvidenceDerivedTargetSynthesisError("persisted untouched failure fingerprint changed")
    if _library_attempt(root, _examples(source_target)).get("supported"):
        raise EvidenceDerivedTargetSynthesisError(
            "persisted untouched failure became supported before derived-target planning"
        )

    derived = _precommit_evidence_derived_schedule(
        source_target=source_target,
        source_evidence_digest=str(source_evidence["digest"]),
        source_schedule_signatures=source_signatures,
        support_inputs=tuple(int(value) for value in source_schedule["support_inputs"]),
    )
    unsupported: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    for target in derived["target_schedule"]:
        attempt = _library_attempt(root, _examples(target))
        attempts.append(
            {
                "target_index": int(target["target_index"]),
                "semantic_signature": str(target["semantic_signature"]),
                "behavior_fingerprint": str(target["behavior_fingerprint"]),
                "supported_before_learning": bool(attempt["supported"]),
                "candidate_ids_supplied_by_caller": bool(attempt["candidate_ids_supplied_by_caller"]),
            }
        )
        if not attempt["supported"]:
            unsupported.append(target)
        if len(unsupported) >= 2:
            break
    if len(unsupported) < 2:
        raise EvidenceDerivedTargetSynthesisError(
            "precommitted evidence-derived schedule exposed fewer than two fail-closed gaps"
        )
    target, control = unsupported[:2]
    if target["semantic_signature"] in source_signatures:
        raise EvidenceDerivedTargetSynthesisError("derived acquisition behavior reused source schedule semantics")

    prior_ids = _all_candidate_ids(root)
    prior_tree = _tree_snapshot(root, prior_ids)
    prior_trials = _trial_snapshot(root, prior_ids)
    prior_candidate_id = str(prior["new_candidate_id"])
    if prior_candidate_id not in prior_ids:
        raise EvidenceDerivedTargetSynthesisError("persisted-curriculum Candidate was not retained")

    commitment = _digest(
        {
            "schedule_commitment": derived["schedule_commitment"],
            "acquisition_semantic_signature": target["semantic_signature"],
            "control_semantic_signature": control["semantic_signature"],
            "phase": "evidence-derived-target-choice-v1",
        }
    )
    challenges = tuple(_challenge_inputs("numeric", commitment))
    challenge_expected = tuple(execute_program(target["program"], value) for value in challenges)
    spec = {
        "name": f"evidence-derived-{str(target['semantic_signature'])[:12]}",
        "input_domain": "numeric",
        "output_domain": "numeric",
        "examples": _examples(target),
        "probe": challenges[0],
        "expected": challenge_expected[0],
        "challenge": challenges[1],
        "challenge_expected": challenge_expected[1],
        "max_nodes": int(target["program_nodes"]),
    }

    with tempfile.TemporaryDirectory(prefix="agi-evidence-derived-learn-") as temporary:
        learner = Path(temporary).resolve()
        _copy_persistent_state(root, learner)
        _assert_unchanged(
            learner, prior_ids, prior_tree, prior_trials, label="evidence-derived learner reconstruction"
        )
        if _library_attempt(learner, _examples(target)).get("supported"):
            raise EvidenceDerivedTargetSynthesisError("derived target became supported before learning")
        learned = _promote_task(learner, seed=f"{seed}:learn", spec=spec)
        candidate_id = str(learned["candidate_id"])
        if candidate_id in prior_ids or not learned.get("negative_evidence_retained"):
            raise EvidenceDerivedTargetSynthesisError("derived Candidate identity/evidence invariant failed")
        for value, expected_value in zip(challenges, challenge_expected, strict=True):
            if execute_program(learned["program"], value) != expected_value:
                raise EvidenceDerivedTargetSynthesisError("derived Candidate failed held-back semantics")
        _assert_unchanged(
            learner, prior_ids, prior_tree, prior_trials, label="evidence-derived learning"
        )
        commit = _commit_verified_candidate(
            learner, root, candidate_id=candidate_id, scope=str(learned["scope"])
        )

    _assert_unchanged(root, prior_ids, prior_tree, prior_trials, label="derived transactional commit")
    final_ids = _all_candidate_ids(root)
    final_tree = _tree_snapshot(root, final_ids)
    final_trials = _trial_snapshot(root, final_ids)
    if candidate_id not in final_ids or not final_trials.get(candidate_id):
        raise EvidenceDerivedTargetSynthesisError("derived Candidate lacks durable trial evidence")

    with tempfile.TemporaryDirectory(prefix="agi-evidence-derived-resolver-") as temporary:
        resolver = Path(temporary).resolve()
        _copy_persistent_state(root, resolver)
        transfer = _solve_and_replay(
            resolver,
            target=target,
            commitment=commitment,
            required_candidate_id=candidate_id,
        )
        previous_target = _target_by_index(
            source_schedule, int(prior["persisted_control_target_index"])
        )
        previous_retention = _solve_and_replay(
            resolver,
            target=previous_target,
            commitment=str(prior["next_objective_commitment"]),
            required_candidate_id=prior_candidate_id,
        )
        if _library_attempt(resolver, _examples(control)).get("supported"):
            raise EvidenceDerivedTargetSynthesisError("derived control gap was overgeneralized")
        if _library_attempt(resolver, _examples(source_target)).get("supported"):
            raise EvidenceDerivedTargetSynthesisError(
                "source persisted untouched control was overgeneralized"
            )
        _assert_unchanged(
            resolver, final_ids, final_tree, final_trials, label="derived fresh resolver"
        )
    _assert_unchanged(root, final_ids, final_tree, final_trials, label="derived durable source")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "evidence-derived-target-synthesis-v1",
        "seed": seed,
        "source_curriculum_digest": str(prior["digest"]),
        "source_failure_evidence_digest": str(source_evidence["digest"]),
        "source_failure_schedule_reconstructed": True,
        "target_generation_source": "persisted_failure_structure",
        "fresh_global_grammar_enumeration_used_for_derived_targets": False,
        "finite_source_schedule_target_replayed_as_new_objective": False,
        "source_semantic_signature": _semantic_signature(source_target),
        "derived_schedule_precommitted_before_support_checks": True,
        "derived_schedule_commitment": str(derived["schedule_commitment"]),
        "support_attempts": attempts,
        "derived_acquisition_semantic_signature": str(target["semantic_signature"]),
        "derived_acquisition_behavior_fingerprint": str(target["behavior_fingerprint"]),
        "derived_target_absent_from_source_schedule": target["semantic_signature"] not in source_signatures,
        "derived_target_failed_closed_before_learning": True,
        "derived_control_semantic_signature": str(control["semantic_signature"]),
        "derived_control_failed_closed_before_learning": True,
        "new_candidate_id": candidate_id,
        "new_candidate_negative_evidence_retained": True,
        "transactional_commit": commit,
        "prior_candidate_state_unchanged": True,
        "prior_trial_state_unchanged": True,
        "fresh_behavior_only_transfer": transfer,
        "new_candidate_rediscovered_without_caller_ids": candidate_id
        in transfer["solver_selected_candidate_ids"],
        "previous_evidence_selected_candidate_id": prior_candidate_id,
        "previous_evidence_selected_behavior_retention": previous_retention,
        "previous_evidence_selected_behavior_retained": prior_candidate_id
        in previous_retention["solver_selected_candidate_ids"],
        "derived_control_gap_remained_failed_closed": True,
        "source_persisted_control_gap_remained_failed_closed": True,
        "source_candidate_state_unchanged": True,
        "source_trial_state_unchanged": True,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded evidence-derived target synthesis only. A new finite target schedule is "
            "constructed deterministically from the structure of a persisted fail-closed behavior and its "
            "evidence digest, and canonical support semantics exclude behaviors already in the original "
            "finite source schedule. It remains repository-authored, bounded, numeric, and internally "
            "evaluated; this is not open-domain autonomous objective invention, independent production "
            "evidence, or AGI."
        ),
    }
    if not all(
        (
            report["source_failure_schedule_reconstructed"],
            report["derived_schedule_precommitted_before_support_checks"],
            report["derived_target_absent_from_source_schedule"],
            report["derived_target_failed_closed_before_learning"],
            report["derived_control_failed_closed_before_learning"],
            report["new_candidate_negative_evidence_retained"],
            report["prior_candidate_state_unchanged"],
            report["prior_trial_state_unchanged"],
            report["new_candidate_rediscovered_without_caller_ids"],
            report["previous_evidence_selected_behavior_retained"],
            report["derived_control_gap_remained_failed_closed"],
            report["source_persisted_control_gap_remained_failed_closed"],
            report["source_candidate_state_unchanged"],
            report["source_trial_state_unchanged"],
        )
    ):
        raise EvidenceDerivedTargetSynthesisError("evidence-derived aggregate invariant failed")
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "evidence-derived-target-synthesis"
        / f"evidence-derived-{_digest(seed)[:16]}.json",
        report,
    )
    return report
