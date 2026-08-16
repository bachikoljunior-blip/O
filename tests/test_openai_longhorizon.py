from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agi.longhorizon import LongHorizonObservation
from agi.openai_agent import OpenAILongHorizonAgent
from agi.sandbox_protocol import SandboxProtocolInstance, run_sandbox_protocol


class _RuleDrivenResponses:
    def __init__(self):
        self.requests: list[dict] = []

    def create(self, *, model, instructions, input):
        payload = json.loads(input)
        self.requests.append({"model": model, "instructions": instructions, "payload": payload})
        observation = payload["observation"]
        phase = observation["phase"]
        task_instruction = observation["task_instruction"]
        if observation["failure"]:
            value = {"kind": "rollback", "payload": {}}
        elif phase == "learn":
            value = {"kind": "remember", "payload": task_instruction["remember"]}
        elif phase == "checkpoint":
            value = {"kind": "checkpoint", "payload": {}}
        elif phase == "intervene":
            value = {"kind": "write", "payload": task_instruction["write"]}
        elif phase == "retain":
            key = task_instruction["recall_key"]
            value = {
                "kind": "recall",
                "payload": {"key": key, "value": observation["durable_memory"].get(key)},
            }
        elif phase == "regression":
            value = {
                "kind": "verify_protected",
                "payload": {"path": task_instruction["verify_protected_path"]},
            }
        else:
            value = {"kind": "finish", "payload": {}}
        return SimpleNamespace(output_text=json.dumps(value))


class _Client:
    def __init__(self, responses):
        self.responses = responses


def _observation() -> LongHorizonObservation:
    return LongHorizonObservation(
        status="ready",
        phase="learn",
        workspace={},
        durable_memory={},
        checkpoint_digest=None,
        failure=None,
        task_instruction={"remember": {"key": "k", "value": "v"}},
    )


def test_openai_longhorizon_maps_strict_json_action():
    responses = _RuleDrivenResponses()
    agent = OpenAILongHorizonAgent(model="test-model", client=_Client(responses))
    action = agent.act(_observation())
    assert action.kind == "remember"
    assert action.payload == {"key": "k", "value": "v"}
    request = responses.requests[0]
    assert request["model"] == "test-model"
    assert request["payload"]["observation"]["task_instruction"] == {
        "remember": {"key": "k", "value": "v"}
    }
    assert "remember" in request["payload"]["action_contracts"]


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ({"kind": "invent", "payload": {}}, "unsupported long-horizon action kind"),
        ({"kind": "checkpoint", "payload": {"extra": 1}}, "unsupported payload fields"),
        ({"kind": "remember", "payload": {"key": "k"}}, "missing payload fields"),
        ({"kind": "checkpoint", "payload": [], "note": "x"}, "unsupported top-level fields"),
    ],
)
def test_openai_longhorizon_rejects_actions_outside_contract(output, message):
    class Responses:
        def create(self, **_kwargs):
            return SimpleNamespace(output_text=json.dumps(output))

    agent = OpenAILongHorizonAgent(client=_Client(Responses()))
    with pytest.raises(ValueError, match=message):
        agent.act(_observation())


def test_stateless_openai_adapter_can_drive_all_varied_reference_protocol_instances(tmp_path):
    responses = _RuleDrivenResponses()
    client = _Client(responses)

    def factory(_instance: SandboxProtocolInstance):
        # The harness may create this adapter repeatedly across failure boundaries; the adapter
        # itself carries no conversation/thread identifier between calls.
        return OpenAILongHorizonAgent(model="test-model", client=client)

    report = run_sandbox_protocol(factory, sandbox_root=tmp_path / "openai-adapter")
    assert report.passed is True
    assert report.instance_count == 3
    assert report.varied_task_count == 3
    assert report.retention_passes == 3
    retain_requests = [
        request["payload"]["observation"]
        for request in responses.requests
        if request["payload"]["observation"]["phase"] == "retain"
        and not request["payload"]["observation"]["failure"]
    ]
    assert len(retain_requests) == 3
    for observation in retain_requests:
        instruction = observation["task_instruction"]
        assert set(instruction) == {"recall_key"}
        key = instruction["recall_key"]
        assert key in observation["durable_memory"]
