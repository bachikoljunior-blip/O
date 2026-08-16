from __future__ import annotations

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

from .acquired_program_runtime import _atomic_json, _digest, _new_runtime_run
from .acquired_programs import ProgramExample, execute_program, synthesize_program

RANDOMIZED_CEGIS_SCOPE_PREFIX = "agi/acquired-program-runtime/randomized-cegis-v1"

_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "name": "absolute-value",
        "target": {"op": "abs", "arg": {"op": "input"}},
        "initial": (1, 3, 8),
        "diagnostic": (-4, -9, 12),
        "final": (-17, -6, 5, 21),
    },
    {
        "name": "nonnegative-clip",
        "target": {
            "op": "if_nonnegative",
            "condition": {"op": "input"},
            "then": {"op": "input"},
            "else": {"op": "const", "domain": "numeric", "value": 0},
        },
        "initial": (2, 5, 11),
        "diagnostic": (-3, -8, 13),
        "final": (-20, -1, 0, 7, 19),
    },
    {
        "name": "numeric-sign",
        "target": {
            "op": "if_nonnegative",
            "condition": {"op": "input"},
            "then": {"op": "const", "domain": "numeric", "value": 1},
            "else": {"op": "const", "domain": "numeric", "value": -1},
        },
        "initial": (1, 4, 9),
        "diagnostic": (-2, -7, 15),
        "final": (-31, -1, 0, 6, 40),
    },
)


def _target_descriptor(expression: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "input_domain": "numeric",
        "output_domain": "numeric",
        "expression": dict(expression),
        "effects": [],
        "max_steps": 16,
        "max_output_length": 128,
    }


def _measurement(task_id: str, repeat: int, score: float, artifact: Any) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "criterion": "self_improvement",
        "domain": "acquired-program:randomized-cegis",
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
                "goal": "apply randomized counterexample-refined acquired program",
                "scope": scope,
                "learned_tool_call": {"tool_id": tool_id, "input": value},
            },
        },
    )
    return output["result"]["output"]


def _variant_for_seed(seed: str) -> tuple[int, dict[str, Any]]:
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be a non-empty string")
    index = int(_digest(seed), 16) % len(_VARIANTS)
    return index, _VARIANTS[index]


def run_randomized_cegis_campaign(
    root: Path,
    seed: str,
    *,
    engine_factory: Callable[[Path], LearningEnabledEngine] = LearningEnabledEngine,
) -> dict[str, Any]:
    """Refine one evaluator-selected ambiguous task without revealing its final holdout.

    The seed selects among several committed typed target structures. The learner sees only ambiguous
    demonstrations and, after a failure, the first diagnostic counterexample. Failed hypotheses and
    counterexamples are retained; final held-out answers are never persisted into learning history.
    Activation still requires exact-scope deterministic regression before a fresh engine may execute
    the acquired program mechanically.
    """

    root = root.resolve()
    variant_index, variant = _variant_for_seed(seed)
    target = _target_descriptor(variant["target"])
    initial_inputs = tuple(variant["initial"])
    diagnostic_inputs = tuple(variant["diagnostic"])
    final_inputs = tuple(variant["final"])
    initial_examples = [
        ProgramExample(value, execute_program(target, value)) for value in initial_inputs
    ]
    diagnostic_expected = tuple(execute_program(target, value) for value in diagnostic_inputs)
    final_expected = tuple(execute_program(target, value) for value in final_inputs)
    commitment_payload = {
        "seed_sha256": _digest(seed),
        "variant_index": variant_index,
        "variant": variant["name"],
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
    scope = f"{RANDOMIZED_CEGIS_SCOPE_PREFIX}/{token}"
    candidate_id = f"randomized-cegis-{token}"
    tool_id = f"randomized-cegis-tool-{token}"

    current = synthesize_program(
        input_domain="numeric",
        output_domain="numeric",
        examples=tuple(initial_examples),
        max_nodes=7,
    )
    initial_digest = _digest(current.descriptor())
    history: list[dict[str, Any]] = []
    counterexamples: list[ProgramExample] = []
    for round_index in range(1, 5):
        failure = None
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
        counterexamples.append(ProgramExample(query, expected))
        current = synthesize_program(
            input_domain="numeric",
            output_domain="numeric",
            examples=tuple([*initial_examples, *counterexamples]),
            max_nodes=7,
        )
    else:
        raise RuntimeError("randomized counterexample refinement exceeded round budget")

    if not counterexamples:
        raise RuntimeError("selected ambiguity did not falsify the initial hypothesis")
    if current.expression != target["expression"]:
        raise RuntimeError("randomized counterexamples did not identify the committed target")

    candidate_dir = root / ".continual" / "candidates" / candidate_id
    candidate_path = candidate_dir / "candidate.json"
    candidate = acquired_program_candidate_payload(
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=scope,
        program=current.descriptor(),
        description="Pure bounded program refined from an evaluator-selected ambiguous task.",
    )
    candidate["supporting_evidence"] = [
        {
            "type": "randomized_evaluator_commitment",
            "commitment_sha256": commitment,
            "seed_sha256": _digest(seed),
            "variant_index": variant_index,
            "final_answers_exposed_to_learner": False,
        }
    ]
    candidate["contradictory_evidence"] = [
        {
            "type": "cegis_failed_hypothesis",
            "round": item["round"],
            "program_sha256": item["program_sha256"],
            "counterexample_index": item["counterexample_index"],
            "failed_answer_sha256": item.get("failed_answer_sha256"),
        }
        for item in history
        if not item["diagnostic_passed"]
    ]
    _atomic_json(candidate_path, candidate)
    _atomic_json(
        candidate_dir / "learning-history" / "randomized-cegis.json",
        {
            "schema_version": 1,
            "commitment_sha256": commitment,
            "seed_sha256": _digest(seed),
            "variant_index": variant_index,
            "initial_demonstrations": [
                {"input": item.input, "output": item.output} for item in initial_examples
            ],
            "hypotheses": history,
            "counterexamples": [
                {"input": item.input, "output": item.output} for item in counterexamples
            ],
            "final_heldout_answers_persisted": False,
        },
    )

    baseline_engine = engine_factory(root)
    baseline_run_id = _new_runtime_run(baseline_engine)
    baseline = []
    trial = []
    target_task = f"withheld-randomized-cegis-{variant_index}"
    for repeat, (query, expected) in enumerate(zip(final_inputs, final_expected, strict=True)):
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
            )
        )
        answer = current.apply(query)
        trial.append(
            _measurement(
                target_task,
                repeat,
                1.0 if answer == expected else 0.0,
                {**artifact, "phase": "candidate", "answer_sha256": _digest(answer)},
            )
        )

    baseline_path = candidate_dir / "regression-input" / "baseline.json"
    candidate_measurements_path = candidate_dir / "regression-input" / "candidate.json"
    _atomic_json(baseline_path, baseline)
    _atomic_json(candidate_measurements_path, trial)
    promotion = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline_path,
        candidate_path=candidate_measurements_path,
        target_task_ids=[target_task],
    )
    if not promotion["decision"]["adopt_candidate"]:
        raise RuntimeError("randomized CEGIS Candidate failed deterministic regression")

    restarted = engine_factory(root)
    run_id = _new_runtime_run(restarted)
    runtime_results = []
    for query, expected in zip(final_inputs, final_expected, strict=True):
        answer = _runtime_call(restarted, run_id, scope=scope, tool_id=tool_id, value=query)
        runtime_results.append(
            {
                "query_sha256": _digest(query),
                "answer_sha256": _digest(answer),
                "success": answer == expected,
            }
        )

    history_path = candidate_dir / "learning-history" / "randomized-cegis.json"
    persisted_history = json.loads(history_path.read_text(encoding="utf-8"))
    report = {
        "schema_version": 1,
        "campaign_id": f"randomized-cegis-{token}",
        "candidate_id": candidate_id,
        "tool_id": tool_id,
        "scope": scope,
        "variant_index": variant_index,
        "variant": variant["name"],
        "commitment": commitment,
        "commitment_verified": _digest(commitment_payload) == commitment,
        "initial_hypothesis_sha256": initial_digest,
        "initial_hypothesis_was_wrong": True,
        "counterexample_count": len(counterexamples),
        "refinement_rounds": len(history),
        "failed_hypotheses": [item for item in history if not item["diagnostic_passed"]],
        "final_answers_exposed_to_learner": False,
        "baseline_score": sum(item["score"] for item in baseline[: len(final_inputs)]) / len(final_inputs),
        "candidate_score": sum(item["score"] for item in trial[: len(final_inputs)]) / len(final_inputs),
        "promotion": promotion["decision"],
        "post_restart_runtime_score": sum(1.0 if item["success"] else 0.0 for item in runtime_results) / len(runtime_results),
        "runtime_results": runtime_results,
        "failed_hypothesis_history_retained": bool(candidate["contradictory_evidence"]),
        "final_heldout_answers_persisted": bool(persisted_history["final_heldout_answers_persisted"]),
        "claim_boundary": "Internal evaluator-controlled capability-development evidence only; not independent production AGI evidence.",
    }
    report["passed"] = bool(
        report["commitment_verified"]
        and report["initial_hypothesis_was_wrong"]
        and report["counterexample_count"] >= 1
        and report["baseline_score"] == 0.0
        and report["candidate_score"] == 1.0
        and report["promotion"]["adopt_candidate"] is True
        and report["post_restart_runtime_score"] == 1.0
        and report["failed_hypothesis_history_retained"]
        and not report["final_heldout_answers_persisted"]
    )
    evidence_path = (
        root
        / ".continual"
        / "evidence"
        / "randomized-cegis"
        / f"{report['campaign_id']}.json"
    )
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(evidence_path, report)
    return report
