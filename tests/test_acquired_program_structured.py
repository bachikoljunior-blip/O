from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import continual.engine as engine_module
from agi.acquired_programs import (
    AcquiredProgramError,
    ProgramExample,
    execute_program,
    synthesize_program,
    validate_program_descriptor,
)
from continual.acquired_program_tools import acquired_program_candidate_payload
from continual.candidate_regression import record_candidate_regression
from continual.learning_engine import LearningEnabledEngine


class NoExecuteModelClient:
    def __init__(self, root: Path):
        self.root = root
        self.model = "no-execute-structured-program-test-model"
        self.calls: list[tuple[str, dict, str | None]] = []

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        self.calls.append((component, payload, prompt_path))
        raise AssertionError(f"model should not execute acquired program: {component}")


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _measurement(task: str, repeat: int, score: float, *, domain: str) -> dict:
    artifact = hashlib.sha256(f"{task}:{repeat}:{score}:{domain}".encode()).hexdigest()
    return {
        "task_id": task,
        "criterion": "self_improvement",
        "domain": domain,
        "repeat_index": repeat,
        "passed": score == 1.0,
        "score": score,
        "artifact_sha256": artifact,
    }


def _new_run(engine: LearningEnabledEngine) -> str:
    run_id = engine.store.new_id("run")
    run_dir = engine.store.run_dir(run_id)
    run_dir.mkdir(parents=True)
    engine.store.atomic_json(
        run_dir / "snapshot.json",
        {"revision": 0, "status": "continue", "phase": "unit_pending"},
    )
    return run_id


def _invoke(engine: LearningEnabledEngine, run_id: str, *, scope: str, tool_id: str, value):
    return engine._invoke(
        run_id,
        "execute",
        {
            "snapshot": {"revision": 0},
            "execution_unit": {
                "goal": "apply a verified finite structured acquired program",
                "scope": scope,
                "learned_tool_call": {"tool_id": tool_id, "input": value},
            },
        },
    )


def test_object_projection_presence_and_numeric_predicate_transfer_to_unseen_inputs() -> None:
    projection = synthesize_program(
        input_domain="object",
        output_domain="numeric",
        examples=[
            ProgramExample({"score": 2, "label": "a"}, 2),
            ProgramExample({"score": 7, "label": "b"}, 7),
            ProgramExample({"score": -3, "label": "c"}, -3),
        ],
        max_nodes=4,
    )
    presence = synthesize_program(
        input_domain="object",
        output_domain="boolean",
        examples=[
            ProgramExample({"approved": 0, "x": 1}, True),
            ProgramExample({"x": 2}, False),
            ProgramExample({"approved": False, "x": 3}, True),
            ProgramExample({"other": 4}, False),
        ],
        max_nodes=4,
    )
    predicate = synthesize_program(
        input_domain="object",
        output_domain="boolean",
        examples=[
            ProgramExample({"score": -4}, False),
            ProgramExample({"score": 0}, False),
            ProgramExample({"score": 1}, True),
            ProgramExample({"score": 9}, True),
        ],
        max_nodes=6,
    )

    assert projection.expression == {
        "op": "object_get",
        "arg": {"op": "input"},
        "key": "score",
        "domain": "numeric",
    }
    assert projection.apply({"score": 13, "unseen": {"nested": True}}) == 13
    assert presence.expression["op"] == "object_has_key"
    assert presence.apply({"approved": None, "novel": [1, 2]}) is True
    assert presence.apply({"novel": [1, 2]}) is False
    assert predicate.expression["op"] == "ge_numeric"
    assert predicate.apply({"score": -1}) is False
    assert predicate.apply({"score": 5}) is True


def test_structured_ops_are_explicitly_falsifiable_and_fail_closed_on_bad_objects() -> None:
    examples = [
        ProgramExample({"score": 2}, 2),
        ProgramExample({"score": 7}, 7),
        ProgramExample({"score": -3}, -3),
    ]
    with pytest.raises(AcquiredProgramError, match="no bounded pure typed program"):
        synthesize_program(
            input_domain="object",
            output_domain="numeric",
            examples=examples,
            max_nodes=5,
            allow_structured_ops=False,
        )

    descriptor = {
        "input_domain": "object",
        "output_domain": "numeric",
        "expression": {
            "op": "object_get",
            "arg": {"op": "input"},
            "key": "score",
            "domain": "numeric",
        },
        "effects": [],
        "max_steps": 8,
        "max_output_length": 128,
    }
    assert execute_program(descriptor, {"score": 5}) == 5
    with pytest.raises(AcquiredProgramError, match="key is absent"):
        execute_program(descriptor, {"other": 5})
    with pytest.raises(AcquiredProgramError, match="invalid numeric value"):
        execute_program(descriptor, {"score": "5"})

    bad_key = dict(descriptor)
    bad_key["expression"] = {
        "op": "object_get",
        "arg": {"op": "input"},
        "key": "",
        "domain": "numeric",
    }
    with pytest.raises(AcquiredProgramError, match="object key"):
        validate_program_descriptor(bad_key)

    too_deep = {"score": 5}
    for index in range(8):
        too_deep = {f"level{index}": too_deep}
    identity = {
        "input_domain": "object",
        "output_domain": "object",
        "expression": {"op": "input"},
        "effects": [],
        "max_steps": 4,
        "max_output_length": 1024,
    }
    with pytest.raises(AcquiredProgramError, match="depth bound"):
        execute_program(identity, too_deep)


def test_failed_single_repeat_is_retained_before_repeated_promotion_and_fresh_engine_replay(
    runtime_repo: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    scope = "agi/acquired-program/structured-projection-v1"
    candidate_id = "candidate-structured-score-projection"
    tool_id = "acquired.object.project-score"
    learned = synthesize_program(
        input_domain="object",
        output_domain="numeric",
        examples=[
            ProgramExample({"score": 2, "noise": "a"}, 2),
            ProgramExample({"score": 7, "noise": "b"}, 7),
            ProgramExample({"score": -3, "noise": "c"}, -3),
        ],
        max_nodes=4,
    )
    candidate_path = runtime_repo / ".continual" / "candidates" / candidate_id / "candidate.json"
    _write(
        candidate_path,
        acquired_program_candidate_payload(
            candidate_id=candidate_id,
            tool_id=tool_id,
            scope=scope,
            program=learned.descriptor(),
            description="Finite exact-key object projection learned from demonstrations.",
        ),
    )

    failed_baseline = tmp_path / "failed-baseline.json"
    failed_candidate = tmp_path / "failed-candidate.json"
    _write(
        failed_baseline,
        [
            _measurement("withheld-structured-target", 0, 0.0, domain="structured"),
            _measurement("protected-prior", 0, 1.0, domain="protected"),
        ],
    )
    _write(
        failed_candidate,
        [
            _measurement("withheld-structured-target", 0, 1.0, domain="structured"),
            _measurement("protected-prior", 0, 1.0, domain="protected"),
        ],
    )
    failed = record_candidate_regression(
        runtime_repo,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=failed_baseline,
        candidate_path=failed_candidate,
        target_task_ids=["withheld-structured-target"],
    )
    assert failed["decision"]["adopt_candidate"] is False
    assert any(
        item["type"] == "insufficient_target_repeats"
        for item in failed["decision"]["negative_evidence"]
    )

    baseline_path = tmp_path / "baseline.json"
    candidate_measurements_path = tmp_path / "candidate.json"
    _write(
        baseline_path,
        [
            _measurement("withheld-structured-target", 0, 0.0, domain="structured"),
            _measurement("withheld-structured-target", 1, 0.0, domain="structured"),
            _measurement("protected-prior", 0, 1.0, domain="protected"),
        ],
    )
    _write(
        candidate_measurements_path,
        [
            _measurement("withheld-structured-target", 0, 1.0, domain="structured"),
            _measurement("withheld-structured-target", 1, 1.0, domain="structured"),
            _measurement("protected-prior", 0, 1.0, domain="protected"),
        ],
    )
    promoted = record_candidate_regression(
        runtime_repo,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline_path,
        candidate_path=candidate_measurements_path,
        target_task_ids=["withheld-structured-target"],
    )
    assert promoted["decision"]["adopt_candidate"] is True
    assert promoted["decision"]["target_repeats"] == {"withheld-structured-target": 2}
    assert promoted["decision"]["mean_target_gain_by_task"] == {
        "withheld-structured-target": 1.0
    }

    state_before = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert len(state_before["regression_decision_refs"]) == 2
    assert any(
        item.get("type") == "deterministic_regression_gate_failure"
        for item in state_before["contradictory_evidence"]
    )

    monkeypatch.setattr(engine_module, "ModelClient", NoExecuteModelClient)
    outputs = []
    for score in (11, -8):
        engine = LearningEnabledEngine(runtime_repo)
        run_id = _new_run(engine)
        response = _invoke(
            engine,
            run_id,
            scope=scope,
            tool_id=tool_id,
            value={"score": score, "fresh": True},
        )
        outputs.append(response["result"]["output"])
        assert engine.model.calls == []
    assert outputs == [11, -8]

    state_after = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert state_after["regression_decision_refs"] == state_before["regression_decision_refs"]
    assert state_after["contradictory_evidence"] == state_before["contradictory_evidence"]
