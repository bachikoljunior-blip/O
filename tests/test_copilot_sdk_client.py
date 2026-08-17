from __future__ import annotations

from types import SimpleNamespace

import pytest

import agi.copilot_sdk_client as client_module
from agi.copilot_sdk_client import CopilotResponsesClient


class _FakeSession:
    def __init__(self, content: str | None):
        self.content = content
        self.prompts: list[str] = []
        self.disconnected = False

    async def send_and_wait(self, prompt: str):
        self.prompts.append(prompt)
        return SimpleNamespace(data=SimpleNamespace(content=self.content))

    async def disconnect(self):
        self.disconnected = True


class _FakeClient:
    def __init__(self, options, content: str | None):
        self.options = options
        self.content = content
        self.started = False
        self.stopped = False
        self.session_kwargs = None
        self.session = None

    async def start(self):
        self.started = True

    async def create_session(self, **kwargs):
        self.session_kwargs = kwargs
        self.session = _FakeSession(self.content)
        return self.session

    async def stop(self):
        self.stopped = True


def _factory(content: str | None):
    clients = []

    # github-copilot-sdk v1 makes CopilotClient.__init__ keyword-only. Keeping
    # the fake on that same contract prevents a positional production call from
    # passing tests while failing immediately in a live campaign.
    def factory(*, mode, use_logged_in_user):
        client = _FakeClient(
            {"mode": mode, "use_logged_in_user": use_logged_in_user},
            content,
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
    assert client.options == {"mode": "empty", "use_logged_in_user": False}
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


def test_supported_environment_auth_allows_default_factory(monkeypatch):
    captured = []

    class Factory:
        def __init__(self, **options):
            captured.append(options)

    monkeypatch.setattr(client_module, "CopilotClient", Factory)
    monkeypatch.setattr(
        client_module,
        "PermissionHandler",
        SimpleNamespace(approve_all="approve"),
    )
    monkeypatch.setenv("GITHUB_TOKEN", "ephemeral-actions-token")

    adapter = CopilotResponsesClient()

    assert adapter._permission_handler == "approve"
    assert adapter._client_factory is Factory
    assert captured == []
