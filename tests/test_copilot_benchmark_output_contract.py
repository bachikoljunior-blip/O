from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agi.benchmark import AgentState, BenchmarkTask
from agi.copilot_cli import CopilotBenchmarkAgent, CopilotWorkspaceAgent
from agi.openai_agent import OpenAIBenchmarkAgent, OpenAIWorkspaceAgent


class _Responses:
    def __init__(self, value):
        self.value = value

    def create(self, **_kwargs):
        return SimpleNamespace(output_text=json.dumps(self.value))


class _Client:
    def __init__(self, value):
        self.responses = _Responses(value)


def _task() -> BenchmarkTask:
    return BenchmarkTask(
        task_id="contract-test",
        criterion="breadth",
        domain="test",
        instruction="Return one.",
        input={},
        expected=1,
        evaluator="exact",
    )


def test_copilot_benchmark_instructions_make_persistent_update_contract_explicit(monkeypatch):
    captured = {}

    def fake_respond(self, *, instructions, payload):
        captured["instructions"] = instructions
        captured["payload"] = payload
        return {
            "answer": 1,
            "evidence": [],
            "state_update": {},
            "procedure_update": {},
        }

    monkeypatch.setattr(OpenAIBenchmarkAgent, "_respond", fake_respond)
    agent = object.__new__(CopilotBenchmarkAgent)
    value = agent._respond(instructions="base benchmark instructions", payload={"x": 1})

    assert value["answer"] == 1
    assert captured["payload"] == {"x": 1}
    assert "state_update" in captured["instructions"]
    assert "procedure_update" in captured["instructions"]
    assert "JSON objects" in captured["instructions"]
    assert "return {}" in captured["instructions"]
    assert "direct task result" in captured["instructions"]
    assert "evidence" in captured["instructions"]


def test_copilot_benchmark_retries_one_malformed_json_response_without_relaxing_parser(monkeypatch):
    calls = []

    def fake_respond(self, *, instructions, payload):
        calls.append((instructions, payload))
        if len(calls) == 1:
            raise json.JSONDecodeError("Expecting value", "not json", 0)
        return {
            "answer": 1,
            "evidence": [],
            "state_update": {},
            "procedure_update": {},
        }

    monkeypatch.setattr(OpenAIBenchmarkAgent, "_respond", fake_respond)
    agent = object.__new__(CopilotBenchmarkAgent)
    payload = {"task": {"task_id": "x"}, "state": {}}

    value = agent._respond(instructions="base benchmark instructions", payload=payload)

    assert value["answer"] == 1
    assert len(calls) == 2
    assert calls[0][1] is payload
    assert calls[1][1] is payload
    assert "direct task result" in calls[0][0]
    assert "preceding model response" in calls[1][0]
    assert "same benchmark input" in calls[1][0]
    assert "no prose or markdown" in calls[1][0]


def test_copilot_benchmark_keeps_second_malformed_response_fail_closed(monkeypatch):
    calls = []

    def fake_respond(self, *, instructions, payload):
        calls.append((instructions, payload))
        raise json.JSONDecodeError("Expecting value", "still not json", 0)

    monkeypatch.setattr(OpenAIBenchmarkAgent, "_respond", fake_respond)
    agent = object.__new__(CopilotBenchmarkAgent)

    with pytest.raises(json.JSONDecodeError):
        agent._respond(instructions="base", payload={"task": {}})

    assert len(calls) == 2


def test_copilot_workspace_retries_one_malformed_json_response_without_relaxing_parser(monkeypatch):
    calls = []

    def fake_respond(self, *, instructions, payload):
        calls.append((instructions, payload))
        if len(calls) == 1:
            raise json.JSONDecodeError("Expecting value", "done", 0)
        return {"kind": "final", "answer": "done"}

    monkeypatch.setattr(OpenAIWorkspaceAgent, "_respond", fake_respond)
    agent = object.__new__(CopilotWorkspaceAgent)
    payload = {"latest_observation": {"status": "ok"}, "trace": []}

    value = agent._respond(instructions="base workspace instructions", payload=payload)

    assert value == {"kind": "final", "answer": "done"}
    assert len(calls) == 2
    assert calls[0][1] is payload
    assert calls[1][1] is payload
    assert "exactly one JSON object" in calls[0][0]
    assert "preceding model response" in calls[1][0]
    assert "same workspace observation" in calls[1][0]


def test_copilot_workspace_keeps_second_malformed_response_fail_closed(monkeypatch):
    calls = []

    def fake_respond(self, *, instructions, payload):
        calls.append((instructions, payload))
        raise json.JSONDecodeError("Expecting value", "still not json", 0)

    monkeypatch.setattr(OpenAIWorkspaceAgent, "_respond", fake_respond)
    agent = object.__new__(CopilotWorkspaceAgent)

    with pytest.raises(json.JSONDecodeError):
        agent._respond(instructions="base", payload={"trace": []})

    assert len(calls) == 2


@pytest.mark.parametrize(
    ("state_update", "procedure_update"),
    [
        ([], {}),
        ({}, []),
        (None, {}),
        ({}, None),
        ("no change", {}),
        ({}, "no change"),
    ],
)
def test_benchmark_agent_still_rejects_non_object_persistent_updates(
    state_update, procedure_update
):
    agent = OpenAIBenchmarkAgent(
        model="test-model",
        client=_Client(
            {
                "answer": 1,
                "evidence": [],
                "state_update": state_update,
                "procedure_update": procedure_update,
            }
        ),
    )

    with pytest.raises(ValueError, match="state_update and procedure_update must be objects"):
        agent.solve(_task(), AgentState())


def test_benchmark_agent_accepts_empty_object_for_no_persistent_update():
    agent = OpenAIBenchmarkAgent(
        model="test-model",
        client=_Client(
            {
                "answer": 1,
                "evidence": [],
                "state_update": {},
                "procedure_update": {},
            }
        ),
    )

    answer = agent.solve(_task(), AgentState())
    assert answer.state_update == {}
    assert answer.procedure_update == {}
