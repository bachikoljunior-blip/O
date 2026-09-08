from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import continual.engine as engine_module
from agi.acquired_programs import ProgramExample, synthesize_program
from continual.acquired_program_tools import (
    AcquiredProgramRegistry,
    AcquiredProgramToolError,
    acquired_program_candidate_payload,
)
from continual.candidate_regression import record_candidate_regression
from continual.learning_engine import LearningEnabledEngine


class NoExecuteModelClient:
    def __init__(self, root: Path):
        self.root = root
        self.model = "no-execute-composed-hidden-model"
        self.calls: list[tuple[str, dict, str | None]] = []

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        self.calls.append((component, payload, prompt_path))
        raise AssertionError(f"model should not mechanically execute acquired program: {component}")


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _measurement(task: str, repeat: int, score: float, *, domain: str, artifact) -> dict:
    return {
        "task_id": task,
        "criterion": "transfer",
        "domain": domain,
        "repeat_index": repeat,
        "passed": score == 1.0,
        "score": score,
        "artifact_sha256": _digest(artifact),
    }


def _install(root: Path, *, candidate_id: str, tool_id: str, scope: str, program: dict) -> Path:
    payload = acquired_program_candidate_payload(
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=scope,
        program=program,
        description="Evaluator-hidden composed pure acquired program.",
    )
    path = root / ".continual" / "candidates" / candidate_id / "candidate.json"
    _write(path, payload)
    return path


def _promote(
    root: Path,
    tmp_path: Path,
    *,
    candidate_id: str,
    scope: str,
    target: str,
    target_domain: str,
    protected: tuple[str, float] | None = None,
) -> dict:
    baseline_path = tmp_path / f"{candidate_id}-baseline.json"
    candidate_path = tmp_path / f"{candidate_id}-candidate.json"
    baseline = [
        _measurement(target, 0, 0.0, domain=target_domain, artifact={"phase": "baseline", "repeat": 0}),
        _measurement(target, 1, 0.0, domain=target_domain, artifact={"phase": "baseline", "repeat": 1}),
    ]
    candidate = [
        _measurement(target, 0, 1.0, domain=target_domain, artifact={"phase": "candidate", "repeat": 0}),
        _measurement(target, 1, 1.0, domain=target_domain, artifact={"phase": "candidate", "repeat": 1}),
    ]
    if protected is not None:
        protected_task, protected_score = protected
        baseline.append(
            _measurement(
                protected_task,
                0,
                1.0,
                domain="protected-acquired-program",
                artifact={"task": protected_task, "phase": "baseline"},
            )
        )
        candidate.append(
            _measurement(
                protected_task,
                0,
                protected_score,
                domain="protected-acquired-program",
                artifact={"task": protected_task, "phase": "candidate"},
            )
        )
    _write(baseline_path, baseline)
    _write(candidate_path, candidate)
    return record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline_path,
        candidate_path=candidate_path,
        target_task_ids=[target],
    )


def _new_run(engine: LearningEnabledEngine) -> str:
    run_id = engine.store.new_id("run")
    run_dir = engine.store.run_dir(run_id)
    run_dir.mkdir(parents=True)
    engine.store.atomic_json(
        run_dir / "snapshot.json",
        {"revision": 0, "status": "continue", "phase": "unit_pending"},
    )
    return run_id


def _runtime(
    engine: LearningEnabledEngine,
    run_id: str,
    *,
    scope: str,
    tool_id: str,
    value,
):
    return engine._invoke(
        run_id,
        "execute",
        {
            "snapshot": {"revision": 0},
            "execution_unit": {
                "goal": "apply regression-verified hidden composed program",
                "scope": scope,
                "learned_tool_call": {"tool_id": tool_id, "input": value},
            },
        },
    )["result"]["output"]


def test_existing_fold_and_conversion_kernels_compose_on_hidden_task_without_grammar_growth(
    runtime_repo: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    protected_scope = "heldout/protected-conditional"
    protected_candidate = "candidate-protected-conditional"
    protected_tool = "acquired.protected.conditional"
    protected_program = synthesize_program(
        input_domain="numeric",
        output_domain="numeric",
        examples=[
            ProgramExample(-4, 0),
            ProgramExample(-1, 0),
            ProgramExample(0, 0),
            ProgramExample(3, 3),
            ProgramExample(8, 8),
        ],
        max_nodes=5,
    )
    _install(
        runtime_repo,
        candidate_id=protected_candidate,
        tool_id=protected_tool,
        scope=protected_scope,
        program=protected_program.descriptor(),
    )
    protected_report = _promote(
        runtime_repo,
        tmp_path,
        candidate_id=protected_candidate,
        scope=protected_scope,
        target="protected-conditional-acquisition",
        target_domain="piecewise-numeric",
    )
    assert protected_report["decision"]["adopt_candidate"] is True
    assert AcquiredProgramRegistry(runtime_repo).apply(
        scope=protected_scope,
        tool_id=protected_tool,
        value=-9,
    ) == 0

    demonstrations = [
        ProgramExample([1, 2], "3"),
        ProgramExample([3, 4], "7"),
        ProgramExample([-2, 7], "5"),
        ProgramExample([10, -3], "7"),
    ]
    heldout = [
        ([9, 8, -2], "15"),
        ([5, 5, 5, 5], "20"),
        ([-8, 3, 9, -1], "3"),
        ([100, -40, -50, 7, 1], "18"),
    ]
    evaluator_commitment = _digest(
        {
            "task": "sum numeric sequence values and render decimal text",
            "heldout": heldout,
        }
    )

    learned = synthesize_program(
        input_domain="sequence",
        output_domain="string",
        examples=demonstrations,
        max_nodes=4,
        allow_sequence_folds=True,
        allow_conditionals=False,
    )
    assert learned.expression == {
        "op": "to_string",
        "arg": {
            "op": "fold_numeric",
            "arg": {"op": "input"},
            "reducer": "add",
            "initial": 0,
        },
    }
    assert [learned.apply(query) for query, _ in heldout] == [expected for _, expected in heldout]
    assert learned.support_sha256 != evaluator_commitment

    scope = "heldout/composed-fold-to-string"
    candidate_id = "candidate-composed-fold-to-string"
    tool_id = "acquired.composed.fold-to-string"
    _install(
        runtime_repo,
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=scope,
        program=learned.descriptor(),
    )
    with pytest.raises(AcquiredProgramToolError, match="not verified for scope"):
        AcquiredProgramRegistry(runtime_repo).apply(scope=scope, tool_id=tool_id, value=[1, 2])

    report = _promote(
        runtime_repo,
        tmp_path,
        candidate_id=candidate_id,
        scope=scope,
        target="withheld-value-aggregation-cross-domain-conversion",
        target_domain="composed-acquired-program",
        protected=("protected-conditional-acquisition", 1.0),
    )
    assert report["decision"]["adopt_candidate"] is True

    monkeypatch.setattr(engine_module, "ModelClient", NoExecuteModelClient)
    fresh = LearningEnabledEngine(runtime_repo)
    run_id = _new_run(fresh)
    runtime_answers = [
        _runtime(fresh, run_id, scope=scope, tool_id=tool_id, value=query)
        for query, _ in heldout
    ]
    assert runtime_answers == [expected for _, expected in heldout]
    assert _runtime(
        fresh,
        run_id,
        scope=protected_scope,
        tool_id=protected_tool,
        value=11,
    ) == 11
    assert fresh.model.calls == []
