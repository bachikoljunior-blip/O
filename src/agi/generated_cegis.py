from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from continual.acquired_program_tools import (
    AcquiredProgramToolError,
    acquired_program_candidate_payload,
)
from continual.candidate_regression import record_candidate_regression
from continual.learned_tools import LearnedToolError
from continual.learning_engine import LearningEnabledEngine

from .acquired_program_runtime import (
    ACQUIRED_RUNTIME_SCOPE,
    _atomic_json,
    _digest,
    _hidden_descriptor,
    _inputs,
    _new_runtime_run,
    run_acquired_program_runtime_campaign,
)
from .acquired_programs import (
    ProgramExample,
    execute_program,
    synthesize_program,
    validate_program_descriptor,
)

GENERATED_CEGIS_SCOPE_PREFIX = "agi/acquired-program-runtime/generated-cegis-v1"
_GENERATOR_VERSION = "piecewise-seeded-v1"
_THRESHOLDS = (-4, -3, -2, -1, 1, 2, 3, 4)


def _seeded_int(seed: str, label: str, low: int, high: int) -> int:
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be a non-empty string")
    if high < low:
        raise ValueError("invalid seeded integer bounds")
    raw = hashlib.sha256(
        f"generated-cegis-v1\0{seed}\0{label}".encode("utf-8")
    ).digest()
    return low + int.from_bytes(raw[:8], "big") % (high - low + 1)


def _const(value: int) -> dict[str, Any]:
    return {"op": "const", "domain": "numeric", "value": value}


def _input() -> dict[str, Any]:
    return {"op": "input"}


def generated_target_descriptor(seed: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate one bounded numeric target from a combinatorial typed grammar.

    This is not a lookup table of tasks. A seed deterministically chooses a non-zero integer threshold
    and one of three branch schemas: input/constant, constant/input, or two distinct constants. Across
    threshold and constant choices this yields hundreds of exact target programs while reusing only
    already-admitted pure arithmetic and conditional kernels. The returned metadata is evaluator-side
    and must not be persisted into learner history verbatim.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be a non-empty string")
    threshold = _THRESHOLDS[_seeded_int(seed, "threshold", 0, len(_THRESHOLDS) - 1)]
    mode = _seeded_int(seed, "mode", 0, 2)
    constant_a = _seeded_int(seed, "const-a", -3, 3)
    constant_b = _seeded_int(seed, "const-b", -3, 3)
    if constant_b == constant_a:
        constant_b = -3 + ((constant_b + 3 + 1) % 7)

    if mode == 0:
        then_branch = _input()
        else_branch = _const(constant_a)
    elif mode == 1:
        then_branch = _const(constant_a)
        else_branch = _input()
    else:
        then_branch = _const(constant_a)
        else_branch = _const(constant_b)

    expression = {
        "op": "if_nonnegative",
        "condition": {
            "op": "add",
            "left": _input(),
            "right": _const(-threshold),
        },
        "then": then_branch,
        "else": else_branch,
    }
    descriptor = {
        "input_domain": "numeric",
        "output_domain": "numeric",
        "expression": expression,
        "effects": [],
        "max_steps": 24,
        "max_output_length": 128,
    }
    meta = validate_program_descriptor(descriptor)
    if meta["nodes"] > 7:
        raise RuntimeError("generated target exceeded the intended bounded grammar")
    return descriptor, {
        "generator_version": _GENERATOR_VERSION,
        "threshold": threshold,
        "mode": mode,
        "constant_a": constant_a,
        "constant_b": constant_b,
        "nodes": meta["nodes"],
    }


def _input_partitions(threshold: int) -> tuple[tuple[float | int, ...], tuple[float | int, ...], tuple[float | int, ...]]:
    initial = (threshold + 1, threshold + 3, threshold + 6)
    diagnostic = (
        threshold - 8,
        threshold - 3,
        threshold - 1,
        threshold - 0.5,
        threshold,
        threshold + 0.5,
        threshold + 2,
        threshold + 7,
    )
    final = (
        threshold - 11.25,
        threshold - 4.5,
        threshold - 0.25,
        threshold + 0.25,
        threshold + 4.5,
        threshold + 12.75,
    )
    if set(initial) & set(diagnostic) or set(initial) & set(final) or set(diagnostic) & set(final):
        raise RuntimeError("generated evaluator partitions overlap")
    return initial, diagnostic, final


def _measurement(task_id: str, repeat: int, score: float, artifact: Any, *, domain: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "criterion": "self_improvement",
        "domain": domain,
        "repeat_index": repeat,
        "passed": score == 1.0,
        "score": score,
        "artifact_sha256": _digest(artifact),
    }


def _runtime_call(
    engine: LearningEnabledEngine,
    run_id: str,
    *,
    scope: str,
    tool_id: str,
    value: Any,
) -> Any:
    output = engine._invoke(
        run_id,
        "execute",
        {
            "snapshot": {"revision": 0},
            "execution_unit": {
                "goal": "apply a regression-verified evaluator-generated acquired program",
                "scope": scope,
                "learned_tool_call": {"tool_id": tool_id, "input": value},
            },
        },
    )
    return output["result"]["output"]


def _prior_retest(
    root: Path,
    prior_seed: str,
    prior_report: Mapping[str, Any],
    *,
    engine_factory: Callable[[Path], LearningEnabledEngine],
) -> dict[str, Any]:
    hidden = _hidden_descriptor(prior_seed)
    _, heldout_inputs = _inputs(str(hidden["input_domain"]))
    expected = tuple(execute_program(hidden, value) for value in heldout_inputs)
    engine = engine_factory(root)
    run_id = _new_runtime_run(engine)
    results = []
    for query, answer_expected in zip(heldout_inputs, expected, strict=True):
        answer = _runtime_call(
            engine,
            run_id,
            scope=ACQUIRED_RUNTIME_SCOPE,
            tool_id=str(prior_report["tool_id"]),
            value=query,
        )
        results.append(
            {
                "query_sha256": _digest(query),
                "answer_sha256": _digest(answer),
                "success": answer == answer_expected,
            }
        )
    return {
        "candidate_id": prior_report["candidate_id"],
        "tool_id": prior_report["tool_id"],
        "scope": ACQUIRED_RUNTIME_SCOPE,
        "score": sum(1.0 if item["success"] else 0.0 for item in results) / len(results),
        "results": results,
    }


def run_generated_cegis_campaign(
    root: Path,
    seed: str,
    *,
    engine_factory: Callable[[Path], LearningEnabledEngine] = LearningEnabledEngine,
) -> dict[str, Any]:
    """Learn one evaluator-generated target by bounded counterexample-guided refinement.

    The evaluator commits to a seed-generated target, diagnostic probes, and final holdout before
    synthesis. The learner receives only three deliberately one-sided demonstrations and then the
    first diagnostic counterexample from each failed hypothesis. Final answers and the generator seed
    never enter learner history. Promotion requires deterministic exact-scope regression, a protected
    previously acquired capability, and successful execution in a fresh Engine instance.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be a non-empty string")
    root = root.resolve()

    prior_seed = "generated-cegis-protected-prior-v1"
    prior = run_acquired_program_runtime_campaign(
        root,
        prior_seed,
        engine_factory=engine_factory,
    )
    if not prior["passed"]:
        raise RuntimeError("protected prerequisite acquired-program campaign did not pass")

    target, target_meta = generated_target_descriptor(seed)
    initial_inputs, diagnostic_inputs, final_inputs = _input_partitions(int(target_meta["threshold"]))
    initial_examples = tuple(
        ProgramExample(value, execute_program(target, value)) for value in initial_inputs
    )
    diagnostic_expected = tuple(execute_program(target, value) for value in diagnostic_inputs)
    final_expected = tuple(execute_program(target, value) for value in final_inputs)
    commitment_payload = {
        "generator_version": _GENERATOR_VERSION,
        "seed_sha256": _digest(seed),
        "target": target,
        "diagnostic": [
            {"input": query, "expected": expected}
            for query, expected in zip(diagnostic_inputs, diagnostic_expected, strict=True)
        ],
        "final": [
            {"input": query, "expected": expected}
            for query, expected in zip(final_inputs, final_expected, strict=True)
        ],
    }
    commitment = _digest(commitment_payload)
    token = commitment[:16]
    scope = f"{GENERATED_CEGIS_SCOPE_PREFIX}/{token}"
    candidate_id = f"generated-cegis-{token}"
    tool_id = f"generated-cegis-tool-{token}"

    current = synthesize_program(
        input_domain="numeric",
        output_domain="numeric",
        examples=initial_examples,
        max_nodes=7,
        allow_conditionals=True,
    )
    initial_hypothesis_sha256 = _digest(current.descriptor())
    history: list[dict[str, Any]] = []
    counterexamples: list[ProgramExample] = []
    for round_index in range(1, 10):
        failure: tuple[int, Any, Any, Any] | None = None
        for diagnostic_index, (query, expected) in enumerate(
            zip(diagnostic_inputs, diagnostic_expected, strict=True)
        ):
            answer = current.apply(query)
            if answer != expected:
                failure = (diagnostic_index, query, expected, answer)
                break
        history.append(
            {
                "round": round_index,
                "program_sha256": _digest(current.descriptor()),
                "support_sha256": current.support_sha256,
                "diagnostic_passed": failure is None,
                "counterexample_index": failure[0] if failure is not None else None,
            }
        )
        if failure is None:
            break
        _, query, expected, answer = failure
        history[-1]["failed_answer_sha256"] = _digest(answer)
        history[-1]["counterexample_input_sha256"] = _digest(query)
        counterexample = ProgramExample(query, expected)
        if any(existing.input == counterexample.input for existing in counterexamples):
            raise RuntimeError("counterexample-guided refinement repeated an already learned probe")
        counterexamples.append(counterexample)
        current = synthesize_program(
            input_domain="numeric",
            output_domain="numeric",
            examples=tuple([*initial_examples, *counterexamples]),
            max_nodes=7,
            allow_conditionals=True,
        )
    else:
        raise RuntimeError("generated counterexample refinement exceeded round budget")

    if not counterexamples:
        raise RuntimeError("generated target did not falsify the initial one-sided hypothesis")
    final_answers = tuple(current.apply(query) for query in final_inputs)
    if final_answers != final_expected:
        failure_report = {
            "schema_version": 1,
            "commitment_sha256": commitment,
            "seed_sha256": _digest(seed),
            "target_program_sha256": _digest(target),
            "failed_hypothesis_count": len([item for item in history if not item["diagnostic_passed"]]),
            "counterexample_count": len(counterexamples),
            "sealed_final_holdout_passed": False,
            "final_answers_exposed_to_learner": False,
            "claim_boundary": "Internal failed generalization evidence only; not independent production AGI evidence.",
        }
        _atomic_json(
            root
            / ".continual"
            / "evidence"
            / "generated-cegis-failures"
            / f"{commitment}.json",
            failure_report,
        )
        raise RuntimeError("diagnostic-refined program failed the sealed final holdout")

    candidate_dir = root / ".continual" / "candidates" / candidate_id
    candidate_path = candidate_dir / "candidate.json"
    candidate = acquired_program_candidate_payload(
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=scope,
        program=current.descriptor(),
        description="Pure bounded program learned from an evaluator-generated committed typed target family.",
    )
    candidate["supporting_evidence"] = [
        {
            "type": "generated_evaluator_commitment",
            "generator_version": _GENERATOR_VERSION,
            "commitment_sha256": commitment,
            "seed_sha256": _digest(seed),
            "target_program_sha256": _digest(target),
            "final_answers_exposed_to_learner": False,
            "diagnostic_feedback_policy": "first_counterexample_only",
        }
    ]
    candidate["contradictory_evidence"] = [
        {
            "type": "cegis_failed_hypothesis",
            "round": item["round"],
            "program_sha256": item["program_sha256"],
            "counterexample_index": item.get("counterexample_index"),
            "counterexample_input_sha256": item.get("counterexample_input_sha256"),
            "failed_answer_sha256": item.get("failed_answer_sha256"),
        }
        for item in history
        if not item["diagnostic_passed"]
    ]
    _atomic_json(candidate_path, candidate)
    _atomic_json(
        candidate_dir / "learning-history" / "generated-cegis.json",
        {
            "schema_version": 1,
            "generator_version": _GENERATOR_VERSION,
            "commitment_sha256": commitment,
            "seed_sha256": _digest(seed),
            "target_program_sha256": _digest(target),
            "initial_demonstrations": [
                {"input": item.input, "output": item.output} for item in initial_examples
            ],
            "hypotheses": history,
            "counterexamples": [
                {"input": item.input, "output": item.output} for item in counterexamples
            ],
            "target_descriptor_persisted": False,
            "final_heldout_answers_persisted": False,
        },
    )

    baseline_engine = engine_factory(root)
    baseline_run_id = _new_runtime_run(baseline_engine)
    baseline: list[dict[str, Any]] = []
    candidate_measurements: list[dict[str, Any]] = []
    target_task = f"withheld-generated-cegis-{token}"
    for repeat, (query, expected, answer) in enumerate(
        zip(final_inputs, final_expected, final_answers, strict=True)
    ):
        try:
            _runtime_call(
                baseline_engine,
                baseline_run_id,
                scope=scope,
                tool_id=tool_id,
                value=query,
            )
            pre_solved = True
        except (LearnedToolError, AcquiredProgramToolError):
            pre_solved = False
        artifact = {
            "query_sha256": _digest(query),
            "expected_sha256": _digest(expected),
            "commitment_sha256": commitment,
        }
        baseline.append(
            _measurement(
                target_task,
                repeat,
                1.0 if pre_solved else 0.0,
                {**artifact, "phase": "baseline"},
                domain="acquired-program:generated-cegis",
            )
        )
        candidate_measurements.append(
            _measurement(
                target_task,
                repeat,
                1.0 if answer == expected else 0.0,
                {
                    **artifact,
                    "phase": "candidate",
                    "answer_sha256": _digest(answer),
                },
                domain="acquired-program:generated-cegis",
            )
        )

    prior_before = _prior_retest(
        root,
        prior_seed,
        prior,
        engine_factory=engine_factory,
    )
    protected = _measurement(
        "protected-prior-generated-cegis",
        0,
        prior_before["score"],
        {
            "candidate_id": prior_before["candidate_id"],
            "scope": prior_before["scope"],
            "result_digests": [_digest(item) for item in prior_before["results"]],
        },
        domain="protected-acquired-program",
    )
    baseline_with_protected = [*baseline, protected]
    adverse_protected = {
        **protected,
        "passed": False,
        "score": 0.0,
        "artifact_sha256": _digest({"forced_protected_regression": True, "commitment": commitment}),
    }
    adverse_trial = [*candidate_measurements, adverse_protected]
    valid_trial = [*candidate_measurements, protected]
    regression_dir = candidate_dir / "regression-input"
    baseline_path = regression_dir / "baseline.json"
    adverse_path = regression_dir / "candidate-adverse.json"
    valid_path = regression_dir / "candidate-valid.json"
    _atomic_json(baseline_path, baseline_with_protected)
    _atomic_json(adverse_path, adverse_trial)
    _atomic_json(valid_path, valid_trial)

    failed = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline_path,
        candidate_path=adverse_path,
        target_task_ids=[target_task],
    )
    if failed["decision"]["adopt_candidate"]:
        raise RuntimeError("generated Candidate ignored a forced protected regression")
    promoted = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline_path,
        candidate_path=valid_path,
        target_task_ids=[target_task],
    )
    if not promoted["decision"]["adopt_candidate"]:
        raise RuntimeError("generated Candidate failed valid deterministic regression")

    restarted = engine_factory(root)
    run_id = _new_runtime_run(restarted)
    runtime_results = []
    for query, expected in zip(final_inputs, final_expected, strict=True):
        answer = _runtime_call(
            restarted,
            run_id,
            scope=scope,
            tool_id=tool_id,
            value=query,
        )
        runtime_results.append(
            {
                "query_sha256": _digest(query),
                "answer_sha256": _digest(answer),
                "success": answer == expected,
            }
        )
    prior_after = _prior_retest(
        root,
        prior_seed,
        prior,
        engine_factory=engine_factory,
    )

    state = json.loads(candidate_path.read_text(encoding="utf-8"))
    history_path = candidate_dir / "learning-history" / "generated-cegis.json"
    persisted_history = json.loads(history_path.read_text(encoding="utf-8"))
    learner_pairs = {
        (_digest(item.input), _digest(item.output))
        for item in [*initial_examples, *counterexamples]
    }
    final_leaked = any(
        (_digest(query), _digest(expected)) in learner_pairs
        for query, expected in zip(final_inputs, final_expected, strict=True)
    )
    report = {
        "schema_version": 1,
        "campaign_id": f"generated-cegis-{token}",
        "candidate_id": candidate_id,
        "tool_id": tool_id,
        "scope": scope,
        "generator_version": _GENERATOR_VERSION,
        "seed_sha256": _digest(seed),
        "target_program_sha256": _digest(target),
        "target_node_count": target_meta["nodes"],
        "commitment": commitment,
        "commitment_verified": _digest(commitment_payload) == commitment,
        "initial_hypothesis_sha256": initial_hypothesis_sha256,
        "initial_hypothesis_was_wrong": bool(counterexamples),
        "counterexample_count": len(counterexamples),
        "refinement_rounds": len(history),
        "failed_hypotheses": [
            {
                "round": item["round"],
                "program_sha256": item["program_sha256"],
                "counterexample_index": item.get("counterexample_index"),
            }
            for item in history
            if not item["diagnostic_passed"]
        ],
        "seed_value_persisted": False,
        "target_descriptor_persisted_to_learner": bool(
            persisted_history["target_descriptor_persisted"]
        ),
        "final_answers_exposed_to_learner": final_leaked,
        "final_heldout_answers_persisted": bool(
            persisted_history["final_heldout_answers_persisted"]
        ),
        "baseline_score": sum(item["score"] for item in baseline) / len(baseline),
        "candidate_score": sum(item["score"] for item in candidate_measurements)
        / len(candidate_measurements),
        "forced_regression_rejected": failed["decision"]["adopt_candidate"] is False,
        "promotion": {
            "adopt_candidate": promoted["decision"]["adopt_candidate"],
            "decision_ref": promoted["decision_ref"],
            "trial": promoted.get("trial"),
        },
        "negative_evidence_retained": bool(state.get("contradictory_evidence")),
        "post_restart_runtime_score": sum(
            1.0 if item["success"] else 0.0 for item in runtime_results
        )
        / len(runtime_results),
        "runtime_results": runtime_results,
        "prior_capability_score_before_promotion": prior_before["score"],
        "prior_capability_score_after_promotion": prior_after["score"],
        "claim_boundary": (
            "This is internally evaluator-generated capability-development evidence. The target family, "
            "counterexamples, and regression evaluator are not independent production evidence, so it does "
            "not support a verified AGI claim."
        ),
    }
    report["passed"] = bool(
        report["commitment_verified"]
        and report["initial_hypothesis_was_wrong"]
        and report["counterexample_count"] >= 1
        and report["baseline_score"] == 0.0
        and report["candidate_score"] == 1.0
        and report["forced_regression_rejected"]
        and report["promotion"]["adopt_candidate"] is True
        and report["negative_evidence_retained"]
        and report["post_restart_runtime_score"] == 1.0
        and report["prior_capability_score_before_promotion"] == 1.0
        and report["prior_capability_score_after_promotion"] == 1.0
        and not report["target_descriptor_persisted_to_learner"]
        and not report["final_answers_exposed_to_learner"]
        and not report["final_heldout_answers_persisted"]
    )
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "generated-cegis"
        / f"{report['campaign_id']}.json",
        report,
    )
    return report
