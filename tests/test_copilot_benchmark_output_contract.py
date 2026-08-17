from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agi.benchmark import AgentState, BenchmarkTask
from agi.copilot_cli import CopilotBenchmarkAgent
from agi.openai_agent import OpenAIBenchmarkAgent


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
