from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agi.benchmark import AgentState, BenchmarkTask
from agi.openai_agent import OpenAIBenchmarkAgent


class _FakeResponses:
    def __init__(self, output: dict[str, object]):
        self.output = output
        self.calls: list[dict[str, object]] = []

    def create(self, *, model: str, instructions: str, input: str):
        self.calls.append(
            {
                "model": model,
                "instructions": instructions,
                "input": input,
            }
        )
        return SimpleNamespace(output_text=json.dumps(self.output))


class _FakeClient:
    def __init__(self, output: dict[str, object]):
        self.responses = _FakeResponses(output)


def _task() -> BenchmarkTask:
    return BenchmarkTask(
        task_id="contract-test",
        criterion="breadth",
        domain="test",
        instruction="Return the supplied value.",
        input={"value": 1},
        expected=1,
        evaluator="exact",
    )


def test_benchmark_prompt_explicitly_requires_object_updates_and_empty_objects():
    client = _FakeClient(
        {
            "answer": 1,
            "evidence": [],
            "state_update": {},
            "procedure_update": {},
        }
    )
    agent = OpenAIBenchmarkAgent(model="test-model", client=client)

    answer = agent.solve(_task(), AgentState())

    assert answer.state_update == {}
    assert answer.procedure_update == {}
    instructions = str(client.responses.calls[0]["instructions"])
    assert "state_update` and `procedure_update` must always be JSON objects" in instructions
    assert "use {} when there is no" in instructions


def test_non_object_state_or_procedure_update_remains_fail_closed():
    client = _FakeClient(
        {
            "answer": 1,
            "evidence": [],
            "state_update": None,
            "procedure_update": {},
        }
    )
    agent = OpenAIBenchmarkAgent(model="test-model", client=client)

    with pytest.raises(ValueError, match="state_update and procedure_update must be objects"):
        agent.solve(_task(), AgentState())
