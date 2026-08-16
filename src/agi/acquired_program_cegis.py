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

from .acquired_program_runtime import (
    _atomic_json,
    _digest,
    _hidden_descriptor,
    _inputs,
    _new_runtime_run,
    run_acquired_program_runtime_campaign,
)
from .acquired_programs import ProgramExample, execute_program, synthesize_program


CEGIS_SCOPE = "agi/acquired-program-runtime/counterexample-refinement-v1"


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
) -> dict[str, Any]:
    return engine._invoke(
        run_id,
        "execute",
        {
            "snapshot": {"revision": 0},
            "execution_unit": {
                "goal": "apply a regression-verified counterexample-refined acquired program",
                "scope": scope,
                "learned_tool_call": {"tool_id": tool_id, "input": value},
            },
        },
    )


def _prior_retest(
    root: Path,
    prior_seed: str,
    prior_report: Mapping[str, Any],
    *,
    engine_factory: Callable[[Path], LearningEnabledEngine],
) -> dict[str, Any]:
    hidden = _hidden_descriptor(prior_seed)
    _, heldout = _inputs(str(hidden["input_domain"]))
    expected = tuple(execute_program(hidden, value) for value in heldout)
    engine = engine_factory(root)
    run_id = _new_runtime_run(engine)
    results = []
    for query, answer_expected in zip(heldout, expected, strict=True):
        output = _runtime_call(
            engine,
            run_id,
            scope=str(prior_report["scope"]),
            tool_id=str(prior_report["tool_id"]),
            value=query,
        )
        answer = output["result"]["output"]
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
        "scope": prior_report["scope"],
        "score": sum(1.0 if item["success"] else 0.0 for item in results) / len(results),
        "results": results,
    }


def run_acquired_program_cegis_campaign(
    root: Path,
    *,
    engine_factory: Callable[[Path], LearningEnabledEngine] = LearningEnabledEngine,
) -> dict[str, Any]:
    """Refine a wrong minimal program using only evaluator-returned counterexamples.

    The evaluator commits to the target, diagnostic pool, and final held-out pool before learning.
    Initial positive demonstrations deliberately make identity the minimal consistent hypothesis even
    though the hidden target is absolute value. The diagnostic evaluator reveals only the first
    counterexample; the learner must then resynthesize. Final held-out answers are never revealed to
    the learner. A previously acquired hidden program is re-run and protected by Candidate regression
    so correction cannot silently trade away an earlier capability.
    """

    root = root.resolve()
    prior_seed = "cegis-protected-prior-v1"
    prior = run_acquired_program_runtime_campaign(
        root,
        prior_seed,
        engine_factory=engine_factory,
    )
    if not prior["passed"]:
        raise RuntimeError("protected prerequisite acquired-program campaign did not pass")

    target_descriptor = {
        "input_domain": "numeric",
        "output_domain": "numeric",
        "expression": {"op": "abs", "arg": {"op": "input"}},
        "effects": [],
        "max_steps": 16,
        "max_output_length": 128,
    }
    initial_inputs = (1, 3, 8)
    diagnostic_inputs = (-4, -9, 12)
    final_inputs = (-17, -6, 5, 21)
    initial_examples = [
        ProgramExample(value, execute_program(target_descriptor, value)) for value in initial_inputs
    ]
    diagnostic_expected = tuple(
        execute_program(target_descriptor, value) for value in diagnostic_inputs
    )
    final_expected = tuple(execute_program(target_descriptor, value) for value in final_inputs)
    commitment_payload = {
        "target": target_descriptor,
        "diagnostic": [
            {"input": value, "expected": expected}
            for value, expected in zip(diagnostic_inputs, diagnostic_expected, strict=True)
        ],
        "final": [
            {"input": value, "expected": expected}
            for value, expected in zip(final_inputs, final_expected, strict=True)
        ],
    }
    evaluator_commitment = _digest(commitment_payload)
    token = evaluator_commitment[:16]
    candidate_id = f"acquired-cegis-{token}"
    tool_id = f"acquired-cegis-tool-{token}"

    first = synthesize_program(
        input_domain="numeric",
        output_domain="numeric",
        examples=tuple(initial_examples),
        max_nodes=5,
    )
    if first.expression != {"op": "input"}:
        raise RuntimeError("initial ambiguous demonstrations did not produce the expected wrong hypothesis")

    hypothesis_history: list[dict[str, Any]] = []
    counterexamples: list[ProgramExample] = []
    current = first
    for round_index in range(1, 5):
        failure: tuple[int, Any, Any, Any] | None = None
        for diagnostic_index, (query, expected) in enumerate(
            zip(diagnostic_inputs, diagnostic_expected, strict=True)
        ):
            answer = current.apply(query)
            if answer != expected:
                failure = (diagnostic_index, query, expected, answer)
                break
        hypothesis_history.append(
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
        counterexample = ProgramExample(query, expected)
        counterexamples.append(counterexample)
        hypothesis_history[-1]["failed_answer_sha256"] = _digest(answer)
        hypothesis_history[-1]["counterexample_input"] = query
        hypothesis_history[-1]["counterexample_output"] = expected
        current = synthesize_program(
            input_domain="numeric",
            output_domain="numeric",
            examples=tuple([*initial_examples, *counterexamples]),
            max_nodes=5,
        )
    else:
        raise RuntimeError("counterexample-guided refinement exceeded round budget")

    if not counterexamples:
        raise RuntimeError("counterexample-guided campaign produced no failed hypothesis")
    if current.expression != target_descriptor["expression"]:
        raise RuntimeError("counterexample did not refine to the hidden target program")
    if not all(
        current.apply(query) == expected
        for query, expected in zip(diagnostic_inputs, diagnostic_expected, strict=True)
    ):
        raise RuntimeError("refined program does not satisfy diagnostic pool")

    candidate_dir = root / ".continual" / "candidates" / candidate_id
    candidate_path = candidate_dir / "candidate.json"
    candidate = acquired_program_candidate_payload(
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=CEGIS_SCOPE,
        program=current.descriptor(),
        description="Pure program refined from a failed hypothesis using evaluator-returned counterexamples.",
    )
    candidate["supporting_evidence"] = [
        {
            "type": "evaluator_commitment",
            "commitment_sha256": evaluator_commitment,
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
            "failed_answer_sha256": item.get("failed_answer_sha256"),
        }
        for item in hypothesis_history
        if not item["diagnostic_passed"]
    ]
    _atomic_json(candidate_path, candidate)
    _atomic_json(
        candidate_dir / "learning-history" / "cegis.json",
        {
            "schema_version": 1,
            "evaluator_commitment": evaluator_commitment,
            "initial_demonstrations": [
                {"input": item.input, "output": item.output} for item in initial_examples
            ],
            "hypotheses": hypothesis_history,
            "counterexamples": [
                {"input": item.input, "output": item.output} for item in counterexamples
            ],
            "final_heldout_answers_persisted": False,
        },
    )

    baseline_engine = engine_factory(root)
    baseline_run_id = _new_runtime_run(baseline_engine)
    baseline: list[dict[str, Any]] = []
    trial: list[dict[str, Any]] = []
    target_task = "withheld-cegis-final-transfer"
    for repeat, (query, expected) in enumerate(zip(final_inputs, final_expected, strict=True)):
        pre_solved = False
        try:
            _runtime_call(
                baseline_engine,
                baseline_run_id,
                scope=CEGIS_SCOPE,
                tool_id=tool_id,
                value=query,
            )
            pre_solved = True
        except (LearnedToolError, AcquiredProgramToolError):
            pre_solved = False
        artifact = {
            "query_sha256": _digest(query),
            "expected_sha256": _digest(expected),
            "commitment_sha256": evaluator_commitment,
        }
        baseline.append(
            _measurement(
                target_task,
                repeat,
                1.0 if pre_solved else 0.0,
                {**artifact, "phase": "pre-promotion-runtime"},
                domain="acquired-program:cegis-final",
            )
        )
        answer = current.apply(query)
        trial.append(
            _measurement(
                target_task,
                repeat,
                1.0 if answer == expected else 0.0,
                {**artifact, "phase": "refined-candidate", "answer_sha256": _digest(answer)},
                domain="acquired-program:cegis-final",
            )
        )

    prior_retained = _prior_retest(
        root,
        prior_seed,
        prior,
        engine_factory=engine_factory,
    )
    protected_artifact = {
        "candidate_id": prior_retained["candidate_id"],
        "scope": prior_retained["scope"],
        "result_digests": [_digest(item) for item in prior_retained["results"]],
    }
    baseline.append(
        _measurement(
            "protected-prior-acquired-capability",
            0,
            1.0,
            protected_artifact,
            domain="protected-acquired-program",
        )
    )
    trial.append(
        _measurement(
            "protected-prior-acquired-capability",
            0,
            prior_retained["score"],
            protected_artifact,
            domain="protected-acquired-program",
        )
    )
    baseline_path = candidate_dir / "regression-input" / "baseline.json"
    trial_path = candidate_dir / "regression-input" / "candidate.json"
    _atomic_json(baseline_path, baseline)
    _atomic_json(trial_path, trial)
    promotion = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=CEGIS_SCOPE,
        baseline_path=baseline_path,
        candidate_path=trial_path,
        target_task_ids=[target_task],
    )
    if not promotion["decision"]["adopt_candidate"]:
        raise RuntimeError("counterexample-refined Candidate failed deterministic regression")

    restarted = engine_factory(root)
    run_id = _new_runtime_run(restarted)
    runtime_results = []
    for query, expected in zip(final_inputs, final_expected, strict=True):
        output = _runtime_call(
            restarted,
            run_id,
            scope=CEGIS_SCOPE,
            tool_id=tool_id,
            value=query,
        )
        answer = output["result"]["output"]
        runtime_results.append(
            {
                "query_sha256": _digest(query),
                "answer_sha256": _digest(answer),
                "success": answer == expected,
            }
        )
    prior_after = _prior_retest(root, prior_seed, prior, engine_factory=engine_factory)
    state = json.loads(candidate_path.read_text(encoding="utf-8"))
    failed_history_retained = any(
        isinstance(item, Mapping) and item.get("type") == "cegis_failed_hypothesis"
        for item in state.get("contradictory_evidence", [])
    )
    learner_pairs = {
        (_digest(item.input), _digest(item.output))
        for item in [*initial_examples, *counterexamples]
    }
    final_answers_leaked = any(
        (_digest(query), _digest(expected)) in learner_pairs
        for query, expected in zip(final_inputs, final_expected, strict=True)
    )
    report = {
        "schema_version": 1,
        "campaign_id": f"acquired-cegis-{token}",
        "candidate_id": candidate_id,
        "tool_id": tool_id,
        "scope": CEGIS_SCOPE,
        "evaluator_commitment": evaluator_commitment,
        "commitment_verified": _digest(commitment_payload) == evaluator_commitment,
        "initial_hypothesis_sha256": _digest(first.descriptor()),
        "initial_hypothesis_was_wrong": True,
        "counterexample_count": len(counterexamples),
        "refinement_rounds": len(hypothesis_history),
        "failed_hypotheses": [
            {
                "round": item["round"],
                "program_sha256": item["program_sha256"],
                "counterexample_index": item.get("counterexample_index"),
            }
            for item in hypothesis_history
            if not item["diagnostic_passed"]
        ],
        "final_answers_exposed_to_learner": final_answers_leaked,
        "baseline_score": sum(
            item["score"] for item in baseline if item["task_id"] == target_task
        )
        / len(final_inputs),
        "candidate_score": sum(
            item["score"] for item in trial if item["task_id"] == target_task
        )
        / len(final_inputs),
        "promotion": {
            "adopt_candidate": promotion["decision"]["adopt_candidate"],
            "decision_ref": promotion["decision_ref"],
            "trial": promotion.get("trial"),
        },
        "post_restart_runtime_score": sum(
            1.0 if item["success"] else 0.0 for item in runtime_results
        )
        / len(runtime_results),
        "runtime_results": runtime_results,
        "prior_capability_score_before_promotion": prior_retained["score"],
        "prior_capability_score_after_promotion": prior_after["score"],
        "failed_hypothesis_history_retained": failed_history_retained,
        "evidence_tier": "development",
        "claim_boundary": (
            "This demonstrates correction from a genuinely failed internal hypothesis using bounded "
            "counterexample feedback while a final held-out set remains sealed. The evaluator and evidence "
            "are internal, so the result is not independent production AGI evidence."
        ),
    }
    report["passed"] = (
        report["commitment_verified"]
        and report["initial_hypothesis_was_wrong"]
        and report["counterexample_count"] >= 1
        and not report["final_answers_exposed_to_learner"]
        and report["baseline_score"] == 0.0
        and report["candidate_score"] == 1.0
        and report["promotion"]["adopt_candidate"] is True
        and report["post_restart_runtime_score"] == 1.0
        and report["prior_capability_score_before_promotion"] == 1.0
        and report["prior_capability_score_after_promotion"] == 1.0
        and report["failed_hypothesis_history_retained"]
    )
    report["digest"] = _digest(report)
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "acquired-program-cegis"
        / f"{report['campaign_id']}.json",
        report,
    )
    return report
