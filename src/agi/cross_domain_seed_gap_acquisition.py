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
from agi.durable_state_rehydration import _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.seed_committed_gap_acquisition import run_seed_committed_gap_acquisition
from agi.transactional_multisession_commit import _commit_verified_candidate
from agi.verified_macro_synthesis import _load_verified_macro_items


class CrossDomainSeedGapAcquisitionError(RuntimeError):
    pass


def _string_descriptor(expression: Mapping[str, Any]) -> dict[str, Any]:
    descriptor = {
        "input_domain": "string",
        "output_domain": "string",
        "expression": dict(expression),
        "effects": [],
        "max_steps": 32,
        "max_output_length": 2048,
    }
    validate_program_descriptor(descriptor)
    return descriptor


def _expression_key(expression: Mapping[str, Any]) -> str:
    return json.dumps(
        expression,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _bounded_string_grammar(
    support_inputs: Sequence[str],
    *,
    max_nodes: int = 5,
    per_cost_limit: int = 192,
) -> list[dict[str, Any]]:
    """Enumerate bounded input-dependent string expressions with behavior deduplication.

    The grammar is intentionally semantic rather than a catalogue of task names. It combines the
    repository's pure string kernels (case conversion, reversal, and concatenation) and retains only
    expressions whose behavior depends on the input and contains at least one concatenation. The latter
    criterion pushes beyond already-common unary skills while remaining inside the same finite typed
    acquired-program language.
    """

    if not 3 <= len(support_inputs) <= 8:
        raise ValueError("support_inputs must contain 3 through 8 values")
    if not all(isinstance(value, str) and value for value in support_inputs):
        raise ValueError("support_inputs must contain non-empty strings")
    if not 3 <= max_nodes <= 7:
        raise ValueError("max_nodes must be in [3, 7]")
    if not 16 <= per_cost_limit <= 512:
        raise ValueError("per_cost_limit must be in [16, 512]")

    by_cost: dict[int, list[dict[str, Any]]] = {cost: [] for cost in range(1, max_nodes + 1)}
    seen_behavior: set[str] = set()

    def outputs(expression: Mapping[str, Any]) -> tuple[str, ...] | None:
        try:
            descriptor = _string_descriptor(expression)
            values = tuple(str(execute_program(descriptor, value)) for value in support_inputs)
        except (AcquiredProgramError, ValueError, TypeError, OverflowError):
            return None
        return values

    def add(cost: int, expression: dict[str, Any]) -> None:
        values = outputs(expression)
        if values is None:
            return
        signature = _digest({"outputs": values, "phase": "cross-domain-string-grammar-behavior-v1"})
        if signature in seen_behavior:
            return
        seen_behavior.add(signature)
        if len(by_cost[cost]) < per_cost_limit:
            by_cost[cost].append(expression)

    add(1, {"op": "input"})
    unary_ops = ("reverse", "upper", "lower")
    for cost in range(2, max_nodes + 1):
        for op in unary_ops:
            for arg in by_cost[cost - 1]:
                add(cost, {"op": op, "arg": arg})
        for left_cost in range(1, cost - 1):
            right_cost = cost - 1 - left_cost
            if right_cost < 1:
                continue
            for left in by_cost[left_cost]:
                for right in by_cost[right_cost]:
                    add(cost, {"op": "concat_string", "left": left, "right": right})

    result: list[dict[str, Any]] = []
    for cost in range(3, max_nodes + 1):
        for expression in by_cost[cost]:
            key = _expression_key(expression)
            if '"op":"concat_string"' not in key or '"op":"input"' not in key:
                continue
            values = outputs(expression)
            if values is None or len(set(values)) <= 1:
                continue
            result.append(expression)
    if len(result) < 2:
        raise CrossDomainSeedGapAcquisitionError(
            "bounded string grammar generated fewer than two input-dependent concatenative targets"
        )
    return result


def _support_inputs(seed: str) -> tuple[str, ...]:
    token = _digest({"seed": seed, "phase": "cross-domain-string-support-inputs-v1"})
    values = (
        f"a{token[:3]}B",
        f"Q{token[3:6]}z",
        f"mN{token[6:9]}2",
        f"Xy{token[9:12]}K",
    )
    if len(set(values)) != len(values):
        raise CrossDomainSeedGapAcquisitionError("generated string support inputs are not unique")
    return values


def _examples_for(
    expression: Mapping[str, Any],
    support_inputs: Sequence[str],
) -> tuple[ProgramExample, ...]:
    descriptor = _string_descriptor(expression)
    return tuple(
        ProgramExample(value, execute_program(descriptor, value))
        for value in support_inputs
    )


def _library_attempt(root: Path, examples: Sequence[ProgramExample]) -> dict[str, Any]:
    try:
        solved = synthesize_shallowest_verified_route(
            root,
            input_domain="string",
            output_domain="string",
            examples=examples,
            max_depth=4,
            max_candidates=128,
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


def _precommit_string_gap_schedule(
    seed: str,
    *,
    max_target_attempts: int = 16,
) -> dict[str, Any]:
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    if (
        not isinstance(max_target_attempts, int)
        or isinstance(max_target_attempts, bool)
        or not 4 <= max_target_attempts <= 24
    ):
        raise ValueError("max_target_attempts must be an integer in [4, 24]")

    support_inputs = _support_inputs(seed)
    expressions = _bounded_string_grammar(support_inputs, max_nodes=5)
    ranked = sorted(
        expressions,
        key=lambda expression: _digest(
            {
                "seed": seed,
                "expression": expression,
                "phase": "cross-domain-string-target-ranking-v1",
            }
        ),
    )[:max_target_attempts]
    schedule: list[dict[str, Any]] = []
    for index, expression in enumerate(ranked):
        descriptor = _string_descriptor(expression)
        meta = validate_program_descriptor(descriptor)
        examples = _examples_for(expression, support_inputs)
        fingerprint = _digest(
            {
                "input_domain": "string",
                "output_domain": "string",
                "examples": [
                    {"input": item.input, "output": item.output}
                    for item in examples
                ],
                "phase": "cross-domain-string-target-semantics-v1",
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
            "generator": "bounded-generic-string-expression-grammar-v1",
            "schedule": schedule,
            "phase": "cross-domain-string-gap-schedule-v1",
        }
    )
    return {
        "support_inputs": list(support_inputs),
        "target_schedule": schedule,
        "schedule_commitment": commitment,
        "generator_named_fixed_goal_family_used": False,
        "domain": "string",
    }


def _string_challenges(commitment: str) -> tuple[str, ...]:
    token = _digest({"commitment": commitment, "phase": "cross-domain-string-challenges-v1"})
    return (
        f"Probe-{token[:7]}xY",
        f"zK-{token[7:14]}-Novel",
    )


def _numeric_retention_examples(
    resolver_root: Path,
    candidate_id: str,
    seed: str,
) -> tuple[ProgramExample, ...]:
    item = _load_verified_macro_items(resolver_root, (candidate_id,))[0]
    if item["input_domain"] != "numeric" or item["output_domain"] != "numeric":
        raise CrossDomainSeedGapAcquisitionError("numeric prerequisite Candidate changed type")
    token = _digest({"seed": seed, "candidate_id": candidate_id, "phase": "numeric-retention-probes-v1"})
    magnitude = 73 + int(token[:4], 16) % 41
    inputs = (magnitude, -(magnitude + 9), magnitude + 17)
    return tuple(
        ProgramExample(value, execute_program(item["program"], value))
        for value in inputs
    )


def run_cross_domain_seed_gap_acquisition(
    root: Path,
    seed: str,
    *,
    max_target_attempts: int = 16,
) -> dict[str, Any]:
    """Acquire a seed-committed string grammar gap after a numeric generated-gap milestone.

    The prerequisite independently creates and retains a generated numeric skill. This milestone then
    precommits a second target source from a bounded generic string expression grammar before querying
    the verified durable library. At least two precommitted string behaviors must fail closed. The first
    is learned in a fresh state-only root, exact-scope regression gated, and transactionally committed;
    the second is an untouched control gap. A later fresh resolver receives behavior examples but no
    Candidate IDs, rediscovers the new string skill, replays held-back string inputs through fresh Engine
    runs, separately rediscovers the earlier numeric capability, and proves the untouched string control
    remains unsupported. Candidate bytes and trial ledgers must remain immutable throughout.

    This is bounded repository-authored internal development evidence. It increases target-source and
    domain diversity but does not establish independent production evaluation or AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    if (
        not isinstance(max_target_attempts, int)
        or isinstance(max_target_attempts, bool)
        or not 4 <= max_target_attempts <= 24
    ):
        raise ValueError("max_target_attempts must be an integer in [4, 24]")

    root = root.resolve()
    numeric = run_seed_committed_gap_acquisition(
        root,
        f"{seed}:numeric-prerequisite",
        max_target_attempts=12,
    )
    if not numeric.get("passed"):
        raise CrossDomainSeedGapAcquisitionError("numeric generated-gap prerequisite did not pass")
    numeric_candidate_id = str(numeric["candidate_id"])

    prior_ids = _all_candidate_ids(root)
    prior_tree = _tree_snapshot(root, prior_ids)
    prior_trials = _trial_snapshot(root, prior_ids)
    if numeric_candidate_id not in prior_ids:
        raise CrossDomainSeedGapAcquisitionError("numeric prerequisite Candidate is not durable")
    if not prior_ids or not all(prior_trials.get(candidate_id) for candidate_id in prior_ids):
        raise CrossDomainSeedGapAcquisitionError("source library lacks durable Candidate trials")

    schedule = _precommit_string_gap_schedule(
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
        raise CrossDomainSeedGapAcquisitionError(
            "precommitted string grammar exposed fewer than two fail-closed gaps"
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
            "phase": "cross-domain-string-gap-choice-v1",
        }
    )
    challenge_inputs = _string_challenges(choice_commitment)
    hidden_program = acquisition_target["program"]
    challenge_expected = tuple(
        execute_program(hidden_program, value) for value in challenge_inputs
    )
    spec = {
        "name": f"seed-string-grammar-{str(acquisition_target['behavior_fingerprint'])[:12]}",
        "input_domain": "string",
        "output_domain": "string",
        "examples": acquisition_examples,
        "probe": challenge_inputs[0],
        "expected": challenge_expected[0],
        "challenge": challenge_inputs[1],
        "challenge_expected": challenge_expected[1],
        "max_nodes": int(acquisition_target["program_nodes"]),
    }

    with tempfile.TemporaryDirectory(prefix="agi-cross-domain-string-learner-") as temporary:
        learner_root = Path(temporary).resolve()
        _copy_persistent_state(root, learner_root)
        if _tree_snapshot(learner_root, prior_ids) != prior_tree:
            raise CrossDomainSeedGapAcquisitionError("learner restart changed prior Candidate bytes")
        if _trial_snapshot(learner_root, prior_ids) != prior_trials:
            raise CrossDomainSeedGapAcquisitionError("learner restart changed prior Candidate trials")
        if _library_attempt(learner_root, acquisition_examples)["supported"]:
            raise CrossDomainSeedGapAcquisitionError("string target became supported before learning")

        acquired = _promote_task(
            learner_root,
            seed=f"{seed}:string-grammar-acquisition",
            spec=spec,
        )
        candidate_id = str(acquired["candidate_id"])
        scope = str(acquired["scope"])
        if candidate_id in prior_ids:
            raise CrossDomainSeedGapAcquisitionError("string acquisition reused prior Candidate identity")
        if not acquired.get("negative_evidence_retained"):
            raise CrossDomainSeedGapAcquisitionError("string acquisition lost protected negative evidence")
        learned_program = acquired["program"]
        for value, expected in zip(challenge_inputs, challenge_expected, strict=True):
            if execute_program(learned_program, value) != expected:
                raise CrossDomainSeedGapAcquisitionError(
                    "learned string program failed held-back grammar semantics"
                )
        if _tree_snapshot(learner_root, prior_ids) != prior_tree:
            raise CrossDomainSeedGapAcquisitionError("string learning changed prior Candidate bytes")
        if _trial_snapshot(learner_root, prior_ids) != prior_trials:
            raise CrossDomainSeedGapAcquisitionError("string learning changed prior Candidate trials")
        commit = _commit_verified_candidate(
            learner_root,
            root,
            candidate_id=candidate_id,
            scope=scope,
        )

    if _tree_snapshot(root, prior_ids) != prior_tree:
        raise CrossDomainSeedGapAcquisitionError("transactional commit changed prior Candidate bytes")
    if _trial_snapshot(root, prior_ids) != prior_trials:
        raise CrossDomainSeedGapAcquisitionError("transactional commit changed prior Candidate trials")
    enlarged_ids = [*prior_ids, candidate_id]
    enlarged_tree = _tree_snapshot(root, enlarged_ids)
    enlarged_trials = _trial_snapshot(root, enlarged_ids)
    if not enlarged_trials.get(candidate_id):
        raise CrossDomainSeedGapAcquisitionError("committed string Candidate lacks trial evidence")

    with tempfile.TemporaryDirectory(prefix="agi-cross-domain-string-resolver-") as temporary:
        resolver_root = Path(temporary).resolve()
        _copy_persistent_state(root, resolver_root)
        prior_runs_copied = (resolver_root / ".continual" / "runs").exists()
        prior_episodes_copied = (resolver_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (resolver_root / ".continual" / "evidence").exists()
        if _tree_snapshot(resolver_root, enlarged_ids) != enlarged_tree:
            raise CrossDomainSeedGapAcquisitionError("resolver restart changed Candidate bytes")
        if _trial_snapshot(resolver_root, enlarged_ids) != enlarged_trials:
            raise CrossDomainSeedGapAcquisitionError("resolver restart changed Candidate trials")

        solved = synthesize_shallowest_verified_route(
            resolver_root,
            input_domain="string",
            output_domain="string",
            examples=acquisition_examples,
            max_depth=4,
            max_candidates=128,
            max_search_nodes=4096,
            max_behavior_evaluations=2048,
        )
        selected_ids = [str(value) for value in solved["selected_candidate_ids"]]
        if solved.get("candidate_ids_supplied_by_caller") is not False:
            raise CrossDomainSeedGapAcquisitionError("string resolver used caller Candidate IDs")
        if candidate_id not in selected_ids:
            raise CrossDomainSeedGapAcquisitionError("string resolver omitted the new Candidate")
        selected_items = tuple(_load_verified_macro_items(resolver_root, selected_ids))
        fresh_run_ids: list[str] = []
        challenge_outputs: list[dict[str, Any]] = []
        for value, expected in zip(challenge_inputs, challenge_expected, strict=True):
            compiled_output = execute_program(solved["compiled_program"], value)
            runtime_output, run_id, refs = _execute_chain(resolver_root, selected_items, value)
            if compiled_output != expected or runtime_output != expected:
                raise CrossDomainSeedGapAcquisitionError("fresh string replay failed held-back semantics")
            if len(refs) != int(solved["selected_chain_depth"]):
                raise CrossDomainSeedGapAcquisitionError("fresh string replay used wrong learned-stage count")
            fresh_run_ids.append(run_id)
            challenge_outputs.append({"input": value, "expected": expected, "output": runtime_output})
        if len(set(fresh_run_ids)) != len(fresh_run_ids):
            raise CrossDomainSeedGapAcquisitionError("string held-back replay reused an Engine run")

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
            max_candidates=128,
            max_search_nodes=4096,
            max_behavior_evaluations=2048,
        )
        if numeric_solved.get("candidate_ids_supplied_by_caller") is not False:
            raise CrossDomainSeedGapAcquisitionError("numeric retention replay used caller Candidate IDs")
        for example in numeric_examples:
            if execute_program(numeric_solved["compiled_program"], example.input) != example.output:
                raise CrossDomainSeedGapAcquisitionError("string acquisition regressed numeric behavior")

        control_attempt = _library_attempt(resolver_root, control_examples)
        if control_attempt["supported"]:
            raise CrossDomainSeedGapAcquisitionError(
                "one string acquisition unexpectedly solved the untouched string control gap"
            )
        if _tree_snapshot(resolver_root, enlarged_ids) != enlarged_tree:
            raise CrossDomainSeedGapAcquisitionError("fresh validation changed Candidate bytes")
        if _trial_snapshot(resolver_root, enlarged_ids) != enlarged_trials:
            raise CrossDomainSeedGapAcquisitionError("fresh validation changed Candidate trials")

    if _tree_snapshot(root, enlarged_ids) != enlarged_tree:
        raise CrossDomainSeedGapAcquisitionError("fresh validation mutated source Candidate bytes")
    if _trial_snapshot(root, enlarged_ids) != enlarged_trials:
        raise CrossDomainSeedGapAcquisitionError("fresh validation mutated source Candidate trials")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "cross-domain-seed-committed-gap-acquisition-v1",
        "seed": seed,
        "source_domains": ["numeric", "string"],
        "new_target_domain": "string",
        "generator_named_fixed_goal_family_used": False,
        "target_schedule_precommitted_before_support_checks": True,
        "target_schedule_commitment": str(schedule["schedule_commitment"]),
        "support_attempts": support_attempts,
        "acquisition_target_index": int(acquisition_target["target_index"]),
        "acquisition_behavior_fingerprint": str(acquisition_target["behavior_fingerprint"]),
        "control_target_index": int(control_target["target_index"]),
        "control_behavior_fingerprint": str(control_target["behavior_fingerprint"]),
        "acquisition_target_failed_closed_before_learning": True,
        "control_target_failed_closed_before_learning": True,
        "numeric_prerequisite_candidate_id": numeric_candidate_id,
        "numeric_prerequisite_retained_after_string_learning": True,
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
            "Internal bounded cross-domain generated-capability evidence only. Numeric and string target "
            "sources, grammars, synthesis, regression, transaction, runtime, scoring, and evaluator are "
            "repository-authored; the string schedule is seed-committed before support checks and the "
            "new Candidate is exact-scope regression-gated before fresh behavior-only replay. This does "
            "not establish independent production evaluation or AGI."
        ),
    }
    if not all(
        (
            report["target_schedule_precommitted_before_support_checks"],
            report["generator_named_fixed_goal_family_used"] is False,
            report["acquisition_target_failed_closed_before_learning"],
            report["control_target_failed_closed_before_learning"],
            report["numeric_prerequisite_retained_after_string_learning"],
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
        raise CrossDomainSeedGapAcquisitionError("cross-domain gap acquisition aggregate invariant failed")

    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "cross-domain-seed-gap-acquisition"
        / f"cross-domain-gap-{_digest(seed)[:16]}.json",
        report,
    )
    return report
