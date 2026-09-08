from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from agi.acquired_program_runtime import _atomic_json, _digest
from agi.acquired_programs import ProgramExample, execute_program, synthesize_program
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
)
from agi.structured_external_acquired_composition import (
    run_structured_external_acquired_composition,
)
from continual.acquired_program_tools import (
    AcquiredProgramToolError,
    acquired_program_candidate_payload,
)
from continual.candidate_regression import record_candidate_regression
from continual.learned_tools import LearnedToolError


STRUCTURED_CEGIS_SCOPE = "agi/acquired-program/structured-counterexample-refinement-v1"


def _measurement(
    task_id: str,
    repeat: int,
    score: float,
    artifact: Any,
    *,
    domain: str,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "criterion": "self_improvement",
        "domain": domain,
        "repeat_index": repeat,
        "passed": score == 1.0,
        "score": score,
        "artifact_sha256": _digest(artifact),
    }


def _target_descriptor() -> dict[str, Any]:
    return {
        "input_domain": "object",
        "output_domain": "boolean",
        "expression": {
            "op": "ge_numeric",
            "left": {
                "op": "object_get",
                "arg": {"op": "input"},
                "key": "score",
                "domain": "numeric",
            },
            "right": {"op": "const", "domain": "numeric", "value": 1},
        },
        "effects": [],
        "max_steps": 16,
        "max_output_length": 512,
    }


def _prior_structured_retest(root: Path, report: Mapping[str, Any]) -> dict[str, Any]:
    candidate_ids = report.get("candidate_ids")
    tool_ids = report.get("tool_ids")
    scopes = report.get("scopes")
    if not (
        isinstance(candidate_ids, list)
        and len(candidate_ids) >= 2
        and isinstance(tool_ids, list)
        and len(tool_ids) >= 2
        and isinstance(scopes, list)
        and len(scopes) >= 2
    ):
        raise RuntimeError("protected structured composition report is malformed")
    candidate_id = str(candidate_ids[1])
    tool_id = str(tool_ids[1])
    scope = str(scopes[1])
    probes = (
        ({"score": -2, "protected": "negative"}, False),
        ({"score": 3, "protected": "positive"}, True),
    )
    engine = _mechanical_engine(root)
    run_id = _new_runtime_run(engine)
    results = []
    for query, expected in probes:
        response = _invoke(
            engine,
            run_id,
            scope=scope,
            tool_id=tool_id,
            value=query,
        )
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise RuntimeError("protected structured capability returned malformed output")
        answer = result.get("output")
        results.append(
            {
                "query_sha256": _digest(query),
                "answer_sha256": _digest(answer),
                "success": answer is expected,
            }
        )
    return {
        "candidate_id": candidate_id,
        "tool_id": tool_id,
        "scope": scope,
        "score": sum(1.0 if item["success"] else 0.0 for item in results) / len(results),
        "results": results,
    }


def run_structured_acquired_program_cegis(root: Path, seed: str) -> dict[str, Any]:
    """Refine a spurious object predicate using only committed evaluator counterexamples.

    Initial demonstrations deliberately make key presence the minimal consistent hypothesis while the
    committed target is a numeric predicate over an object field. The learner receives at most the
    first failing diagnostic example per round. Final held-out answers are committed before learning
    but are never added to synthesis support. Promotion requires repeated target gain and retention of
    an already verified structured capability. Post-promotion replay uses a fresh mechanical Engine and
    must leave the Candidate regression trial ledger byte-identical.

    This is internal evaluator-separation evidence, not independent external evaluation or AGI proof.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    prior = run_structured_external_acquired_composition(
        root,
        f"structured-cegis-protected-prior::{seed}",
    )
    if not prior.get("passed"):
        raise RuntimeError("protected structured prerequisite did not pass")
    prior_before = _prior_structured_retest(root, prior)
    if prior_before["score"] != 1.0:
        raise RuntimeError("protected structured capability failed before CEGIS learning")

    target = _target_descriptor()
    initial_inputs = (
        {"score": 2, "approved": 0, "label": "a"},
        {"score": 5, "approved": False, "label": "b"},
        {"score": -3, "label": "c"},
        {"score": 0, "label": "d"},
    )
    diagnostic_inputs = (
        {"score": 4, "label": "diagnostic-missing-approved"},
        {"score": -1, "approved": True, "label": "diagnostic-spurious-approved"},
    )
    final_inputs = (
        {"score": -7, "approved": True, "nested": {"x": 1}},
        {"score": 1, "nested": {"x": 2}},
        {"score": 8, "approved": False, "nested": {"x": 3}},
        {"score": 0, "approved": True, "nested": {"x": 4}},
    )
    initial_examples = [
        ProgramExample(value, execute_program(target, value)) for value in initial_inputs
    ]
    diagnostic_expected = tuple(execute_program(target, value) for value in diagnostic_inputs)
    final_expected = tuple(execute_program(target, value) for value in final_inputs)
    commitment_payload = {
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
    evaluator_commitment = _digest(commitment_payload)
    token = _digest({"seed": seed, "commitment": evaluator_commitment})[:16]
    candidate_id = f"structured-cegis-{token}"
    tool_id = f"structured-cegis-tool-{token}"

    first = synthesize_program(
        input_domain="object",
        output_domain="boolean",
        examples=initial_examples,
        max_nodes=6,
    )
    expected_wrong = {
        "op": "object_has_key",
        "arg": {"op": "input"},
        "key": "approved",
    }
    if first.expression != expected_wrong:
        raise RuntimeError(
            "initial structured demonstrations did not produce the committed spurious hypothesis"
        )

    hypothesis_history: list[dict[str, Any]] = []
    counterexamples: list[ProgramExample] = []
    current = first
    for round_index in range(1, 5):
        failure: tuple[int, Any, Any, Any] | None = None
        for diagnostic_index, (query, expected) in enumerate(
            zip(diagnostic_inputs, diagnostic_expected, strict=True)
        ):
            answer = current.apply(query)
            if answer is not expected:
                failure = (diagnostic_index, query, expected, answer)
                break
        history = {
            "round": round_index,
            "program_sha256": _digest(current.descriptor()),
            "support_sha256": current.support_sha256,
            "diagnostic_passed": failure is None,
            "counterexample_index": failure[0] if failure is not None else None,
        }
        hypothesis_history.append(history)
        if failure is None:
            break
        _, query, expected, answer = failure
        history["failed_answer_sha256"] = _digest(answer)
        counterexample = ProgramExample(query, expected)
        counterexamples.append(counterexample)
        current = synthesize_program(
            input_domain="object",
            output_domain="boolean",
            examples=[*initial_examples, *counterexamples],
            max_nodes=6,
        )
    else:
        raise RuntimeError("structured CEGIS exceeded its refinement round budget")

    if not counterexamples:
        raise RuntimeError("structured CEGIS did not expose a failed hypothesis")
    if current.expression != target["expression"]:
        raise RuntimeError("structured counterexample did not refine to the committed target")
    if not all(
        current.apply(query) is expected
        for query, expected in zip(diagnostic_inputs, diagnostic_expected, strict=True)
    ):
        raise RuntimeError("refined structured program still fails the diagnostic pool")

    candidate_dir = root / ".continual" / "candidates" / candidate_id
    candidate_path = candidate_dir / "candidate.json"
    candidate = acquired_program_candidate_payload(
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=STRUCTURED_CEGIS_SCOPE,
        program=current.descriptor(),
        description="Object predicate refined from a spurious key-presence hypothesis using committed evaluator counterexamples.",
    )
    candidate["supporting_evidence"] = [
        {
            "type": "evaluator_commitment",
            "commitment_sha256": evaluator_commitment,
            "diagnostic_feedback_policy": "first_counterexample_only",
            "final_answers_exposed_to_learner": False,
        }
    ]
    candidate["contradictory_evidence"] = [
        {
            "type": "structured_cegis_failed_hypothesis",
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
        candidate_dir / "learning-history" / "structured-cegis.json",
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

    baseline: list[dict[str, Any]] = []
    trial: list[dict[str, Any]] = []
    baseline_engine = _mechanical_engine(root)
    baseline_run = _new_runtime_run(baseline_engine)
    target_task = f"withheld-structured-cegis-{token}"
    for repeat, (query, expected) in enumerate(zip(final_inputs, final_expected, strict=True)):
        pre_solved = False
        try:
            _invoke(
                baseline_engine,
                baseline_run,
                scope=STRUCTURED_CEGIS_SCOPE,
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
                domain="acquired-program:structured-cegis-final",
            )
        )
        answer = current.apply(query)
        trial.append(
            _measurement(
                target_task,
                repeat,
                1.0 if answer is expected else 0.0,
                {**artifact, "phase": "refined-candidate", "answer_sha256": _digest(answer)},
                domain="acquired-program:structured-cegis-final",
            )
        )

    protected_artifact = {
        "candidate_id": prior_before["candidate_id"],
        "scope": prior_before["scope"],
        "result_digests": [_digest(item) for item in prior_before["results"]],
    }
    baseline.append(
        _measurement(
            "protected-prior-structured-capability",
            0,
            1.0,
            protected_artifact,
            domain="protected-structured-program",
        )
    )
    prior_during = _prior_structured_retest(root, prior)
    trial.append(
        _measurement(
            "protected-prior-structured-capability",
            0,
            prior_during["score"],
            protected_artifact,
            domain="protected-structured-program",
        )
    )

    baseline_path = candidate_dir / "regression-input" / "baseline.json"
    trial_path = candidate_dir / "regression-input" / "candidate.json"
    _atomic_json(baseline_path, baseline)
    _atomic_json(trial_path, trial)
    promotion = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=STRUCTURED_CEGIS_SCOPE,
        baseline_path=baseline_path,
        candidate_path=trial_path,
        target_task_ids=[target_task],
    )
    if not promotion["decision"]["adopt_candidate"]:
        raise RuntimeError("structured CEGIS Candidate failed exact-scope repeated regression")

    trial_before_runtime = _candidate_trial_snapshot(root, candidate_id)
    restarted = _mechanical_engine(root)
    runtime_run = _new_runtime_run(restarted)
    runtime_results = []
    for query, expected in zip(final_inputs, final_expected, strict=True):
        response = _invoke(
            restarted,
            runtime_run,
            scope=STRUCTURED_CEGIS_SCOPE,
            tool_id=tool_id,
            value=query,
        )
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise RuntimeError("structured CEGIS runtime returned malformed result")
        answer = result.get("output")
        runtime_results.append(
            {
                "query_sha256": _digest(query),
                "answer_sha256": _digest(answer),
                "success": answer is expected,
            }
        )
    trial_after_runtime = _candidate_trial_snapshot(root, candidate_id)
    trial_state_unchanged = trial_after_runtime == trial_before_runtime
    if not trial_state_unchanged:
        raise RuntimeError("structured CEGIS runtime replay rewrote regression trial evidence")

    prior_after = _prior_structured_retest(root, prior)
    state = json.loads(candidate_path.read_text(encoding="utf-8"))
    failed_history_retained = any(
        isinstance(item, Mapping)
        and item.get("type") == "structured_cegis_failed_hypothesis"
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
    target_repeats = promotion["decision"].get("target_repeats", {})
    report = {
        "schema_version": 1,
        "campaign_id": f"structured-cegis-{token}",
        "candidate_id": candidate_id,
        "tool_id": tool_id,
        "scope": STRUCTURED_CEGIS_SCOPE,
        "evaluator_commitment": evaluator_commitment,
        "commitment_verified": _digest(commitment_payload) == evaluator_commitment,
        "initial_hypothesis": first.expression,
        "initial_hypothesis_was_wrong": True,
        "refined_expression": current.expression,
        "counterexample_count": len(counterexamples),
        "refinement_rounds": len(hypothesis_history),
        "final_answers_exposed_to_learner": final_answers_leaked,
        "baseline_score": sum(
            item["score"] for item in baseline if item["task_id"] == target_task
        )
        / len(final_inputs),
        "candidate_score": sum(
            item["score"] for item in trial if item["task_id"] == target_task
        )
        / len(final_inputs),
        "target_repeats": target_repeats,
        "protected_prior_score_before": prior_before["score"],
        "protected_prior_score_during": prior_during["score"],
        "protected_prior_score_after": prior_after["score"],
        "promotion_adopted": promotion["decision"]["adopt_candidate"],
        "fresh_engine_runtime_score": sum(
            1.0 if item["success"] else 0.0 for item in runtime_results
        )
        / len(runtime_results),
        "candidate_trial_state_unchanged": trial_state_unchanged,
        "failed_hypothesis_evidence_retained": failed_history_retained,
        "claim_boundary": (
            "Internal evaluator-committed structured refinement evidence only. The evaluator and target "
            "are still repository-authored rather than independently operated production evidence, so "
            "this does not establish open-ended self-improvement or AGI."
        ),
    }
    report["passed"] = bool(
        report["commitment_verified"]
        and report["initial_hypothesis_was_wrong"]
        and report["counterexample_count"] >= 1
        and report["final_answers_exposed_to_learner"] is False
        and report["baseline_score"] == 0.0
        and report["candidate_score"] == 1.0
        and set(report["target_repeats"].values()) == {len(final_inputs)}
        and report["protected_prior_score_before"] == 1.0
        and report["protected_prior_score_during"] == 1.0
        and report["protected_prior_score_after"] == 1.0
        and report["promotion_adopted"]
        and report["fresh_engine_runtime_score"] == 1.0
        and report["candidate_trial_state_unchanged"]
        and report["failed_hypothesis_evidence_retained"]
    )
    if not report["passed"]:
        raise RuntimeError("structured CEGIS campaign failed a required evidence gate")
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "structured-acquired-program-cegis"
        / f"{report['campaign_id']}.json",
        report,
    )
    return report
