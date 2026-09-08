from __future__ import annotations

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
        self.model = "no-execute-conditional-test-model"
        self.calls: list[tuple[str, dict, str | None]] = []

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        self.calls.append((component, payload, prompt_path))
        raise AssertionError(f"model should not mechanically execute acquired program: {component}")


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _measurement(task: str, repeat: int, score: float, *, domain: str) -> dict:
    return {
        "task_id": task,
        "criterion": "self_improvement",
        "domain": domain,
        "repeat_index": repeat,
        "passed": score == 1.0,
        "score": score,
        "artifact_sha256": f"{repeat + 41:02x}" * 32,
    }


def _install(
    root: Path,
    *,
    candidate_id: str,
    tool_id: str,
    scope: str,
    program: dict,
    contradictory_evidence: list[dict] | None = None,
) -> Path:
    payload = acquired_program_candidate_payload(
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=scope,
        program=program,
        description="Pure bounded acquired program admitted only after a hidden task falsified the previous grammar.",
    )
    payload["contradictory_evidence"] = list(contradictory_evidence or [])
    path = root / ".continual" / "candidates" / candidate_id / "candidate.json"
    _write(path, payload)
    return path


def _promote(
    root: Path,
    tmp_path: Path,
    *,
    candidate_id: str,
    scope: str,
    target_task: str,
    protected_task: str | None = None,
) -> dict:
    baseline = tmp_path / f"{candidate_id}-baseline.json"
    candidate = tmp_path / f"{candidate_id}-candidate.json"
    baseline_values = [
        _measurement(target_task, 0, 0.0, domain="hidden-piecewise"),
        _measurement(target_task, 1, 0.0, domain="hidden-piecewise"),
    ]
    candidate_values = [
        _measurement(target_task, 0, 1.0, domain="hidden-piecewise"),
        _measurement(target_task, 1, 1.0, domain="hidden-piecewise"),
    ]
    if protected_task is not None:
        baseline_values.append(
            _measurement(protected_task, 0, 1.0, domain="protected-acquired-program")
        )
        candidate_values.append(
            _measurement(protected_task, 0, 1.0, domain="protected-acquired-program")
        )
    _write(baseline, baseline_values)
    _write(candidate, candidate_values)
    return record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline,
        candidate_path=candidate,
        target_task_ids=[target_task],
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


def _runtime_call(
    engine: LearningEnabledEngine,
    run_id: str,
    *,
    scope: str,
    tool_id: str,
    value,
) -> dict:
    return engine._invoke(
        run_id,
        "execute",
        {
            "snapshot": {"revision": 0},
            "execution_unit": {
                "goal": "apply exact-scope regression-verified acquired program",
                "scope": scope,
                "learned_tool_call": {"tool_id": tool_id, "input": value},
            },
        },
    )


def test_hidden_piecewise_task_must_falsify_old_grammar_before_conditional_is_admitted() -> None:
    demonstrations = [
        ProgramExample(-5, 0),
        ProgramExample(-2, 0),
        ProgramExample(0, 0),
        ProgramExample(3, 3),
        ProgramExample(7, 7),
    ]

    with pytest.raises(AcquiredProgramError, match="no bounded pure typed program"):
        synthesize_program(
            input_domain="numeric",
            output_domain="numeric",
            examples=demonstrations,
            max_nodes=5,
            allow_conditionals=False,
        )

    learned = synthesize_program(
        input_domain="numeric",
        output_domain="numeric",
        examples=demonstrations,
        max_nodes=5,
        allow_conditionals=True,
    )
    assert learned.expression == {
        "op": "if_nonnegative",
        "condition": {"op": "input"},
        "then": {"op": "input"},
        "else": {"op": "const", "domain": "numeric", "value": 0},
    }
    assert [learned.apply(value) for value in (-11, -1, 0, 4, 19)] == [0, 0, 0, 4, 19]


def test_conditional_descriptor_is_typed_pure_and_resource_bounded() -> None:
    with pytest.raises(AcquiredProgramError, match="condition expects numeric"):
        validate_program_descriptor(
            {
                "input_domain": "string",
                "output_domain": "string",
                "expression": {
                    "op": "if_nonnegative",
                    "condition": {"op": "input"},
                    "then": {"op": "input"},
                    "else": {"op": "const", "domain": "string", "value": ""},
                },
                "effects": [],
                "max_steps": 8,
            }
        )

    with pytest.raises(AcquiredProgramError, match="branches must share a domain"):
        validate_program_descriptor(
            {
                "input_domain": "numeric",
                "output_domain": "numeric",
                "expression": {
                    "op": "if_nonnegative",
                    "condition": {"op": "input"},
                    "then": {"op": "input"},
                    "else": {"op": "const", "domain": "string", "value": ""},
                },
                "effects": [],
                "max_steps": 8,
            }
        )

    with pytest.raises(AcquiredProgramError, match="node count exceeds max_steps"):
        validate_program_descriptor(
            {
                "input_domain": "numeric",
                "output_domain": "numeric",
                "expression": {
                    "op": "if_nonnegative",
                    "condition": {"op": "input"},
                    "then": {"op": "input"},
                    "else": {"op": "const", "domain": "numeric", "value": 0},
                },
                "effects": [],
                "max_steps": 3,
            }
        )


def test_piecewise_capability_requires_regression_and_survives_fresh_engine_without_forgetting(
    runtime_repo: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    protected_scope = "heldout/protected-fold-sum"
    protected_candidate = "candidate-protected-fold-sum"
    protected_tool = "acquired.protected.fold.sum"
    protected = synthesize_program(
        input_domain="sequence",
        output_domain="numeric",
        examples=[
            ProgramExample([1, 2], 3),
            ProgramExample([3, 4], 7),
            ProgramExample([-2, 7], 5),
        ],
        max_nodes=5,
    )
    _install(
        runtime_repo,
        candidate_id=protected_candidate,
        tool_id=protected_tool,
        scope=protected_scope,
        program=protected.descriptor(),
    )
    protected_report = _promote(
        runtime_repo,
        tmp_path,
        candidate_id=protected_candidate,
        scope=protected_scope,
        target_task="protected-fold-acquisition",
    )
    assert protected_report["decision"]["adopt_candidate"] is True
    assert AcquiredProgramRegistry(runtime_repo).apply(
        scope=protected_scope,
        tool_id=protected_tool,
        value=[8, -3, 4],
    ) == 9

    demonstrations = [
        ProgramExample(-5, 0),
        ProgramExample(-2, 0),
        ProgramExample(0, 0),
        ProgramExample(3, 3),
        ProgramExample(7, 7),
    ]
    failure = "no bounded pure typed program matches all demonstrations"
    learned = synthesize_program(
        input_domain="numeric",
        output_domain="numeric",
        examples=demonstrations,
        max_nodes=5,
        allow_conditionals=True,
    )
    scope = "heldout/failure-driven-nonnegative-clip"
    candidate_id = "candidate-failure-driven-nonnegative-clip"
    tool_id = "acquired.failure-driven.nonnegative-clip"
    candidate_path = _install(
        runtime_repo,
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=scope,
        program=learned.descriptor(),
        contradictory_evidence=[
            {
                "type": "pre_extension_synthesis_failure",
                "grammar_feature": "conditionals_disabled",
                "error": failure,
                "task": "evaluator-hidden nonnegative clipping",
            }
        ],
    )

    registry = AcquiredProgramRegistry(runtime_repo)
    with pytest.raises(AcquiredProgramToolError, match="not verified for scope"):
        registry.apply(scope=scope, tool_id=tool_id, value=-9)

    report = _promote(
        runtime_repo,
        tmp_path,
        candidate_id=candidate_id,
        scope=scope,
        target_task="withheld-piecewise-numeric",
        protected_task="protected-fold-acquisition",
    )
    assert report["decision"]["adopt_candidate"] is True
    state = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert state["scope_states"][scope] == "VERIFIED_FOR_SCOPE"
    assert state["contradictory_evidence"][0]["grammar_feature"] == "conditionals_disabled"

    monkeypatch.setattr(engine_module, "ModelClient", NoExecuteModelClient)
    fresh = LearningEnabledEngine(runtime_repo)
    run_id = _new_run(fresh)
    hidden = [(-13, 0), (-1, 0), (0, 0), (6, 6), (21, 21)]
    answers = [
        _runtime_call(fresh, run_id, scope=scope, tool_id=tool_id, value=value)["result"]["output"]
        for value, _ in hidden
    ]
    assert answers == [expected for _, expected in hidden]
    protected_after = _runtime_call(
        fresh,
        run_id,
        scope=protected_scope,
        tool_id=protected_tool,
        value=[10, -4, 2],
    )
    assert protected_after["result"]["output"] == 8
    assert fresh.model.calls == []

    descriptor = learned.descriptor()
    assert descriptor["effects"] == ()
    assert execute_program(descriptor, -100) == 0
