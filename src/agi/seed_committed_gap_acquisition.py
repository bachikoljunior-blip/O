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
    _challenge_inputs,
    synthesize_shallowest_verified_route,
)
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.durable_state_rehydration import _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.seed_committed_target_generator import _precommit_target_plan
from agi.transactional_multisession_commit import _commit_verified_candidate
from agi.verified_macro_synthesis import _load_verified_macro_items


class SeedCommittedGapAcquisitionError(RuntimeError):
    pass


def _numeric_descriptor(expression: Mapping[str, Any]) -> dict[str, Any]:
    descriptor = {
        "input_domain": "numeric",
        "output_domain": "numeric",
        "expression": dict(expression),
        "effects": [],
        "max_steps": 32,
        "max_output_length": 1024,
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


def _bounded_numeric_grammar(
    support_inputs: Sequence[int],
    *,
    max_nodes: int = 5,
    per_cost_limit: int = 256,
) -> list[dict[str, Any]]:
    """Enumerate a bounded generic numeric expression grammar with behavior deduplication."""

    if not 3 <= len(support_inputs) <= 8:
        raise ValueError("support_inputs must contain 3 through 8 values")
    if not 2 <= max_nodes <= 6:
        raise ValueError("max_nodes must be in [2, 6]")
    by_cost: dict[int, list[dict[str, Any]]] = {cost: [] for cost in range(1, max_nodes + 1)}
    seen_behavior: set[str] = set()

    def outputs(expression: Mapping[str, Any]) -> tuple[Any, ...] | None:
        try:
            descriptor = _numeric_descriptor(expression)
            values = tuple(execute_program(descriptor, value) for value in support_inputs)
        except (AcquiredProgramError, ValueError, TypeError, OverflowError):
            return None
        return values

    def add(cost: int, expression: dict[str, Any]) -> None:
        values = outputs(expression)
        if values is None:
            return
        signature = _digest({"outputs": values, "phase": "seed-grammar-behavior-v1"})
        if signature in seen_behavior:
            return
        seen_behavior.add(signature)
        if len(by_cost[cost]) < per_cost_limit:
            by_cost[cost].append(expression)

    add(1, {"op": "input"})
    for value in range(-5, 6):
        add(1, {"op": "const", "domain": "numeric", "value": value})

    unary_ops = ("neg", "abs")
    binary_ops = ("add", "sub", "mul")
    for cost in range(2, max_nodes + 1):
        for op in unary_ops:
            for arg in by_cost[cost - 1]:
                add(cost, {"op": op, "arg": arg})
        for left_cost in range(1, cost - 1):
            right_cost = cost - 1 - left_cost
            if right_cost < 1:
                continue
            for op in binary_ops:
                for left in by_cost[left_cost]:
                    for right in by_cost[right_cost]:
                        add(cost, {"op": op, "left": left, "right": right})

    result: list[dict[str, Any]] = []
    for cost in range(2, max_nodes + 1):
        for expression in by_cost[cost]:
            values = outputs(expression)
            if values is None or len({_digest(value) for value in values}) <= 1:
                continue
            if '"op":"input"' not in _expression_key(expression):
                continue
            result.append(expression)
    if not result:
        raise SeedCommittedGapAcquisitionError("bounded numeric grammar generated no nonconstant targets")
    return result


def _support_inputs(seed: str) -> tuple[int, ...]:
    token = _digest({"seed": seed, "phase": "seed-grammar-support-inputs-v1"})
    base = 7 + int(token[:4], 16) % 11
    values = (-(base + 5), -3, 2, base, base + 7)
    if len(set(values)) != len(values):  # pragma: no cover - arithmetic construction is unique
        raise SeedCommittedGapAcquisitionError("generated support inputs are not unique")
    return values


def _examples_for(expression: Mapping[str, Any], support_inputs: Sequence[int]) -> tuple[ProgramExample, ...]:
    descriptor = _numeric_descriptor(expression)
    return tuple(
        ProgramExample(value, execute_program(descriptor, value))
        for value in support_inputs
    )


def _library_attempt(root: Path, examples: Sequence[ProgramExample]) -> dict[str, Any]:
    try:
        solved = synthesize_shallowest_verified_route(
            root,
            input_domain="numeric",
            output_domain="numeric",
            examples=examples,
            max_depth=4,
            max_candidates=64,
            max_search_nodes=512,
            max_behavior_evaluations=256,
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


def _precommit_gap_schedule(
    root: Path,
    seed: str,
    *,
    max_target_attempts: int,
) -> dict[str, Any]:
    support_inputs = _support_inputs(seed)
    expressions = _bounded_numeric_grammar(support_inputs, max_nodes=5)
    ranked = sorted(
        expressions,
        key=lambda expression: _digest(
            {
                "seed": seed,
                "expression": expression,
                "phase": "seed-grammar-target-ranking-v1",
            }
        ),
    )[:max_target_attempts]
    schedule = []
    for index, expression in enumerate(ranked):
        descriptor = _numeric_descriptor(expression)
        meta = validate_program_descriptor(descriptor)
        examples = _examples_for(expression, support_inputs)
        fingerprint = _digest(
            {
                "input_domain": "numeric",
                "output_domain": "numeric",
                "examples": [
                    {"input": item.input, "output": item.output}
                    for item in examples
                ],
                "phase": "seed-grammar-target-semantics-v1",
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
            "generator": "bounded-generic-numeric-expression-grammar-v1",
            "schedule": schedule,
            "phase": "seed-grammar-gap-schedule-v1",
        }
    )
    return {
        "support_inputs": list(support_inputs),
        "target_schedule": schedule,
        "schedule_commitment": commitment,
        "generator_named_fixed_goal_family_used": False,
    }


def _program_expected(program: Mapping[str, Any], value: Any) -> Any:
    return execute_program(program, value)


def run_seed_committed_gap_acquisition(
    root: Path,
    seed: str,
    *,
    max_target_attempts: int = 12,
) -> dict[str, Any]:
    """Acquire one seed-generated grammar target that the current verified library cannot solve safely.

    The exact target schedule is committed before any library support check. Targets are generic bounded
    numeric expression programs generated from the same pure typed grammar used for acquired programs,
    not selected from a named task family. The existing verified durable library is queried in schedule
    order without Candidate IDs; the first two fail-closed targets become the acquisition target and an
    untouched control gap. The acquisition target is then synthesized and exact-scope regression-gated
    in a fresh state-only learner, transactionally committed, rediscovered without caller Candidate IDs
    after another restart, and tested on held-back inputs through fresh Engine execution. A pre-existing
    generated skill-graph target must still replay correctly and the second generated grammar gap must
    remain fail-closed.

    This is repository-authored bounded internal development evidence. It does not establish independent
    production evaluation or AGI.
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
    # Bootstrap and freeze a nontrivial verified skill graph using the immediately preceding milestone.
    protected_plan = _precommit_target_plan(
        root,
        f"{seed}:protected-skill-graph",
        max_target_attempts=2,
        max_generation_depth=3,
        max_generation_search_nodes=512,
    )
    if not protected_plan["target_plan"]:
        raise SeedCommittedGapAcquisitionError("protected skill-graph target plan is empty")
    protected_target = dict(protected_plan["target_plan"][0])

    prior_ids = _all_candidate_ids(root)
    prior_tree = _tree_snapshot(root, prior_ids)
    prior_trials = _trial_snapshot(root, prior_ids)
    if not prior_ids or not all(prior_trials.get(candidate_id) for candidate_id in prior_ids):
        raise SeedCommittedGapAcquisitionError("source skill graph lacks durable Candidate trials")

    schedule = _precommit_gap_schedule(
        root,
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
        raise SeedCommittedGapAcquisitionError(
            "precommitted bounded grammar schedule exposed fewer than two fail-closed gaps"
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
    acquisition_commitment = _digest(
        {
            "schedule_commitment": schedule["schedule_commitment"],
            "acquisition_fingerprint": acquisition_target["behavior_fingerprint"],
            "control_fingerprint": control_target["behavior_fingerprint"],
            "phase": "seed-grammar-gap-choice-v1",
        }
    )
    challenge_inputs = _challenge_inputs("numeric", acquisition_commitment)
    hidden_program = acquisition_target["program"]
    challenge_expected = tuple(
        _program_expected(hidden_program, value) for value in challenge_inputs
    )
    probe = challenge_inputs[0]
    expected = challenge_expected[0]
    challenge = challenge_inputs[1]
    challenge_value = challenge_expected[1]
    spec = {
        "name": f"seed-grammar-{str(acquisition_target['behavior_fingerprint'])[:12]}",
        "input_domain": "numeric",
        "output_domain": "numeric",
        "examples": acquisition_examples,
        "probe": probe,
        "expected": expected,
        "challenge": challenge,
        "challenge_expected": challenge_value,
        "max_nodes": int(acquisition_target["program_nodes"]),
    }

    with tempfile.TemporaryDirectory(prefix="agi-seed-grammar-gap-learner-") as temporary:
        learner_root = Path(temporary).resolve()
        _copy_persistent_state(root, learner_root)
        if _tree_snapshot(learner_root, prior_ids) != prior_tree:
            raise SeedCommittedGapAcquisitionError("learner restart changed prior Candidate bytes")
        if _trial_snapshot(learner_root, prior_ids) != prior_trials:
            raise SeedCommittedGapAcquisitionError("learner restart changed prior Candidate trials")
        if _library_attempt(learner_root, acquisition_examples)["supported"]:
            raise SeedCommittedGapAcquisitionError("acquisition target became supported before learning")

        acquired = _promote_task(
            learner_root,
            seed=f"{seed}:grammar-acquisition",
            spec=spec,
        )
        candidate_id = str(acquired["candidate_id"])
        scope = str(acquired["scope"])
        if candidate_id in prior_ids:
            raise SeedCommittedGapAcquisitionError("generated acquisition reused prior Candidate identity")
        if not acquired.get("negative_evidence_retained"):
            raise SeedCommittedGapAcquisitionError("generated acquisition lost protected negative evidence")
        learned_program = acquired["program"]
        for value, hidden_expected in zip(challenge_inputs, challenge_expected, strict=True):
            if execute_program(learned_program, value) != hidden_expected:
                raise SeedCommittedGapAcquisitionError(
                    "synthesized generated-gap program failed hidden held-back semantics"
                )
        if _tree_snapshot(learner_root, prior_ids) != prior_tree:
            raise SeedCommittedGapAcquisitionError("learning changed prior Candidate bytes")
        if _trial_snapshot(learner_root, prior_ids) != prior_trials:
            raise SeedCommittedGapAcquisitionError("learning changed prior Candidate trials")
        commit = _commit_verified_candidate(
            learner_root,
            root,
            candidate_id=candidate_id,
            scope=scope,
        )

    if _tree_snapshot(root, prior_ids) != prior_tree:
        raise SeedCommittedGapAcquisitionError("transactional commit changed prior Candidate bytes")
    if _trial_snapshot(root, prior_ids) != prior_trials:
        raise SeedCommittedGapAcquisitionError("transactional commit changed prior Candidate trials")
    enlarged_ids = [*prior_ids, candidate_id]
    enlarged_tree = _tree_snapshot(root, enlarged_ids)
    enlarged_trials = _trial_snapshot(root, enlarged_ids)
    if not enlarged_trials.get(candidate_id):
        raise SeedCommittedGapAcquisitionError("committed generated Candidate lacks trial evidence")

    with tempfile.TemporaryDirectory(prefix="agi-seed-grammar-gap-resolver-") as temporary:
        resolver_root = Path(temporary).resolve()
        _copy_persistent_state(root, resolver_root)
        prior_runs_copied = (resolver_root / ".continual" / "runs").exists()
        prior_episodes_copied = (resolver_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (resolver_root / ".continual" / "evidence").exists()
        if _tree_snapshot(resolver_root, enlarged_ids) != enlarged_tree:
            raise SeedCommittedGapAcquisitionError("resolver restart changed Candidate bytes")
        if _trial_snapshot(resolver_root, enlarged_ids) != enlarged_trials:
            raise SeedCommittedGapAcquisitionError("resolver restart changed Candidate trials")

        solved = synthesize_shallowest_verified_route(
            resolver_root,
            input_domain="numeric",
            output_domain="numeric",
            examples=acquisition_examples,
            max_depth=4,
            max_candidates=64,
            max_search_nodes=512,
            max_behavior_evaluations=256,
        )
        selected_ids = [str(value) for value in solved["selected_candidate_ids"]]
        if solved.get("candidate_ids_supplied_by_caller") is not False:
            raise SeedCommittedGapAcquisitionError("post-learning resolver used caller Candidate IDs")
        if candidate_id not in selected_ids:
            raise SeedCommittedGapAcquisitionError("post-learning resolver omitted the new Candidate")
        selected_items = tuple(_load_verified_macro_items(resolver_root, selected_ids))
        challenge_outputs: list[dict[str, Any]] = []
        fresh_run_ids: list[str] = []
        for value, hidden_expected in zip(challenge_inputs, challenge_expected, strict=True):
            compiled_output = execute_program(solved["compiled_program"], value)
            runtime_output, run_id, refs = _execute_chain(resolver_root, selected_items, value)
            if compiled_output != hidden_expected or runtime_output != hidden_expected:
                raise SeedCommittedGapAcquisitionError("fresh replay failed generated held-back semantics")
            if len(refs) != int(solved["selected_chain_depth"]):
                raise SeedCommittedGapAcquisitionError("fresh replay executed wrong learned-stage count")
            fresh_run_ids.append(run_id)
            challenge_outputs.append(
                {"input": value, "expected": hidden_expected, "output": runtime_output}
            )
        if len(set(fresh_run_ids)) != len(fresh_run_ids):
            raise SeedCommittedGapAcquisitionError("generated held-back replay reused an Engine run")

        protected_examples = tuple(
            ProgramExample(item["input"], item["output"])
            for item in protected_target["support_examples"]
        )
        protected_solved = synthesize_shallowest_verified_route(
            resolver_root,
            input_domain=str(protected_target["input_domain"]),
            output_domain=str(protected_target["output_domain"]),
            examples=protected_examples,
            max_depth=4,
            max_candidates=64,
            max_search_nodes=512,
            max_behavior_evaluations=256,
        )
        if protected_solved.get("candidate_ids_supplied_by_caller") is not False:
            raise SeedCommittedGapAcquisitionError("protected replay used caller Candidate IDs")
        protected_hidden_items = tuple(
            _load_verified_macro_items(
                resolver_root,
                [str(value) for value in protected_target["generator_hidden_candidate_ids"]],
            )
        )
        protected_challenges = _challenge_inputs(
            str(protected_target["input_domain"]),
            str(protected_plan["target_plan_commitment"]),
        )
        for value in protected_challenges:
            expected_value = value
            for item in protected_hidden_items:
                expected_value = execute_program(item["program"], expected_value)
            if execute_program(protected_solved["compiled_program"], value) != expected_value:
                raise SeedCommittedGapAcquisitionError("new acquisition regressed protected generated behavior")

        control_attempt = _library_attempt(resolver_root, control_examples)
        if control_attempt["supported"]:
            raise SeedCommittedGapAcquisitionError(
                "one generated acquisition unexpectedly solved the untouched control gap"
            )
        if _tree_snapshot(resolver_root, enlarged_ids) != enlarged_tree:
            raise SeedCommittedGapAcquisitionError("fresh validation changed Candidate bytes")
        if _trial_snapshot(resolver_root, enlarged_ids) != enlarged_trials:
            raise SeedCommittedGapAcquisitionError("fresh validation changed Candidate trials")

    if _tree_snapshot(root, enlarged_ids) != enlarged_tree:
        raise SeedCommittedGapAcquisitionError("fresh validation mutated source Candidate bytes")
    if _trial_snapshot(root, enlarged_ids) != enlarged_trials:
        raise SeedCommittedGapAcquisitionError("fresh validation mutated source Candidate trials")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "seed-committed-generic-grammar-gap-acquisition-v1",
        "seed": seed,
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
        "protected_generated_behavior_retained": True,
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
            "Internal bounded generated-capability acquisition evidence only. The exact target schedule "
            "is seed-committed from a generic pure numeric expression grammar, the current durable "
            "library must fail closed before learning, and the new Candidate is exact-scope regression-"
            "gated and transactionally retained before fresh replay. Generator, grammar, synthesis, "
            "regression, runtime, scoring, and evaluator remain repository-authored and do not establish "
            "independent production evidence or AGI."
        ),
    }
    if not all(
        (
            report["target_schedule_precommitted_before_support_checks"],
            report["generator_named_fixed_goal_family_used"] is False,
            report["acquisition_target_failed_closed_before_learning"],
            report["control_target_failed_closed_before_learning"],
            report["new_candidate_rediscovered"],
            report["fresh_engine_runs_unique"],
            report["protected_generated_behavior_retained"],
            report["control_gap_remained_failed_closed"],
            report["prior_candidate_state_unchanged"],
            report["prior_trial_state_unchanged"],
            not report["prior_runs_copied"],
            not report["prior_episodes_copied"],
            not report["prior_evidence_copied"],
        )
    ):
        raise SeedCommittedGapAcquisitionError("generated gap acquisition aggregate invariant failed")

    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "seed-committed-gap-acquisition"
        / f"gap-acquisition-{_digest(seed)[:16]}.json",
        report,
    )
    return report
