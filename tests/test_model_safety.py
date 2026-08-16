from __future__ import annotations

import json
from pathlib import Path

import pytest

from continual.openai_client import ModelClient


def client_without_api(root: Path) -> ModelClient:
    client = object.__new__(ModelClient)
    client.root = root.resolve()
    return client


def test_secret_like_files_cannot_be_read(tmp_path: Path):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=x", encoding="utf-8")
    client = client_without_api(tmp_path)
    with pytest.raises(PermissionError):
        client._tool("read_file", {"path": ".env"})


def test_active_control_file_cannot_be_written_directly(tmp_path: Path):
    (tmp_path / "prompts").mkdir()
    client = client_without_api(tmp_path)
    with pytest.raises(PermissionError):
        client._tool("write_file", {"path": "prompts/root.md", "content": "replace"})


def test_network_command_is_blocked(tmp_path: Path):
    client = client_without_api(tmp_path)
    with pytest.raises(PermissionError):
        client._tool(
            "run_command",
            {"argv": ["git", "push"], "cwd": ".", "timeout_seconds": 1},
        )


def test_local_command_runs_with_structured_result(tmp_path: Path):
    client = client_without_api(tmp_path)
    value = json.loads(
        client._tool(
            "run_command",
            {"argv": ["python", "-c", "print('ok')"], "cwd": ".", "timeout_seconds": 5},
        )
    )
    assert value["returncode"] == 0
    assert value["stdout"].strip() == "ok"
