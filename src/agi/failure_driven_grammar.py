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
from .acquired_programs import (
    AcquiredProgramError,
    ProgramExample,
    execute_program,
    synthesize_program,
)


FAILURE_DRIVEN_FOLD_SCOPE = "agi/acquired-program-runtime/failure-driven-fold-v1"


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
                "goal": "apply a regression-verified failure-driven aggregation program",
                "scope": scope,
                "learned_tool_call": {"tool_id": tool_id, "input": value},
            },
        },
    )


def _retest_prior(
    root: Path,
    seed: str,
    report: Mapping[str, Any],
    *,
    engine_factory: Callable[[Path], LearningEnabledEngine],
) -> float:
    hidden = _hidden_descriptor(seed)
    _, heldout = _inputs(str(hidden["input_domain"]))
    expected = tuple(execute_program(hidden, value) for value in heldout)
    engine = engine_factory(root)
    run_id = _new_runtime_run(engine)
    successes = []
    for query, answer_expected in zip(heldout, expected, strict=True):
        output = _runtime_call(
            engine,
            run_id,
            scope=str(report["scope"]),
            tool_id=str(report["tool_id"]),
            value=query,
        )
        successes.append(output["result"]["output"] == answer_expected)
    return sum(1.0 if value else 0.0 for value in successes) / len(successes)


def run_failure_driven_fold_campaign(
    root: Path,
    *,
    engine_factory: Callable[[Path], LearningEnabledEngine] = LearningEnabledEngine,
) -> dict[str, Any]:
    """Admit a generic numeric sequence fold only after the earlier grammar fails a hidden task.

    The hidden task depends on sequence values, not length. Three demonstrations all have identical
    length but distinct sums, making the pre-fold grammar incapable of representing the target. That
    failure is required and persisted before synthesis is retried with the bounded fold kernel. The
    new program still receives no side effects or arbitrary code authority and remains inactive until
    exact-scope deterministic regression passes with a prior acquired capability protected.
    """

    root = root.resolve()
    prior_seed = "failure-driven-fold-prior-v1"
    prior = run_acquired_program_runtime_campaign(
        root,
        prior_seed,
        engine_factory=engine_factory,
    )
    if not prior["passed"]:
        raise RuntimeError("protected prior capability did not pass")

    demonstrations = (
        ProgramExample([1, 2], 3),
        ProgramExample([3, 4], 7),
        ProgramExample([-2, 7], 5),
    )
    heldout = (
        ([10, -1, 2], 11),
        ([5, 5, 5, 5], 20),
        ([-8, 3, 9, -1], 3),
        ([100, -40, -50, 7, 1], 18),
    )
    evaluator_commitment = _digest(
        {
            "task": "sum numeric sequence values",
            "demonstrations": [
                {"input": item.input, "output": item.output} for item in demonstrations
            ],
            "heldout": [
                {"input": query, "expected": expected} for query, expected in heldout
            ],
        }
    )

    legacy_failure: str | None = None
    try:
        synthesize_program(
            input_domain="sequence",
            output_domain="numeric",
            examples=demonstrations,
            max_nodes=5,
            allow_sequence_folds=False,
        )
    except AcquiredProgramError as exc:
        legacy_failure = str(exc)
    if legacy_failure is None:
        raise RuntimeError("pre-fold grammar unexpectedly solved the value-dependent hidden task")

    learned = synthesize_program(
        input_domain="sequence",
        output_domain="numeric",
        examples=demonstrations,
        max_nodes=5,
        allow_sequence_folds=True,
    )
    expected_expression = {
        "op": "fold_numeric",
        "arg": {"op": "input"},
        "reducer": "add",
        "initial": 0,
    }
    if learned.expression != expected_expression:
        raise RuntimeError("failure-driven synthesis did not select the generic additive fold")

    token = evaluator_commitment[:16]
    candidate_id = f"failure-driven-fold-{token}"
    tool_id = f"acquired-fold-{token}"
    candidate_dir = root / ".continual" / "candidates" / candidate_id
    candidate_path = candidate_dir / "candidate.json"
    candidate = acquired_program_candidate_payload(
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=FAILURE_DRIVEN_FOLD_SCOPE,
        program=learned.descriptor(),
        description="Bounded pure numeric fold admitted after a value-dependent sequence task falsified the previous grammar.",
    )
    candidate["supporting_evidence"] = [
        {
            "type": "failure_driven_feature_admission",
            "evaluator_commitment": evaluator_commitment,
            "feature": "fold_numeric",
            "reason": "same-length sequences with different outputs cannot be represented by the prior length-only grammar",
        }
    ]
    candidate["contradictory_evidence"] = [
        {
            "type": "pre_extension_synthesis_failure",
            "grammar_feature": "sequence_folds_disabled",
            "error": legacy_failure,
            "task_commitment": evaluator_commitment,
        }
    ]
    _atomic_json(candidate_path, candidate)

    baseline_engine = engine_factory(root)
    baseline_run_id = _new_runtime_run(baseline_engine)
    baseline = []
    trial = []
    target_task = "withheld-value-dependent-sequence-aggregation"
    for repeat, (query, expected) in enumerate(heldout):
        pre_solved = False
        try:
            _runtime_call(
                baseline_engine,
                baseline_run_id,
                scope=FAILURE_DRIVEN_FOLD_SCOPE,
                tool_id=tool_id,
                value=query,
            )
            pre_solved = True
        except (LearnedToolError, AcquiredProgramToolError):
            pre_solved = False
        artifact = {
            "query_sha256": _digest(query),
            "expected_sha256": _digest(expected),
            "evaluator_commitment": evaluator_commitment,
        }
        baseline.append(
            _measurement(
                target_task,
                repeat,
                1.0 if pre_solved else 0.0,
                {**artifact, "phase": "pre-promotion-runtime"},
                domain="acquired-program:value-dependent-aggregation",
            )
        )
        answer = learned.apply(query)
        trial.append(
            _measurement(
                target_task,
                repeat,
                1.0 if answer == expected else 0.0,
                {**artifact, "phase": "fold-candidate", "answer_sha256": _digest(answer)},
                domain="acquired-program:value-dependent-aggregation",
            )
        )

    prior_before = _retest_prior(root, prior_seed, prior, engine_factory=engine_factory)
    protected_artifact = {
        "candidate_id": prior["candidate_id"],
        "scope": prior["scope"],
        "score": prior_before,
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
            prior_before,
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
        scope=FAILURE_DRIVEN_FOLD_SCOPE,
        baseline_path=baseline_path,
        candidate_path=trial_path,
        target_task_ids=[target_task],
    )
    if not promotion["decision"]["adopt_candidate"]:
        raise RuntimeError("failure-driven fold Candidate failed deterministic regression")

    restarted = engine_factory(root)
    run_id = _new_runtime_run(restarted)
    runtime_results = []
    for query, expected in heldout:
        output = _runtime_call(
            restarted,
            run_id,
            scope=FAILURE_DRIVEN_FOLD_SCOPE,
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
    prior_after = _retest_prior(root, prior_seed, prior, engine_factory=engine_factory)
    state = json.loads(candidate_path.read_text(encoding="utf-8"))
    failure_retained = any(
        isinstance(item, Mapping) and item.get("type") == "pre_extension_synthesis_failure"
        for item in state.get("contradictory_evidence", [])
    )
    report = {
        "schema_version": 1,
        "campaign_id": f"failure-driven-fold-{token}",
        "candidate_id": candidate_id,
        "tool_id": tool_id,
        "scope": FAILURE_DRIVEN_FOLD_SCOPE,
        "evaluator_commitment": evaluator_commitment,
        "pre_extension_failure_observed": True,
        "pre_extension_failure_sha256": _digest(legacy_failure),
        "admitted_feature": "fold_numeric",
        "admitted_expression_sha256": _digest(learned.expression),
        "baseline_score": sum(item["score"] for item in baseline if item["task_id"] == target_task) / len(heldout),
        "candidate_score": sum(item["score"] for item in trial if item["task_id"] == target_task) / len(heldout),
        "promotion": {
            "adopt_candidate": promotion["decision"]["adopt_candidate"],
            "decision_ref": promotion["decision_ref"],
            "trial": promotion.get("trial"),
        },
        "post_restart_runtime_score": sum(1.0 if item["success"] else 0.0 for item in runtime_results) / len(runtime_results),
        "runtime_results": runtime_results,
        "prior_capability_score_before_promotion": prior_before,
        "prior_capability_score_after_promotion": prior_after,
        "pre_extension_failure_retained": failure_retained,
        "evidence_tier": "development",
        "claim_boundary": (
            "This demonstrates a bounded grammar extension justified by a measured internal failure. "
            "The task generator, extension choice, and evidence are internal; this is not independent "
            "production AGI evidence."
        ),
    }
    report["passed"] = (
        report["pre_extension_failure_observed"]
        and report["baseline_score"] == 0.0
        and report["candidate_score"] == 1.0
        and report["promotion"]["adopt_candidate"] is True
        and report["post_restart_runtime_score"] == 1.0
        and report["prior_capability_score_before_promotion"] == 1.0
        and report["prior_capability_score_after_promotion"] == 1.0
        and report["pre_extension_failure_retained"]
    )
    report["digest"] = _digest(report)
    _atomic_json(
        root / ".continual" / "evidence" / "failure-driven-grammar" / f"{report['campaign_id']}.json",
        report,
    )
    return report
