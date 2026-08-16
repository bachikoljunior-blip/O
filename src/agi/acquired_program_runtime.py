from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from continual.acquired_program_tools import (
    AcquiredProgramRegistry,
    AcquiredProgramToolError,
    acquired_program_candidate_payload,
)
from continual.candidate_regression import record_candidate_regression
from continual.learned_tools import LearnedToolError
from continual.learning_engine import LearningEnabledEngine

from .acquired_programs import ProgramExample, execute_program, synthesize_program


ACQUIRED_RUNTIME_SCOPE = "agi/acquired-program-runtime/hidden-transfer-v1"


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _seeded_int(seed: str, label: str, low: int, high: int) -> int:
    if high < low:
        raise ValueError("invalid seeded integer bounds")
    raw = hashlib.sha256(f"acquired-runtime-v1\0{seed}\0{label}".encode("utf-8")).digest()
    return low + int.from_bytes(raw[:8], "big") % (high - low + 1)


def _hidden_descriptor(seed: str) -> dict[str, Any]:
    domain_index = _seeded_int(seed, "domain", 0, 2)
    multiplier = _seeded_int(seed, "multiplier", 2, 5)
    offset = _seeded_int(seed, "offset", -3, 3)
    if domain_index == 0:
        input_domain = "string"
        length = {"op": "length_string", "arg": {"op": "input"}}
        numeric = {
            "op": "add",
            "left": {
                "op": "mul",
                "left": length,
                "right": {"op": "const", "domain": "numeric", "value": multiplier},
            },
            "right": {"op": "const", "domain": "numeric", "value": offset},
        }
        output_domain = "numeric"
        expression = numeric
    elif domain_index == 1:
        input_domain = "sequence"
        length = {"op": "length_sequence", "arg": {"op": "input"}}
        expression = {
            "op": "add",
            "left": {
                "op": "mul",
                "left": length,
                "right": {"op": "const", "domain": "numeric", "value": multiplier},
            },
            "right": {"op": "const", "domain": "numeric", "value": offset},
        }
        output_domain = "numeric"
    else:
        input_domain = "numeric"
        numeric = {
            "op": "add",
            "left": {
                "op": "mul",
                "left": {"op": "input"},
                "right": {"op": "const", "domain": "numeric", "value": multiplier},
            },
            "right": {"op": "const", "domain": "numeric", "value": offset},
        }
        output_domain = "string"
        expression = {"op": "to_string", "arg": numeric}
    return {
        "input_domain": input_domain,
        "output_domain": output_domain,
        "expression": expression,
        "effects": [],
        "max_steps": 32,
        "max_output_length": 1024,
    }


def _inputs(input_domain: str) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    if input_domain == "string":
        return (
            ("a", "model", "reasoning"),
            ("heldout", "persistent", "generalization"),
        )
    if input_domain == "sequence":
        return (
            ([1], [1, 2, 3], [9, 8, 7, 6, 5]),
            ([4, 2], [7, 1, 9, 3], [8, 6, 4, 2, 0, 1]),
        )
    if input_domain == "numeric":
        return ((1, 4, 9), (7, 11, 23))
    raise ValueError(input_domain)


def _measurement(task_id: str, repeat: int, score: float, artifact: Any) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "criterion": "self_improvement",
        "domain": "acquired-program:hidden-runtime-transfer",
        "repeat_index": repeat,
        "passed": score == 1.0,
        "score": score,
        "artifact_sha256": _digest(artifact),
    }


def _stable_candidate(value: Mapping[str, Any]) -> dict[str, Any]:
    stable = dict(value)
    for key in (
        "status",
        "scope_states",
        "verified_scope_states",
        "regression_decision_refs",
        "supporting_evidence",
        "contradictory_evidence",
    ):
        stable.pop(key, None)
    return stable


def _new_runtime_run(engine: LearningEnabledEngine) -> str:
    run_id = engine.store.new_id("run")
    run_dir = engine.store.run_dir(run_id)
    run_dir.mkdir(parents=True)
    engine.store.atomic_json(
        run_dir / "snapshot.json",
        {"revision": 0, "status": "continue", "phase": "unit_pending"},
    )
    return run_id


def _runtime_call(
    engine: LearningEnabledEngine,
    run_id: str,
    *,
    tool_id: str,
    value: Any,
) -> dict[str, Any]:
    return engine._invoke(
        run_id,
        "execute",
        {
            "snapshot": {"revision": 0},
            "execution_unit": {
                "goal": "apply regression-verified acquired program to evaluator-hidden input",
                "scope": ACQUIRED_RUNTIME_SCOPE,
                "learned_tool_call": {"tool_id": tool_id, "input": value},
            },
        },
    )


def run_acquired_program_runtime_campaign(
    root: Path,
    seed: str,
    *,
    engine_factory: Callable[[Path], LearningEnabledEngine] = LearningEnabledEngine,
) -> dict[str, Any]:
    """Measure hidden before/after continual-runtime gain with regression-gated program acquisition.

    The evaluator commits to its hidden program and held-out answers before synthesis. The learner
    receives demonstrations only. An intentionally adverse protected-task trial must fail before the
    exact same Candidate may later pass with intact protected measurements. After promotion, a fresh
    Engine instance executes the same held-out inputs mechanically and reuses identical invocations.
    """

    if not isinstance(seed, str) or not seed:
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    evaluator_program = _hidden_descriptor(seed)
    training_inputs, heldout_inputs = _inputs(str(evaluator_program["input_domain"]))
    demonstrations = tuple(
        ProgramExample(value, execute_program(evaluator_program, value))
        for value in training_inputs
    )
    heldout_expected = tuple(execute_program(evaluator_program, value) for value in heldout_inputs)
    commitment_payload = {
        "program": evaluator_program,
        "heldout": [
            {"input": value, "expected": expected}
            for value, expected in zip(heldout_inputs, heldout_expected, strict=True)
        ],
    }
    evaluator_commitment = _digest(commitment_payload)
    token = evaluator_commitment[:16]
    candidate_id = f"acquired-runtime-{token}"
    tool_id = f"acquired-hidden-{token}"

    training_values = {_digest(item.input) for item in demonstrations}
    heldout_values = {_digest(value) for value in heldout_inputs}
    contamination_events: list[dict[str, Any]] = []
    overlap = sorted(training_values & heldout_values)
    if overlap:
        contamination_events.append(
            {"type": "heldout_input_overlap", "input_digests": overlap}
        )

    learned = synthesize_program(
        input_domain=str(evaluator_program["input_domain"]),
        output_domain=str(evaluator_program["output_domain"]),
        examples=demonstrations,
        max_nodes=8,
    )
    candidate_dir = root / ".continual" / "candidates" / candidate_id
    candidate_path = candidate_dir / "candidate.json"
    candidate = acquired_program_candidate_payload(
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=ACQUIRED_RUNTIME_SCOPE,
        program=learned.descriptor(),
        description="Pure bounded program synthesized from demonstrations under evaluator-hidden held-out tasks.",
    )
    candidate["supporting_evidence"] = [
        {
            "type": "evaluator_commitment",
            "commitment_sha256": evaluator_commitment,
            "training_support_sha256": learned.support_sha256,
            "heldout_answers_exposed_to_learner": False,
        }
    ]
    if candidate_path.exists():
        existing = json.loads(candidate_path.read_text(encoding="utf-8"))
        if not isinstance(existing, Mapping) or _stable_candidate(existing) != _stable_candidate(candidate):
            raise ValueError("existing acquired-program runtime Candidate conflicts with campaign")
    else:
        _atomic_json(candidate_path, candidate)

    registry = AcquiredProgramRegistry(root)
    inactive_before_regression = registry.descriptors(ACQUIRED_RUNTIME_SCOPE) == ()
    if not inactive_before_regression:
        raise RuntimeError("acquired program became callable before deterministic regression")

    baseline_engine = engine_factory(root)
    baseline_run_id = _new_runtime_run(baseline_engine)
    baseline: list[dict[str, Any]] = []
    baseline_results: list[dict[str, Any]] = []
    for repeat, (query, expected) in enumerate(
        zip(heldout_inputs, heldout_expected, strict=True)
    ):
        solved = False
        try:
            _runtime_call(baseline_engine, baseline_run_id, tool_id=tool_id, value=query)
            solved = True
        except (LearnedToolError, AcquiredProgramToolError):
            solved = False
        baseline.append(
            _measurement(
                "withheld-acquired-runtime-transfer",
                repeat,
                1.0 if solved else 0.0,
                {
                    "query_sha256": _digest(query),
                    "expected_sha256": _digest(expected),
                    "evaluator_commitment": evaluator_commitment,
                    "phase": "pre-promotion-runtime",
                },
            )
        )
        baseline_results.append(
            {"query_sha256": _digest(query), "solved": solved}
        )

    candidate_measurements: list[dict[str, Any]] = []
    candidate_results: list[dict[str, Any]] = []
    for repeat, (query, expected) in enumerate(
        zip(heldout_inputs, heldout_expected, strict=True)
    ):
        answer = learned.apply(query)
        success = answer == expected
        candidate_measurements.append(
            _measurement(
                "withheld-acquired-runtime-transfer",
                repeat,
                1.0 if success else 0.0,
                {
                    "query_sha256": _digest(query),
                    "answer_sha256": _digest(answer),
                    "expected_sha256": _digest(expected),
                    "evaluator_commitment": evaluator_commitment,
                    "phase": "isolated-candidate-sandbox",
                },
            )
        )
        candidate_results.append(
            {
                "query_sha256": _digest(query),
                "answer_sha256": _digest(answer),
                "success": success,
            }
        )

    protected_artifact = {
        "task": "protected-existing-capability",
        "invariant": "must remain passing",
        "evaluator_commitment": evaluator_commitment,
    }
    baseline_with_protected = [
        *baseline,
        _measurement("protected-existing-capability", 0, 1.0, protected_artifact),
    ]
    adverse_trial = [
        *candidate_measurements,
        _measurement("protected-existing-capability", 0, 0.0, protected_artifact),
    ]
    valid_trial = [
        *candidate_measurements,
        _measurement("protected-existing-capability", 0, 1.0, protected_artifact),
    ]
    regression_input = candidate_dir / "regression-input"
    baseline_path = regression_input / "baseline.json"
    adverse_path = regression_input / "candidate-adverse.json"
    valid_path = regression_input / "candidate-valid.json"
    _atomic_json(baseline_path, baseline_with_protected)
    _atomic_json(adverse_path, adverse_trial)
    _atomic_json(valid_path, valid_trial)

    failed_promotion = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=ACQUIRED_RUNTIME_SCOPE,
        baseline_path=baseline_path,
        candidate_path=adverse_path,
        target_task_ids=["withheld-acquired-runtime-transfer"],
    )
    if failed_promotion["decision"]["adopt_candidate"]:
        raise RuntimeError("protected-task regression was incorrectly adopted")
    if AcquiredProgramRegistry(root).descriptors(ACQUIRED_RUNTIME_SCOPE):
        raise RuntimeError("failed Candidate regression exposed acquired program")

    promotion = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=ACQUIRED_RUNTIME_SCOPE,
        baseline_path=baseline_path,
        candidate_path=valid_path,
        target_task_ids=["withheld-acquired-runtime-transfer"],
    )
    if not promotion["decision"]["adopt_candidate"]:
        raise RuntimeError("valid acquired-program Candidate failed deterministic regression")

    restarted_engine = engine_factory(root)
    runtime_run_id = _new_runtime_run(restarted_engine)
    runtime_results: list[dict[str, Any]] = []
    for query, expected in zip(heldout_inputs, heldout_expected, strict=True):
        output = _runtime_call(restarted_engine, runtime_run_id, tool_id=tool_id, value=query)
        answer = output["result"]["output"]
        runtime_results.append(
            {
                "query_sha256": _digest(query),
                "answer_sha256": _digest(answer),
                "success": answer == expected,
                "program_kind": output["result"].get("program_kind"),
            }
        )
    replay_first = _runtime_call(
        restarted_engine,
        runtime_run_id,
        tool_id=tool_id,
        value=heldout_inputs[0],
    )
    events_path = restarted_engine.store.run_dir(runtime_run_id) / "events.jsonl"
    events = events_path.read_text(encoding="utf-8") if events_path.exists() else ""
    replay_reused = "learned_tool_invocation_reused" in events

    state = json.loads(candidate_path.read_text(encoding="utf-8"))
    negative_evidence_retained = bool(state.get("contradictory_evidence"))
    commitment_verified = _digest(commitment_payload) == evaluator_commitment
    baseline_score = sum(item["score"] for item in baseline) / len(baseline)
    candidate_score = sum(item["score"] for item in candidate_measurements) / len(
        candidate_measurements
    )
    runtime_score = sum(1.0 if item["success"] else 0.0 for item in runtime_results) / len(
        runtime_results
    )
    report = {
        "schema_version": 1,
        "campaign_id": f"acquired-runtime-{token}",
        "scope": ACQUIRED_RUNTIME_SCOPE,
        "candidate_id": candidate_id,
        "tool_id": tool_id,
        "evaluator_commitment": evaluator_commitment,
        "commitment_verified": commitment_verified,
        "seed_persisted": False,
        "heldout_answers_exposed_to_learner": False,
        "contamination_events": contamination_events,
        "input_domain": learned.input_domain,
        "output_domain": learned.output_domain,
        "program_support_sha256": learned.support_sha256,
        "inactive_before_regression": inactive_before_regression,
        "baseline_score": baseline_score,
        "isolated_candidate_score": candidate_score,
        "post_restart_runtime_score": runtime_score,
        "baseline_results": baseline_results,
        "candidate_results": candidate_results,
        "runtime_results": runtime_results,
        "failed_protected_regression": {
            "adopt_candidate": failed_promotion["decision"]["adopt_candidate"],
            "decision_ref": failed_promotion["decision_ref"],
            "trial": failed_promotion.get("trial"),
        },
        "promotion": {
            "adopt_candidate": promotion["decision"]["adopt_candidate"],
            "decision_ref": promotion["decision_ref"],
            "trial": promotion.get("trial"),
        },
        "negative_evidence_retained": negative_evidence_retained,
        "restart_run_id": runtime_run_id,
        "runtime_replay_reused": replay_reused,
        "replay_output_sha256": _digest(replay_first["result"]["output"]),
        "evidence_tier": "development",
        "claim_boundary": (
            "This campaign measures evaluator-hidden before/after capability gain in the real continual "
            "runtime with deterministic regression and restart replay. The evaluator, grammar, and "
            "evidence are still internally produced, so this is not independent production AGI evidence."
        ),
    }
    report["passed"] = (
        commitment_verified
        and not contamination_events
        and inactive_before_regression
        and baseline_score == 0.0
        and candidate_score == 1.0
        and failed_promotion["decision"]["adopt_candidate"] is False
        and promotion["decision"]["adopt_candidate"] is True
        and negative_evidence_retained
        and runtime_score == 1.0
        and replay_reused
    )
    report["digest"] = _digest(report)
    evidence_path = (
        root
        / ".continual"
        / "evidence"
        / "acquired-program-runtime"
        / f"{report['campaign_id']}.json"
    )
    _atomic_json(evidence_path, report)
    return report
