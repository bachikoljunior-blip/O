from __future__ import annotations

import json
from pathlib import Path

import pytest

import continual.engine as engine_module
from agi.typed_execution_learning import (
    TYPED_EXECUTION_SCOPE,
    run_typed_learned_tool_execution_campaign,
)
from continual.learned_tools import LearnedToolError, LearnedToolRegistry
from continual.learning_engine import LearningEnabledEngine


class RecordingModelClient:
    def __init__(self, root: Path):
        self.root = root
        self.model = "recording-test-model"
        self.calls: list[tuple[str, dict, str | None]] = []

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        self.calls.append((component, payload, prompt_path))
        local = {"decision": "NO_CHANGE", "candidates": []}
        fragment = {
            "component": component,
            "purpose": "test",
            "observations": [],
            "evidence_refs": [],
            "unresolved": [],
        }
        if component == "root":
            return {
                "result": {
                    "component": "execute",
                    "goal": "use a verified typed learned tool when useful",
                    "scope": TYPED_EXECUTION_SCOPE,
                },
                "local_learn": local,
                "fragment": fragment,
            }
        if component == "execute":
            return {
                "result": {"status": "implemented", "kind": "model-fallback"},
                "local_learn": local,
                "fragment": fragment,
            }
        raise AssertionError(f"unexpected model call: {component}")


def _engine(runtime_repo: Path, monkeypatch) -> LearningEnabledEngine:
    monkeypatch.setattr(engine_module, "ModelClient", RecordingModelClient)
    return LearningEnabledEngine(runtime_repo)


def _new_run(engine: LearningEnabledEngine) -> str:
    run_id = engine.store.new_id("run")
    run_dir = engine.store.run_dir(run_id)
    run_dir.mkdir(parents=True)
    engine.store.atomic_json(
        run_dir / "snapshot.json",
        {"revision": 0, "status": "continue", "phase": "unit_pending"},
    )
    return run_id


def test_typed_tool_is_learned_promoted_and_executed_cross_domain(runtime_repo: Path) -> None:
    report = run_typed_learned_tool_execution_campaign(runtime_repo, "typed-execution-seed")

    assert report["passed"] is True
    assert report["pre_learning_solved"] is False
    assert report["inactive_before_regression"] is True
    assert report["expanded_depth"] == 5
    assert report["promotion"]["decision"]["adopt_candidate"] is True
    assert report["rejected_wrong_type"] is True
    assert all(item["success"] for item in report["runtime_results"])
    assert all(item["output_is_numeric"] for item in report["runtime_results"])
    descriptor = report["available_tool"]
    assert descriptor["program_kind"] == "typed"
    assert descriptor["input_domain"] == "string"
    assert descriptor["output_domain"] == "numeric"
    assert descriptor["program_length"] == 5
    assert "not open-ended learning or AGI evidence" in report["claim_boundary"]


def test_typed_tool_executes_mechanically_in_learning_engine(
    runtime_repo: Path, monkeypatch
) -> None:
    report = run_typed_learned_tool_execution_campaign(runtime_repo, "typed-engine-seed")
    engine = _engine(runtime_repo, monkeypatch)
    run_id = _new_run(engine)
    expected = next(
        item["expected"] for item in report["runtime_results"] if item["query"] == "Bookkeeper"
    )
    payload = {
        "snapshot": {"revision": 0},
        "execution_unit": {
            "goal": "apply acquired typed transform",
            "scope": TYPED_EXECUTION_SCOPE,
            "learned_tool_call": {
                "tool_id": report["tool_id"],
                "input": "Bookkeeper",
            },
        },
    }

    first = engine._invoke(run_id, "execute", payload)
    second = engine._invoke(run_id, "execute", payload)

    assert first == second
    assert first["result"]["execution_kind"] == "verified_learned_tool"
    assert first["result"]["output"] == expected
    assert isinstance(first["result"]["output"], int)
    assert [component for component, _, _ in engine.model.calls if component == "execute"] == []
    events = (engine.store.run_dir(run_id) / "events.jsonl").read_text(encoding="utf-8")
    assert "learned_tool_invocation_completed" in events
    assert "learned_tool_invocation_reused" in events


def test_typed_registry_fails_closed_on_wrong_input_and_ill_typed_tampering(
    runtime_repo: Path,
) -> None:
    report = run_typed_learned_tool_execution_campaign(runtime_repo, "typed-tamper-seed")
    registry = LearnedToolRegistry(runtime_repo)

    with pytest.raises(LearnedToolError, match="expected string input"):
        registry.apply(
            scope=TYPED_EXECUTION_SCOPE,
            tool_id=report["tool_id"],
            value=["not", "a", "string"],
        )

    candidate_path = (
        runtime_repo
        / ".continual"
        / "candidates"
        / report["candidate_id"]
        / "candidate.json"
    )
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate["learned_tool"]["expanded_primitives"] = [
        "string.reverse",
        "numeric.plus3",
    ]
    candidate_path.write_text(
        json.dumps(candidate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(LearnedToolError, match="ill-typed primitive chain"):
        LearnedToolRegistry(runtime_repo).available(TYPED_EXECUTION_SCOPE)
