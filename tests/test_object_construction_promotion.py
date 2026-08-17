from __future__ import annotations

import hashlib
import json
from pathlib import Path

import continual.engine as engine_module
from agi.acquired_programs import ProgramExample, synthesize_program
from continual.acquired_program_tools import (
    AcquiredProgramRegistry,
    acquired_program_candidate_payload,
)
from continual.candidate_regression import record_candidate_regression
from continual.learning_engine import LearningEnabledEngine


class NoExecuteModelClient:
    def __init__(self, root: Path):
        self.root = root
        self.model = "no-execute-object-construction-test-model"
        self.calls: list[tuple[str, dict, str | None]] = []

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        self.calls.append((component, payload, prompt_path))
        raise AssertionError(f"model should not execute promoted object constructor: {component}")


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
                "goal": "apply the regression-verified bounded object constructor",
                "scope": scope,
                "learned_tool_call": {"tool_id": tool_id, "input": value},
            },
        },
    )


def test_numeric_to_object_constructor_requires_exact_scope_promotion_and_replays_across_fresh_engines(
    runtime_repo: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    scope = "agi/acquired-program/object-construction-v1"
    candidate_id = "candidate-numeric-object-constructor"
    tool_id = "acquired.object.construct-score"
    learned = synthesize_program(
        input_domain="numeric",
        output_domain="object",
        examples=[
            ProgramExample(2, {"score": 2}),
            ProgramExample(-3, {"score": -3}),
            ProgramExample(7, {"score": 7}),
        ],
        max_nodes=3,
    )
    candidate_path = runtime_repo / ".continual" / "candidates" / candidate_id / "candidate.json"
    _write(
        candidate_path,
        acquired_program_candidate_payload(
            candidate_id=candidate_id,
            tool_id=tool_id,
            scope=scope,
            program=learned.descriptor(),
            description="Finite one-field object constructor learned from numeric demonstrations.",
        ),
    )

    registry = AcquiredProgramRegistry(runtime_repo)
    assert registry.available(scope) == ()

    baseline_path = tmp_path / "baseline.json"
    candidate_measurements_path = tmp_path / "candidate.json"
    _write(
        baseline_path,
        [
            _measurement("withheld-object-construction", 0, 0.0, domain="structured"),
            _measurement("withheld-object-construction", 1, 0.0, domain="structured"),
            _measurement("protected-prior", 0, 1.0, domain="protected"),
        ],
    )
    _write(
        candidate_measurements_path,
        [
            _measurement("withheld-object-construction", 0, 1.0, domain="structured"),
            _measurement("withheld-object-construction", 1, 1.0, domain="structured"),
            _measurement("protected-prior", 0, 1.0, domain="protected"),
        ],
    )
    promoted = record_candidate_regression(
        runtime_repo,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline_path,
        candidate_path=candidate_measurements_path,
        target_task_ids=["withheld-object-construction"],
    )
    assert promoted["decision"]["adopt_candidate"] is True
    assert promoted["decision"]["target_repeats"] == {"withheld-object-construction": 2}
    assert promoted["decision"]["mean_target_gain_by_task"] == {
        "withheld-object-construction": 1.0
    }

    available = AcquiredProgramRegistry(runtime_repo).available(scope)
    assert len(available) == 1
    assert available[0].tool_id == tool_id
    assert available[0].descriptor()["output_domain"] == "object"

    state_before = json.loads(candidate_path.read_text(encoding="utf-8"))
    regression_refs_before = list(state_before["regression_decision_refs"])
    contradictory_before = list(state_before["contradictory_evidence"])

    monkeypatch.setattr(engine_module, "ModelClient", NoExecuteModelClient)
    outputs = []
    for value in (11, -8, 42):
        engine = LearningEnabledEngine(runtime_repo)
        run_id = _new_run(engine)
        response = _invoke(engine, run_id, scope=scope, tool_id=tool_id, value=value)
        outputs.append(response["result"]["output"])
        assert engine.model.calls == []

    assert outputs == [{"score": 11}, {"score": -8}, {"score": 42}]

    state_after = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert state_after["regression_decision_refs"] == regression_refs_before
    assert state_after["contradictory_evidence"] == contradictory_before
