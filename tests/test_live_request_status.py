from __future__ import annotations

import json
from typing import Any

import pytest

from agi.live_request_status import (
    STATUS_CONTEXT,
    build_status_payload,
    publish_request_status,
    terminal_state,
)


class _Response:
    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _environment() -> dict[str, str]:
    return {
        "GITHUB_TOKEN": "ephemeral-test-token",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_RUN_ID": "12345",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_API_URL": "https://api.github.com",
        "GITHUB_SERVER_URL": "https://github.com",
    }


def test_terminal_state_fails_closed_for_unknown_status():
    assert terminal_state("success") == "success"
    assert terminal_state("failure") == "failure"
    assert terminal_state("cancelled") == "error"
    assert terminal_state("mystery") == "error"


def test_status_payload_is_bound_to_exact_actions_run():
    payload = build_status_payload(
        state="pending",
        run_id="12345",
        attempt="2",
        server_url="https://github.com",
        repository="owner/repo",
    )

    assert payload == {
        "state": "pending",
        "context": STATUS_CONTEXT,
        "description": "bounded live campaign run 12345 attempt 2 started",
        "target_url": "https://github.com/owner/repo/actions/runs/12345",
    }


def test_publish_request_status_posts_only_status_metadata():
    observed: dict[str, Any] = {}

    def opener(request, *, timeout: int):
        observed["url"] = request.full_url
        observed["timeout"] = timeout
        observed["headers"] = dict(request.header_items())
        observed["body"] = json.loads(request.data.decode("utf-8"))
        return _Response(201)

    payload = publish_request_status("success", environ=_environment(), opener=opener)

    assert observed["url"] == "https://api.github.com/repos/owner/repo/statuses/" + "a" * 40
    assert observed["timeout"] == 30
    assert observed["body"] == payload
    assert payload["context"] == STATUS_CONTEXT
    assert payload["target_url"] == "https://github.com/owner/repo/actions/runs/12345"
    assert "ephemeral-test-token" not in json.dumps(payload)


def test_publish_request_status_rejects_invalid_commit_identity_before_network():
    env = _environment()
    env["GITHUB_SHA"] = "not-a-commit"

    def opener(*args, **kwargs):
        raise AssertionError("network must not be reached")

    with pytest.raises(ValueError, match="GITHUB_SHA"):
        publish_request_status("pending", environ=env, opener=opener)


def test_publish_request_status_fails_closed_on_non_created_response():
    def opener(request, *, timeout: int):
        return _Response(200)

    with pytest.raises(RuntimeError, match="HTTP 200"):
        publish_request_status("failure", environ=_environment(), opener=opener)
