from __future__ import annotations

from pathlib import Path

import pytest

import continual.openai_client as client_module


class FakeOpenAI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_openai_key_configures_continual_engine(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(client_module, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test")
    monkeypatch.setenv("GITHUB_TOKEN", "github-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    client = client_module.ModelClient(tmp_path)
    assert client.provider == "openai"
    assert client.model == "gpt-test"
    assert client.client.kwargs == {"api_key": "openai-test"}


def test_github_token_does_not_route_to_retired_github_models(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(client_module, "OpenAI", FakeOpenAI)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "ephemeral-actions-token")
    with pytest.raises(RuntimeError, match="constrained Copilot CLI continuity backend"):
        client_module.ModelClient(tmp_path)


def test_no_openai_credentials_fails_closed(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(client_module, "OpenAI", FakeOpenAI)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is required"):
        client_module.ModelClient(tmp_path)
