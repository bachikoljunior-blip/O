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
        self.model = "no-execute-test-model"
        self.calls: list[tuple[str, dict, str | None]] = []

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        self.calls.append((component, payload, prompt_path))
        raise AssertionError(f"model should not execute acquired program: {component}")


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _measurement(task: str, repeat: int, score: float, *, domain: str = "program") -> dict:
    return {
        "task_id": task,
        "criterion": "transfer",
        "domain": domain,
        "repeat_index": repeat,
        "passed": score == 1.0,
        "score": score,
        "artifact_sha256": f"{repeat + 17:02x}" * 32,
    }


def _promote(
    root: Path,
    *,
    candidate_id: str,
    scope: str,
    tmp_path: Path,
) -> dict:
    baseline = tmp_path / f"{candidate_id}-baseline.json"
    candidate = tmp_path / f"{candidate_id}-candidate.json"
    _write(
        baseline,
        [
            _measurement("target", 0, 0.0),
            _measurement("target", 1, 0.0),
            _measurement("protected", 0, 1.0, domain="protected"),
        ],
    )
    _write(
        candidate,
        [
            _measurement("target", 0, 1.0),
            _measurement("target", 1, 1.0),
            _measurement("protected", 0, 1.0, domain="protected"),
        ],
    )
    return record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline,
        candidate_path=candidate,
        target_task_ids=["target"],
    )


def _install_candidate(
    root: Path,
    *,
    candidate_id: str,
    tool_id: str,
    scope: str,
    program: dict,
) -> Path:
    payload = acquired_program_candidate_payload(
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=scope,
        program=program,
        description="Synthesized pure typed expression learned from demonstrations.",
    )
    path = root / ".continual" / "candidates" / candidate_id / "candidate.json"
    _write(path, payload)
    return path


def _new_run(engine: LearningEnabledEngine) -> str:
    run_id = engine.store.new_id("run")
    run_dir = engine.store.run_dir(run_id)
    run_dir.mkdir(parents=True)
    engine.store.atomic_json(
        run_dir / "snapshot.json",
        {"revision": 0, "status": "continue", "phase": "unit_pending"},
    )
    return run_id


def test_synthesizes_unlisted_cross_domain_programs_and_transfers_to_heldout_inputs() -> None:
    string_program = synthesize_program(
        input_domain="string",
        output_domain="numeric",
        examples=[
            ProgramExample("a", 3),
            ProgramExample("abcd", 6),
            ProgramExample("abcdef", 8),
        ],
        max_nodes=6,
    )
    sequence_program = synthesize_program(
        input_domain="sequence",
        output_domain="numeric",
        examples=[
            ProgramExample([1], 4),
            ProgramExample([1, 2], 7),
            ProgramExample([1, 2, 3, 4], 13),
        ],
        max_nodes=7,
    )
    numeric_to_string = synthesize_program(
        input_domain="numeric",
        output_domain="string",
        examples=[
            ProgramExample(1, "3"),
            ProgramExample(4, "6"),
            ProgramExample(8, "10"),
        ],
        max_nodes=7,
    )

    assert string_program.apply("OpenAI") == 8
    assert sequence_program.apply([9, 8, 7]) == 10
    assert numeric_to_string.apply(11) == "13"
    assert string_program.expression["op"] != "input"
    assert sequence_program.expression["op"] != "input"
    assert numeric_to_string.output_domain == "string"


def test_acquired_program_descriptor_rejects_effects_unknown_ops_and_resource_escape() -> None:
    with pytest.raises(AcquiredProgramError, match="no side effects"):
        validate_program_descriptor(
            {
                "input_domain": "numeric",
                "output_domain": "numeric",
                "expression": {"op": "input"},
                "effects": ["network"],
            }
        )

    with pytest.raises(AcquiredProgramError, match="unknown acquired-program op"):
        validate_program_descriptor(
            {
                "input_domain": "numeric",
                "output_domain": "numeric",
                "expression": {"op": "shell", "argv": ["echo", "unsafe"]},
                "effects": [],
            }
        )

    descriptor = {
        "input_domain": "string",
        "output_domain": "string",
        "expression": {
            "op": "concat_string",
            "left": {"op": "input"},
            "right": {"op": "input"},
        },
        "effects": [],
        "max_steps": 8,
        "max_output_length": 4,
    }
    with pytest.raises(AcquiredProgramError, match="output exceeds runtime bound"):
        execute_program(descriptor, "abc")


def test_acquired_program_is_inactive_until_exact_scope_regression_and_tampering_fails_closed(
    runtime_repo: Path,
    tmp_path: Path,
) -> None:
    scope = "heldout/acquired-string-length"
    candidate_id = "candidate-acquired-length"
    tool_id = "acquired.string.length-plus-two"
    learned = synthesize_program(
        input_domain="string",
        output_domain="numeric",
        examples=[
            ProgramExample("aa", 4),
            ProgramExample("abcde", 7),
            ProgramExample("abcdefgh", 10),
        ],
        max_nodes=6,
    )
    candidate_path = _install_candidate(
        runtime_repo,
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=scope,
        program=learned.descriptor(),
    )
    registry = AcquiredProgramRegistry(runtime_repo)
    assert registry.descriptors(scope) == ()

    promotion = _promote(
        runtime_repo,
        candidate_id=candidate_id,
        scope=scope,
        tmp_path=tmp_path,
    )
    assert promotion["decision"]["adopt_candidate"] is True
    descriptor = registry.descriptors(scope)[0]
    assert descriptor["program_kind"] == "acquired_program"
    assert descriptor["input_domain"] == "string"
    assert descriptor["output_domain"] == "numeric"
    assert descriptor["effects"] == []
    assert registry.apply(scope=scope, tool_id=tool_id, value="heldout") == 9

    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate["acquired_program"]["program"]["expression"] = {
        "op": "length_string",
        "arg": {"op": "input"},
    }
    candidate_path.write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(AcquiredProgramToolError, match="changed after regression verification"):
        AcquiredProgramRegistry(runtime_repo).descriptors(scope)


def test_verified_acquired_program_executes_mechanically_in_continual_engine(
    runtime_repo: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    scope = "heldout/acquired-sequence-count"
    candidate_id = "candidate-acquired-sequence"
    tool_id = "acquired.sequence.length-times-three-plus-one"
    learned = synthesize_program(
        input_domain="sequence",
        output_domain="numeric",
        examples=[
            ProgramExample([1], 4),
            ProgramExample([1, 2], 7),
            ProgramExample([1, 2, 3, 4], 13),
        ],
        max_nodes=7,
    )
    _install_candidate(
        runtime_repo,
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=scope,
        program=learned.descriptor(),
    )
    _promote(runtime_repo, candidate_id=candidate_id, scope=scope, tmp_path=tmp_path)

    monkeypatch.setattr(engine_module, "ModelClient", NoExecuteModelClient)
    engine = LearningEnabledEngine(runtime_repo)
    run_id = _new_run(engine)
    payload = {
        "snapshot": {"revision": 0},
        "execution_unit": {
            "goal": "apply verified acquired program to unseen input",
            "scope": scope,
            "learned_tool_call": {
                "tool_id": tool_id,
                "input": [9, 8, 7, 6, 5],
            },
        },
    }

    first = engine._invoke(run_id, "execute", payload)
    second = engine._invoke(run_id, "execute", payload)

    assert first == second
    assert first["result"]["program_kind"] == "acquired_program"
    assert first["result"]["output"] == 16
    assert engine.model.calls == []
    events = (engine.store.run_dir(run_id) / "events.jsonl").read_text(encoding="utf-8")
    assert "learned_tool_invocation_completed" in events
    assert "learned_tool_invocation_reused" in events
