from __future__ import annotations

from pathlib import Path

import pytest

import continual.engine as engine_module
from agi.parameterized_execution import (
    PARAMETERIZED_EXECUTION_SCOPE,
    run_parameterized_execution_campaign,
)
from continual.learning_engine import LearningEnabledEngine
from continual.parameterized_registry import ParameterizedRegistryError, ParameterizedToolRegistry


class RecordingModelClient:
    def __init__(self, root: Path):
        self.root = root
        self.model = "recording-parameterized-test-model"
        self.calls: list[tuple[str, dict, str | None]] = []

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        self.calls.append((component, payload, prompt_path))
        if component != "root":
            raise AssertionError(f"unexpected model call: {component}")
        return {
            "result": {
                "component": "execute",
                "goal": "use verified parameterized tool",
                "scope": PARAMETERIZED_EXECUTION_SCOPE,
            },
            "local_learn": {"decision": "NO_CHANGE", "candidates": []},
            "fragment": {
                "component": "root",
                "purpose": "test",
                "observations": [],
                "evidence_refs": [],
                "unresolved": [],
            },
        }


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


def test_parameterized_tool_remains_inactive_until_regression_then_runs(tmp_path: Path) -> None:
    report = run_parameterized_execution_campaign(tmp_path, "parameterized-runtime-seed")

    assert report["passed"] is True
    assert report["inactive_before_regression"] is True
    assert report["promotion"]["decision"]["adopt_candidate"] is True
    assert report["promotion"]["candidate_status"] == "active-for-scope"
    assert report["descriptor"]["kind"] == "parameterized"
    assert all(item["success"] for item in report["runtime_results"])
    assert "does not establish arbitrary tool learning" in report["claim_boundary"]

    registry = ParameterizedToolRegistry(tmp_path)
    assert len(registry.descriptors(PARAMETERIZED_EXECUTION_SCOPE)) == 1
    with pytest.raises(ParameterizedRegistryError, match="not verified"):
        registry.apply(scope="agi/wrong-scope", tool_id=report["tool_id"], value="abc")


def test_learning_engine_catalog_exposes_protocol_and_parameterized_descriptor(
    runtime_repo: Path, monkeypatch
) -> None:
    report = run_parameterized_execution_campaign(runtime_repo, "parameterized-catalog-seed")
    engine = _engine(runtime_repo, monkeypatch)
    run_id = _new_run(engine)

    engine._invoke(run_id, "root", {"snapshot": {"revision": 0}})

    root_payload = next(payload for component, payload, _ in engine.model.calls if component == "root")
    descriptors = root_payload["verified_learned_tools"]
    selected = [item for item in descriptors if item["tool_id"] == report["tool_id"]]
    assert len(selected) == 1
    assert selected[0]["kind"] == "parameterized"
    protocol = root_payload["verified_learned_tool_protocol"]
    assert protocol["call_field"] == "learned_tool_call"
    assert protocol["required_execution_unit"]["scope"].startswith("must exactly equal")


def test_learning_engine_executes_parameterized_tool_mechanically(
    runtime_repo: Path, monkeypatch
) -> None:
    report = run_parameterized_execution_campaign(runtime_repo, "parameterized-engine-seed")
    engine = _engine(runtime_repo, monkeypatch)
    run_id = _new_run(engine)
    heldout = report["runtime_results"][0]
    payload = {
        "snapshot": {"revision": 0},
        "execution_unit": {
            "goal": "execute acquired parameterized transformation",
            "scope": PARAMETERIZED_EXECUTION_SCOPE,
            "learned_tool_call": {
                "tool_id": report["tool_id"],
                "input": heldout["query"],
            },
        },
    }

    first = engine._invoke(run_id, "execute", payload)
    second = engine._invoke(run_id, "execute", payload)

    assert first == second
    assert first["result"]["execution_kind"] == "verified_learned_tool"
    assert first["result"]["tool_kind"] == "parameterized"
    assert first["result"]["output"] == heldout["expected"]
    assert [component for component, _, _ in engine.model.calls if component == "execute"] == []
    events = (engine.store.run_dir(run_id) / "events.jsonl").read_text(encoding="utf-8")
    assert "learned_tool_invocation_completed" in events
    assert "learned_tool_invocation_reused" in events
