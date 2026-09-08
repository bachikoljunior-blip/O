from __future__ import annotations

from pathlib import Path

import pytest

import continual.openai_client as client_module


class FakeOpenAI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_openai_key_remains_preferred(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(client_module, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test")
    monkeypatch.setenv("GITHUB_TOKEN", "github-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    client = client_module.ModelClient(tmp_path)
    assert client.provider == "openai"
    assert client.model == "gpt-test"
    assert client.client.kwargs == {"api_key": "openai-test"}


def test_github_models_is_automatic_fallback(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(client_module, "OpenAI", FakeOpenAI)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "ephemeral-actions-token")
    monkeypatch.setenv("GITHUB_MODELS_MODEL", "openai/gpt-4.1")
    client = client_module.ModelClient(tmp_path)
    assert client.provider == "github-models"
    assert client.model == "openai/gpt-4.1"
    assert client.client.kwargs["api_key"] == "ephemeral-actions-token"
    assert client.client.kwargs["base_url"] == "https://models.github.ai/inference"


def test_no_available_model_credentials_fails_closed(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(client_module, "OpenAI", FakeOpenAI)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY or GITHUB_TOKEN"):
        client_module.ModelClient(tmp_path)
