from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import agi.copilot_sdk_client as client_module
from agi.copilot_sdk_client import CopilotProviderBlocked, CopilotResponsesClient


class _FakeSession:
    def __init__(self, content: str | None, error: Exception | None = None):
        self.content = content
        self.error = error
        self.prompts: list[str] = []
        self.disconnected = False

    async def send_and_wait(self, prompt: str):
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(data=SimpleNamespace(content=self.content))

    async def disconnect(self):
        self.disconnected = True


class _FakeClient:
    def __init__(self, options, content: str | None, error: Exception | None = None):
        self.options = options
        self.content = content
        self.error = error
        self.started = False
        self.stopped = False
        self.session_kwargs = None
        self.session = None

    async def start(self):
        self.started = True

    async def create_session(self, **kwargs):
        self.session_kwargs = kwargs
        self.session = _FakeSession(self.content, self.error)
        return self.session

    async def stop(self):
        self.stopped = True


def _factory(content: str | None, error: Exception | None = None):
    clients = []

    # github-copilot-sdk v1 makes CopilotClient.__init__ keyword-only and
    # mode="empty" requires explicit tenant storage. Keep the fake on that
    # same production contract so live-only construction errors cannot hide.
    def factory(*, mode, use_logged_in_user, base_directory):
        client = _FakeClient(
            {
                "mode": mode,
                "use_logged_in_user": use_logged_in_user,
                "base_directory": base_directory,
            },
            content,
            error,
        )
        clients.append(client)
        return client

    return factory, clients


def test_responses_proxy_uses_fresh_tool_free_session_and_cleans_up():
    factory, clients = _factory('{"answer": 4}')
    adapter = CopilotResponsesClient(
        client_factory=factory,
        permission_handler="test-handler",
    )

    response = adapter.responses.create(
        model="gpt-test",
        instructions="Return JSON.",
        input='{"question":"2+2"}',
    )

    assert response.output_text == '{"answer": 4}'
    assert len(clients) == 1
    client = clients[0]
    assert client.options["mode"] == "empty"
    assert client.options["use_logged_in_user"] is False
    assert Path(client.options["base_directory"]).name.startswith("o-copilot-")
    assert not Path(client.options["base_directory"]).exists()
    assert client.started is True
    assert client.stopped is True
    assert client.session_kwargs == {
        "model": "gpt-test",
        "available_tools": [],
        "on_permission_request": "test-handler",
    }
    assert client.session.disconnected is True
    assert "<instructions>\nReturn JSON.\n</instructions>" in client.session.prompts[0]
    assert '<input>\n{"question":"2+2"}\n</input>' in client.session.prompts[0]


def test_empty_copilot_response_fails_closed_after_cleanup():
    factory, clients = _factory("")
    adapter = CopilotResponsesClient(client_factory=factory)

    with pytest.raises(RuntimeError, match="empty or non-text"):
        adapter.responses.create(model="gpt-test", instructions="x", input="{}")

    assert clients[0].session.disconnected is True
    assert clients[0].stopped is True
    assert not Path(clients[0].options["base_directory"]).exists()


def test_monthly_quota_block_is_stable_not_retried_and_cleans_up():
    raw = RuntimeError(
        "Session error: You have exceeded your monthly quota. request_id=sensitive-runtime-id"
    )
    factory, clients = _factory(None, raw)
    adapter = CopilotResponsesClient(client_factory=factory)

    with pytest.raises(CopilotProviderBlocked) as raised:
        adapter.responses.create(model="gpt-test", instructions="x", input="{}")

    assert raised.value.reason == "monthly_quota_exhausted"
    assert str(raised.value) == "Copilot provider blocked: monthly_quota_exhausted"
    assert "sensitive-runtime-id" not in str(raised.value)
    client = clients[0]
    assert len(client.session.prompts) == 1
    assert client.session.disconnected is True
    assert client.stopped is True
    assert not Path(client.options["base_directory"]).exists()


def test_unknown_provider_error_propagates_without_retry_after_cleanup():
    raw = RuntimeError("unclassified provider failure")
    factory, clients = _factory(None, raw)
    adapter = CopilotResponsesClient(client_factory=factory)

    with pytest.raises(RuntimeError, match="unclassified provider failure") as raised:
        adapter.responses.create(model="gpt-test", instructions="x", input="{}")

    assert raised.value is raw
    client = clients[0]
    assert len(client.session.prompts) == 1
    assert client.session.disconnected is True
    assert client.stopped is True
    assert not Path(client.options["base_directory"]).exists()


def test_default_client_requires_sdk_and_supported_auth_environment(monkeypatch):
    monkeypatch.setattr(client_module, "CopilotClient", lambda **_options: object())
    monkeypatch.setattr(
        client_module,
        "PermissionHandler",
        SimpleNamespace(approve_all=object()),
    )
    for name in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="requires COPILOT_GITHUB_TOKEN"):
        CopilotResponsesClient()


def test_supported_environment_auth_is_forwarded_to_sdk_without_logged_in_user(monkeypatch):
    clients = []

    def factory(**options):
        client = _FakeClient(options, '{"answer": 4}')
        clients.append(client)
        return client

    monkeypatch.setattr(client_module, "CopilotClient", factory)
    monkeypatch.setattr(
        client_module,
        "PermissionHandler",
        SimpleNamespace(approve_all="approve"),
    )
    monkeypatch.setenv("GITHUB_TOKEN", "ephemeral-actions-token")

    adapter = CopilotResponsesClient()
    response = adapter.responses.create(model="gpt-test", instructions="x", input="{}")

    assert response.output_text == '{"answer": 4}'
    options = dict(clients[0].options)
    base_directory = options.pop("base_directory")
    assert options == {
        "mode": "empty",
        "use_logged_in_user": False,
        "github_token": "ephemeral-actions-token",
    }
    assert Path(base_directory).name.startswith("o-copilot-")
    assert not Path(base_directory).exists()
    assert clients[0].stopped is True
