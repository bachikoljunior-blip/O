from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import (
    AcquiredProgramError,
    ProgramExample,
    execute_program,
    validate_program_descriptor,
)
from agi.adaptive_depth_composition import (
    AdaptiveDepthCompositionError,
    synthesize_shallowest_verified_route,
)
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.cross_domain_seed_gap_acquisition import (
    _numeric_retention_examples,
    run_cross_domain_seed_gap_acquisition,
)
from agi.durable_state_rehydration import _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.transactional_multisession_commit import _commit_verified_candidate
from agi.verified_macro_synthesis import _load_verified_macro_items


class SequenceSeedGapAcquisitionError(RuntimeError):
    pass


def _sequence_descriptor(expression: Mapping[str, Any]) -> dict[str, Any]:
    descriptor = {
        "input_domain": "sequence",
        "output_domain": "sequence",
        "expression": dict(expression),
        "effects": [],
        "max_steps": 32,
        "max_output_length": 256,
    }
    validate_program_descriptor(descriptor)
    return descriptor


def _bounded_sequence_grammar(
    support_inputs: Sequence[Sequence[Any]],
    *,
    max_nodes: int = 7,
) -> list[dict[str, Any]]:
    """Generate input-dependent finite sequence concatenation programs.

    The grammar contains no task names, hidden Candidate IDs, arbitrary code, or ambient effects. It
    starts from the typed input expression and recursively applies the existing pure concat_sequence
    kernel under a strict node bound, deduplicating observed behavior at every cost.
    """

    if not 3 <= len(support_inputs) <= 8:
        raise ValueError("support_inputs must contain 3 through 8 sequences")
    if not 3 <= max_nodes <= 9 or max_nodes % 2 == 0:
        raise ValueError("max_nodes must be an odd integer in [3, 9]")

    by_cost: dict[int, list[dict[str, Any]]] = {cost: [] for cost in range(1, max_nodes + 1)}
    seen_behavior: set[str] = set()

    def outputs(expression: Mapping[str, Any]) -> tuple[str, ...] | None:
        try:
            descriptor = _sequence_descriptor(expression)
            values = tuple(
                json.dumps(
                    execute_program(descriptor, list(value)),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                for value in support_inputs
            )
        except (AcquiredProgramError, ValueError, TypeError, OverflowError):
            return None
        return values

    def add(cost: int, expression: dict[str, Any]) -> None:
        values = outputs(expression)
        if values is None:
            return
        signature = _digest({"outputs": values, "phase": "sequence-seed-grammar-behavior-v1"})
        if signature in seen_behavior:
            return
        seen_behavior.add(signature)
        by_cost[cost].append(expression)

    add(1, {"op": "input"})
    for cost in range(3, max_nodes + 1, 2):
        for left_cost in range(1, cost - 1, 2):
            right_cost = cost - 1 - left_cost
            if right_cost < 1 or right_cost % 2 == 0:
                continue
            for left in by_cost[left_cost]:
                for right in by_cost[right_cost]:
                    add(cost, {"op": "concat_sequence", "left": left, "right": right})

    result: list[dict[str, Any]] = []
    for cost in range(3, max_nodes + 1, 2):
        result.extend(by_cost[cost])
    if len(result) < 2:
        raise SequenceSeedGapAcquisitionError(
            "bounded sequence grammar generated fewer than two distinct concatenative behaviors"
        )
    return result


def _support_inputs(seed: str) -> tuple[list[int], ...]:
    token = _digest({"seed": seed, "phase": "sequence-seed-support-inputs-v1"})
    base = 11 + int(token[:4], 16) % 17
    values = (
        [base, -2],
        [3, base + 1, 0],
        [-(base + 4)],
        [7, -5, base + 3, 2],
    )
    if len({json.dumps(value) for value in values}) != len(values):
        raise SequenceSeedGapAcquisitionError("generated sequence support inputs are not unique")
    return values


def _examples_for(
    expression: Mapping[str, Any],
    support_inputs: Sequence[Sequence[Any]],
) -> tuple[ProgramExample, ...]:
    descriptor = _sequence_descriptor(expression)
    return tuple(
        ProgramExample(list(value), execute_program(descriptor, list(value)))
        for value in support_inputs
    )


def _library_attempt(root: Path, examples: Sequence[ProgramExample]) -> dict[str, Any]:
    try:
        solved = synthesize_shallowest_verified_route(
            root,
            input_domain="sequence",
            output_domain="sequence",
            examples=examples,
            max_depth=4,
            max_candidates=160,
            max_search_nodes=4096,
            max_behavior_evaluations=2048,
        )
    except AdaptiveDepthCompositionError as exc:
        return {
            "supported": False,
            "failure": str(exc),
            "candidate_ids_supplied_by_caller": False,
        }
    return {
        "supported": True,
        "selected_candidate_ids": [str(value) for value in solved["selected_candidate_ids"]],
        "selected_chain_depth": int(solved["selected_chain_depth"]),
        "candidate_ids_supplied_by_caller": bool(solved["candidate_ids_supplied_by_caller"]),
        "digest": str(solved["digest"]),
    }


def _precommit_sequence_gap_schedule(
    seed: str,
    *,
    max_target_attempts: int = 3,
) -> dict[str, Any]:
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    if (
        not isinstance(max_target_attempts, int)
        or isinstance(max_target_attempts, bool)
        or not 2 <= max_target_attempts <= 4
    ):
        raise ValueError("max_target_attempts must be an integer in [2, 4]")

    support_inputs = _support_inputs(seed)
    expressions = _bounded_sequence_grammar(support_inputs, max_nodes=7)
    ranked = sorted(
        expressions,
        key=lambda expression: _digest(
            {
                "seed": seed,
                "expression": expression,
                "phase": "sequence-seed-target-ranking-v1",
            }
        ),
    )[:max_target_attempts]
    schedule: list[dict[str, Any]] = []
    for index, expression in enumerate(ranked):
        descriptor = _sequence_descriptor(expression)
        meta = validate_program_descriptor(descriptor)
        examples = _examples_for(expression, support_inputs)
        fingerprint = _digest(
            {
                "input_domain": "sequence",
                "output_domain": "sequence",
                "examples": [
                    {"input": item.input, "output": item.output}
                    for item in examples
                ],
                "phase": "sequence-seed-target-semantics-v1",
            }
        )
        schedule.append(
            {
                "target_index": index,
                "expression": expression,
                "program": descriptor,
                "program_nodes": int(meta["nodes"]),
                "support_examples": [
                    {"input": item.input, "output": item.output}
                    for item in examples
                ],
                "behavior_fingerprint": fingerprint,
            }
        )
    commitment = _digest(
        {
            "seed": seed,
            "generator": "bounded-generic-sequence-concat-grammar-v1",
            "schedule": schedule,
            "phase": "sequence-seed-gap-schedule-v1",
        }
    )
    return {
        "support_inputs": [list(value) for value in support_inputs],
        "target_schedule": schedule,
        "schedule_commitment": commitment,
        "generator_named_fixed_goal_family_used": False,
        "domain": "sequence",
    }


def _sequence_challenges(commitment: str) -> tuple[list[int], ...]:
    token = _digest({"commitment": commitment, "phase": "sequence-seed-challenges-v1"})
    base = 101 + int(token[:4], 16) % 53
    return (
        [base, -9, 4],
        [-(base + 7), 12, 0, 5],
    )


def _string_retention_examples(
    resolver_root: Path,
    candidate_id: str,
    seed: str,
) -> tuple[ProgramExample, ...]:
    item = _load_verified_macro_items(resolver_root, (candidate_id,))[0]
    if item["input_domain"] != "string" or item["output_domain"] != "string":
        raise SequenceSeedGapAcquisitionError("string prerequisite Candidate changed type")
    token = _digest({"seed": seed, "candidate_id": candidate_id, "phase": "string-retention-probes-v1"})
    inputs = (
        f"Retain-{token[:5]}aB",
        f"qZ-{token[5:10]}-2",
        f"Novel-{token[10:15]}Xy",
    )
    return tuple(
        ProgramExample(value, execute_program(item["program"], value))
        for value in inputs
    )


def run_sequence_seed_gap_acquisition(
    root: Path,
    seed: str,
    *,
    max_target_attempts: int = 3,
) -> dict[str, Any]:
    """Add a third seed-committed target domain and verify cross-domain retention.

    A numeric generated-gap skill and a separately generated string-grammar skill are first established
    by the prior milestone. This campaign then precommits a bounded sequence-expression target schedule
    before any support check, requires two sequence behaviors to fail closed, learns exactly one under
    the ordinary exact-scope regression gate in a fresh state-only learner, transactionally commits it,
    and reconstructs persistent state in a later resolver. The resolver receives behavior examples but
    no Candidate IDs, must rediscover the new sequence skill, must still rediscover the prior string and
    numeric generated skills, and must leave the untouched sequence control gap unsupported.

    All evidence is repository-authored bounded internal development evidence, not independent
    production evaluation and not an AGI claim.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    if (
        not isinstance(max_target_attempts, int)
        or isinstance(max_target_attempts, bool)
        or not 2 <= max_target_attempts <= 4
    ):
        raise ValueError("max_target_attempts must be an integer in [2, 4]")

    root = root.resolve()
    cross_domain = run_cross_domain_seed_gap_acquisition(
        root,
        f"{seed}:cross-domain-prerequisite",
        max_target_attempts=16,
    )
    if not cross_domain.get("passed"):
        raise SequenceSeedGapAcquisitionError("cross-domain prerequisite did not pass")
    numeric_candidate_id = str(cross_domain["numeric_prerequisite_candidate_id"])
    string_candidate_id = str(cross_domain["candidate_id"])

    prior_ids = _all_candidate_ids(root)
    prior_tree = _tree_snapshot(root, prior_ids)
    prior_trials = _trial_snapshot(root, prior_ids)
    if numeric_candidate_id not in prior_ids or string_candidate_id not in prior_ids:
        raise SequenceSeedGapAcquisitionError("cross-domain prerequisite Candidates are not durable")
    if not prior_ids or not all(prior_trials.get(candidate_id) for candidate_id in prior_ids):
        raise SequenceSeedGapAcquisitionError("source library lacks durable Candidate trials")

    schedule = _precommit_sequence_gap_schedule(
        seed,
        max_target_attempts=max_target_attempts,
    )
    support_attempts: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    for target in schedule["target_schedule"]:
        examples = tuple(
            ProgramExample(item["input"], item["output"])
            for item in target["support_examples"]
        )
        attempt = _library_attempt(root, examples)
        record = {
            "target_index": int(target["target_index"]),
            "behavior_fingerprint": str(target["behavior_fingerprint"]),
            "supported_before_learning": bool(attempt["supported"]),
            "candidate_ids_supplied_by_caller": bool(attempt["candidate_ids_supplied_by_caller"]),
        }
        if "failure" in attempt:
            record["fail_closed_reason"] = str(attempt["failure"])
        support_attempts.append(record)
        if not attempt["supported"]:
            unsupported.append(target)
        if len(unsupported) >= 2:
            break
    if len(unsupported) < 2:
        raise SequenceSeedGapAcquisitionError(
            "precommitted sequence grammar exposed fewer than two fail-closed gaps"
        )

    acquisition_target = unsupported[0]
    control_target = unsupported[1]
    acquisition_examples = tuple(
        ProgramExample(item["input"], item["output"])
        for item in acquisition_target["support_examples"]
    )
    control_examples = tuple(
        ProgramExample(item["input"], item["output"])
        for item in control_target["support_examples"]
    )
    choice_commitment = _digest(
        {
            "schedule_commitment": schedule["schedule_commitment"],
            "acquisition_fingerprint": acquisition_target["behavior_fingerprint"],
            "control_fingerprint": control_target["behavior_fingerprint"],
            "phase": "sequence-seed-gap-choice-v1",
        }
    )
    challenge_inputs = _sequence_challenges(choice_commitment)
    hidden_program = acquisition_target["program"]
    challenge_expected = tuple(
        execute_program(hidden_program, value) for value in challenge_inputs
    )
    spec = {
        "name": f"seed-sequence-grammar-{str(acquisition_target['behavior_fingerprint'])[:12]}",
        "input_domain": "sequence",
        "output_domain": "sequence",
        "examples": acquisition_examples,
        "probe": challenge_inputs[0],
        "expected": challenge_expected[0],
        "challenge": challenge_inputs[1],
        "challenge_expected": challenge_expected[1],
        "max_nodes": int(acquisition_target["program_nodes"]),
    }

    with tempfile.TemporaryDirectory(prefix="agi-sequence-gap-learner-") as temporary:
        learner_root = Path(temporary).resolve()
        _copy_persistent_state(root, learner_root)
        if _tree_snapshot(learner_root, prior_ids) != prior_tree:
            raise SequenceSeedGapAcquisitionError("learner restart changed prior Candidate bytes")
        if _trial_snapshot(learner_root, prior_ids) != prior_trials:
            raise SequenceSeedGapAcquisitionError("learner restart changed prior Candidate trials")
        if _library_attempt(learner_root, acquisition_examples)["supported"]:
            raise SequenceSeedGapAcquisitionError("sequence target became supported before learning")

        acquired = _promote_task(
            learner_root,
            seed=f"{seed}:sequence-grammar-acquisition",
            spec=spec,
        )
        candidate_id = str(acquired["candidate_id"])
        scope = str(acquired["scope"])
        if candidate_id in prior_ids:
            raise SequenceSeedGapAcquisitionError("sequence acquisition reused prior Candidate identity")
        if not acquired.get("negative_evidence_retained"):
            raise SequenceSeedGapAcquisitionError("sequence acquisition lost protected negative evidence")
        learned_program = acquired["program"]
        for value, expected in zip(challenge_inputs, challenge_expected, strict=True):
            if execute_program(learned_program, value) != expected:
                raise SequenceSeedGapAcquisitionError(
                    "learned sequence program failed held-back grammar semantics"
                )
        if _tree_snapshot(learner_root, prior_ids) != prior_tree:
            raise SequenceSeedGapAcquisitionError("sequence learning changed prior Candidate bytes")
        if _trial_snapshot(learner_root, prior_ids) != prior_trials:
            raise SequenceSeedGapAcquisitionError("sequence learning changed prior Candidate trials")
        commit = _commit_verified_candidate(
            learner_root,
            root,
            candidate_id=candidate_id,
            scope=scope,
        )

    if _tree_snapshot(root, prior_ids) != prior_tree:
        raise SequenceSeedGapAcquisitionError("transactional commit changed prior Candidate bytes")
    if _trial_snapshot(root, prior_ids) != prior_trials:
        raise SequenceSeedGapAcquisitionError("transactional commit changed prior Candidate trials")
    enlarged_ids = [*prior_ids, candidate_id]
    enlarged_tree = _tree_snapshot(root, enlarged_ids)
    enlarged_trials = _trial_snapshot(root, enlarged_ids)
    if not enlarged_trials.get(candidate_id):
        raise SequenceSeedGapAcquisitionError("committed sequence Candidate lacks trial evidence")

    with tempfile.TemporaryDirectory(prefix="agi-sequence-gap-resolver-") as temporary:
        resolver_root = Path(temporary).resolve()
        _copy_persistent_state(root, resolver_root)
        prior_runs_copied = (resolver_root / ".continual" / "runs").exists()
        prior_episodes_copied = (resolver_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (resolver_root / ".continual" / "evidence").exists()
        if _tree_snapshot(resolver_root, enlarged_ids) != enlarged_tree:
            raise SequenceSeedGapAcquisitionError("resolver restart changed Candidate bytes")
        if _trial_snapshot(resolver_root, enlarged_ids) != enlarged_trials:
            raise SequenceSeedGapAcquisitionError("resolver restart changed Candidate trials")

        solved = synthesize_shallowest_verified_route(
            resolver_root,
            input_domain="sequence",
            output_domain="sequence",
            examples=acquisition_examples,
            max_depth=4,
            max_candidates=160,
            max_search_nodes=4096,
            max_behavior_evaluations=2048,
        )
        selected_ids = [str(value) for value in solved["selected_candidate_ids"]]
        if solved.get("candidate_ids_supplied_by_caller") is not False:
            raise SequenceSeedGapAcquisitionError("sequence resolver used caller Candidate IDs")
        if candidate_id not in selected_ids:
            raise SequenceSeedGapAcquisitionError("sequence resolver omitted the new Candidate")
        selected_items = tuple(_load_verified_macro_items(resolver_root, selected_ids))
        fresh_run_ids: list[str] = []
        challenge_outputs: list[dict[str, Any]] = []
        for value, expected in zip(challenge_inputs, challenge_expected, strict=True):
            compiled_output = execute_program(solved["compiled_program"], value)
            runtime_output, run_id, refs = _execute_chain(resolver_root, selected_items, value)
            if compiled_output != expected or runtime_output != expected:
                raise SequenceSeedGapAcquisitionError("fresh sequence replay failed held-back semantics")
            if len(refs) != int(solved["selected_chain_depth"]):
                raise SequenceSeedGapAcquisitionError("fresh sequence replay used wrong learned-stage count")
            fresh_run_ids.append(run_id)
            challenge_outputs.append({"input": value, "expected": expected, "output": runtime_output})
        if len(set(fresh_run_ids)) != len(fresh_run_ids):
            raise SequenceSeedGapAcquisitionError("sequence held-back replay reused an Engine run")

        string_examples = _string_retention_examples(resolver_root, string_candidate_id, seed)
        string_solved = synthesize_shallowest_verified_route(
            resolver_root,
            input_domain="string",
            output_domain="string",
            examples=string_examples,
            max_depth=4,
            max_candidates=160,
            max_search_nodes=4096,
            max_behavior_evaluations=2048,
        )
        if string_solved.get("candidate_ids_supplied_by_caller") is not False:
            raise SequenceSeedGapAcquisitionError("string retention replay used caller Candidate IDs")
        for example in string_examples:
            if execute_program(string_solved["compiled_program"], example.input) != example.output:
                raise SequenceSeedGapAcquisitionError("sequence learning regressed string behavior")

        numeric_examples = _numeric_retention_examples(
            resolver_root,
            numeric_candidate_id,
            seed,
        )
        numeric_solved = synthesize_shallowest_verified_route(
            resolver_root,
            input_domain="numeric",
            output_domain="numeric",
            examples=numeric_examples,
            max_depth=4,
            max_candidates=160,
            max_search_nodes=4096,
            max_behavior_evaluations=2048,
        )
        if numeric_solved.get("candidate_ids_supplied_by_caller") is not False:
            raise SequenceSeedGapAcquisitionError("numeric retention replay used caller Candidate IDs")
        for example in numeric_examples:
            if execute_program(numeric_solved["compiled_program"], example.input) != example.output:
                raise SequenceSeedGapAcquisitionError("sequence learning regressed numeric behavior")

        control_attempt = _library_attempt(resolver_root, control_examples)
        if control_attempt["supported"]:
            raise SequenceSeedGapAcquisitionError(
                "one sequence acquisition unexpectedly solved the untouched sequence control gap"
            )
        if _tree_snapshot(resolver_root, enlarged_ids) != enlarged_tree:
            raise SequenceSeedGapAcquisitionError("fresh validation changed Candidate bytes")
        if _trial_snapshot(resolver_root, enlarged_ids) != enlarged_trials:
            raise SequenceSeedGapAcquisitionError("fresh validation changed Candidate trials")

    if _tree_snapshot(root, enlarged_ids) != enlarged_tree:
        raise SequenceSeedGapAcquisitionError("fresh validation mutated source Candidate bytes")
    if _trial_snapshot(root, enlarged_ids) != enlarged_trials:
        raise SequenceSeedGapAcquisitionError("fresh validation mutated source Candidate trials")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "sequence-seed-committed-gap-acquisition-v1",
        "seed": seed,
        "source_domains": ["numeric", "string", "sequence"],
        "new_target_domain": "sequence",
        "generator_named_fixed_goal_family_used": False,
        "target_schedule_precommitted_before_support_checks": True,
        "target_schedule_commitment": str(schedule["schedule_commitment"]),
        "support_attempts": support_attempts,
        "acquisition_target_index": int(acquisition_target["target_index"]),
        "control_target_index": int(control_target["target_index"]),
        "acquisition_target_failed_closed_before_learning": True,
        "control_target_failed_closed_before_learning": True,
        "numeric_prerequisite_candidate_id": numeric_candidate_id,
        "string_prerequisite_candidate_id": string_candidate_id,
        "numeric_prerequisite_retained_after_sequence_learning": True,
        "string_prerequisite_retained_after_sequence_learning": True,
        "candidate_id": candidate_id,
        "scope": scope,
        "new_candidate_negative_evidence_retained": True,
        "transactional_commit": commit,
        "solver_candidate_ids_supplied_by_caller": False,
        "solver_selected_candidate_ids": selected_ids,
        "solver_selected_chain_depth": int(solved["selected_chain_depth"]),
        "new_candidate_rediscovered": candidate_id in selected_ids,
        "heldback_challenge_outputs": challenge_outputs,
        "fresh_engine_runs": fresh_run_ids,
        "fresh_engine_runs_unique": len(set(fresh_run_ids)) == len(fresh_run_ids),
        "control_gap_remained_failed_closed": True,
        "prior_candidate_state_unchanged": True,
        "prior_trial_state_unchanged": True,
        "source_candidate_state_unchanged": True,
        "source_trial_state_unchanged": True,
        "prior_runs_copied": prior_runs_copied,
        "prior_episodes_copied": prior_episodes_copied,
        "prior_evidence_copied": prior_evidence_copied,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded three-domain generated-capability evidence only. Numeric, string, and "
            "sequence generators, grammars, synthesis, regression, persistence, runtime, scoring, and "
            "evaluator remain repository-authored. This does not establish independent production "
            "evaluation or AGI."
        ),
    }
    if not all(
        (
            report["target_schedule_precommitted_before_support_checks"],
            report["generator_named_fixed_goal_family_used"] is False,
            report["acquisition_target_failed_closed_before_learning"],
            report["control_target_failed_closed_before_learning"],
            report["numeric_prerequisite_retained_after_sequence_learning"],
            report["string_prerequisite_retained_after_sequence_learning"],
            report["new_candidate_rediscovered"],
            report["fresh_engine_runs_unique"],
            report["control_gap_remained_failed_closed"],
            report["prior_candidate_state_unchanged"],
            report["prior_trial_state_unchanged"],
            not report["prior_runs_copied"],
            not report["prior_episodes_copied"],
            not report["prior_evidence_copied"],
        )
    ):
        raise SequenceSeedGapAcquisitionError("sequence gap acquisition aggregate invariant failed")

    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "sequence-seed-gap-acquisition"
        / f"sequence-gap-{_digest(seed)[:16]}.json",
        report,
    )
    return report
