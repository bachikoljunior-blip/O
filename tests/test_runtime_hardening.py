from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from continual.engine import Engine
from continual.openai_client import ModelClient
from continual.store import Store


def _client_without_api(root: Path) -> ModelClient:
    client = object.__new__(ModelClient)
    client.root = root.resolve()
    client.model = "test-model"
    client.client = None
    return client


def _engine_without_api(root: Path, model) -> Engine:
    engine = object.__new__(Engine)
    engine.root = root.resolve()
    engine.store = Store(engine.root)
    engine.model = model
    return engine


def test_active_prompt_is_used_and_candidate_is_an_overlay(tmp_path: Path) -> None:
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "execute.md").write_text("DEFAULT", encoding="utf-8")
    (tmp_path / "prompts" / "active-execute.md").write_text(
        "ACTIVE CONTRACT", encoding="utf-8"
    )
    (tmp_path / "candidate.md").write_text("LEARNED HINT", encoding="utf-8")
    config = tmp_path / ".continual" / "system" / "active-components.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps({"execute": "prompts/active-execute.md"}),
        encoding="utf-8",
    )

    client = _client_without_api(tmp_path)
    prompt = client.prompt("execute", "candidate.md")

    assert prompt.startswith("ACTIVE CONTRACT")
    assert "SELECTED CANDIDATE OVERLAY" in prompt
    assert prompt.rstrip().endswith("LEARNED HINT")


def test_model_cannot_write_runtime_prompts_or_secret_files(tmp_path: Path) -> None:
    client = _client_without_api(tmp_path)

    with pytest.raises(PermissionError):
        client._tool("write_file", {"path": "prompts/execute.md", "content": "x"})
    with pytest.raises(PermissionError):
        client._tool("write_file", {"path": ".continual/runs/x.json", "content": "x"})
    with pytest.raises(PermissionError):
        client._tool("write_file", {"path": ".env", "content": "x"})

    result = json.loads(
        client._tool("write_file", {"path": "artifacts/result.txt", "content": "ok"})
    )
    assert result["ok"] is True
    assert (tmp_path / "artifacts" / "result.txt").read_text(encoding="utf-8") == "ok"


def test_command_environment_strips_secret_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client_without_api(tmp_path)
    captured = {}

    def fake_run(argv, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("NORMAL_TEST_VALUE", "visible")
    monkeypatch.setattr("continual.openai_client.subprocess.run", fake_run)

    client._tool(
        "run_command",
        {"argv": ["python", "-V"], "cwd": ".", "timeout_seconds": 5},
    )

    assert "OPENAI_API_KEY" not in captured["env"]
    assert captured["env"]["NORMAL_TEST_VALUE"] == "visible"
    assert captured["env"]["CONTINUAL_TOOL_SANDBOX"] == "1"


class CaptureModel:
    model = "capture-model"

    def __init__(self):
        self.calls = []

    def call(self, component, payload, prompt_path=None):
        self.calls.append(
            {"component": component, "payload": payload, "prompt_path": prompt_path}
        )
        return {
            "result": {"decision": "USE_ACTIVE"},
            "fragment": {"component": component},
            "local_learn": {},
        }


def test_preflight_only_receives_candidates_for_the_target_component(
    tmp_path: Path,
) -> None:
    model = CaptureModel()
    engine = _engine_without_api(tmp_path, model)
    engine.store.atomic_json(
        engine.candidate_index_path,
        {
            "schema_version": 2,
            "candidates": [
                {"candidate_id": "execute-candidate", "target_component": "execute"},
                {"candidate_id": "root-candidate", "target_component": "root"},
                {"candidate_id": "global-candidate", "target_component": "*"},
            ],
        },
    )

    engine._preflight(
        "run-test",
        "execute",
        {"task": "test"},
        invocation_id="execute-1",
    )

    candidates = model.calls[0]["payload"]["candidate_index"]["candidates"]
    assert {item["candidate_id"] for item in candidates} == {
        "execute-candidate",
        "global-candidate",
    }


def test_completed_invocation_is_reused_after_resume(tmp_path: Path) -> None:
    model = CaptureModel()
    engine = _engine_without_api(tmp_path, model)

    first = engine._invoke(
        "run-test",
        "execute",
        {"task": "idempotency"},
        invocation_id="unit-fixed",
        preflight=False,
    )
    second = engine._invoke(
        "run-test",
        "execute",
        {"task": "idempotency"},
        invocation_id="unit-fixed",
        preflight=False,
    )

    assert first == second
    assert [call["component"] for call in model.calls] == ["execute"]


def test_step_budget_pauses_without_marking_the_run_failed(tmp_path: Path) -> None:
    model = CaptureModel()
    engine = _engine_without_api(tmp_path, model)
    run_id = "run-budget"
    run_directory = engine.store.run_dir(run_id)
    engine.store.atomic_text(run_directory / "request.md", "do one step")
    engine.store.atomic_json(
        run_directory / "snapshot.json",
        {
            "schema_version": 2,
            "run_id": run_id,
            "status": "continue",
            "phase": "entry_pending",
            "revision": 0,
            "environment": {},
        },
    )

    status = engine.resume(run_id, max_steps=1)

    assert status == "continue"
    snapshot = engine.store.snapshot(run_id)
    assert snapshot["status"] == "continue"
    assert snapshot["phase"] == "root_pending"
