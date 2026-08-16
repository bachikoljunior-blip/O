from __future__ import annotations

from pathlib import Path

import pytest

import continual.engine as engine_module
from agi.execution_learning import EXECUTION_SCOPE, run_learned_tool_execution_campaign
from continual.learned_tools import LearnedToolError
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
                    "goal": "use a verified learned tool when useful",
                    "scope": EXECUTION_SCOPE,
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


def test_root_receives_only_verified_learned_tool_descriptors(runtime_repo: Path, monkeypatch) -> None:
    report = run_learned_tool_execution_campaign(runtime_repo, "root-catalog-seed")
    engine = _engine(runtime_repo, monkeypatch)
    run_id = _new_run(engine)

    output = engine._invoke(run_id, "root", {"snapshot": {"revision": 0}})

    assert output["result"]["component"] == "execute"
    root_calls = [payload for component, payload, _ in engine.model.calls if component == "root"]
    assert len(root_calls) == 1
    catalog = root_calls[0]["verified_learned_tools"]
    assert len(catalog) == 1
    assert catalog[0]["tool_id"] == report["tool_id"]
    assert catalog[0]["scope"] == EXECUTION_SCOPE
    assert "expanded_primitives" not in catalog[0]


def test_verified_tool_call_executes_mechanically_and_is_journal_reused(
    runtime_repo: Path, monkeypatch
) -> None:
    report = run_learned_tool_execution_campaign(runtime_repo, "engine-execution-seed")
    engine = _engine(runtime_repo, monkeypatch)
    run_id = _new_run(engine)
    unit = {
        "goal": "apply acquired transform",
        "scope": EXECUTION_SCOPE,
        "learned_tool_call": {
            "tool_id": report["tool_id"],
            "input": "FreshInput",
        },
    }
    payload = {"snapshot": {"revision": 0}, "execution_unit": unit}

    first = engine._invoke(run_id, "execute", payload)
    second = engine._invoke(run_id, "execute", payload)

    assert first == second
    assert first["result"]["execution_kind"] == "verified_learned_tool"
    assert first["result"]["output"] == "tupnIhserF#tupnIhserF#"
    assert [component for component, _, _ in engine.model.calls if component == "execute"] == []
    invocations = list((engine.store.run_dir(run_id) / "invocations").glob("invoke-learned-tool-*.json"))
    assert len(invocations) == 1
    events = (engine.store.run_dir(run_id) / "events.jsonl").read_text(encoding="utf-8")
    assert "learned_tool_invocation_completed" in events
    assert "learned_tool_invocation_reused" in events


def test_wrong_scope_cannot_call_verified_tool(runtime_repo: Path, monkeypatch) -> None:
    report = run_learned_tool_execution_campaign(runtime_repo, "wrong-scope-seed")
    engine = _engine(runtime_repo, monkeypatch)
    run_id = _new_run(engine)

    with pytest.raises(LearnedToolError, match="not verified"):
        engine._invoke(
            run_id,
            "execute",
            {
                "snapshot": {"revision": 0},
                "execution_unit": {
                    "goal": "attempt wrong scope",
                    "scope": "agi/other-scope",
                    "learned_tool_call": {
                        "tool_id": report["tool_id"],
                        "input": "abc",
                    },
                },
            },
        )


def test_explicit_call_scope_must_match_execution_unit(runtime_repo: Path, monkeypatch) -> None:
    report = run_learned_tool_execution_campaign(runtime_repo, "mismatch-seed")
    engine = _engine(runtime_repo, monkeypatch)
    run_id = _new_run(engine)

    with pytest.raises(LearnedToolError, match="exactly match"):
        engine._invoke(
            run_id,
            "execute",
            {
                "snapshot": {"revision": 0},
                "execution_unit": {
                    "goal": "attempt scope mismatch",
                    "scope": EXECUTION_SCOPE,
                    "learned_tool_call": {
                        "scope": "agi/other-scope",
                        "tool_id": report["tool_id"],
                        "input": "abc",
                    },
                },
            },
        )
