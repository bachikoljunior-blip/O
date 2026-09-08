from __future__ import annotations

import json
from types import SimpleNamespace

from agi.benchmark import ReferenceAgent
from agi.campaign import run_campaign
from agi.openai_agent import OpenAIWorkspaceAgent
from agi.workspace import ReferenceWorkspaceAgent, WorkspaceAction, workspace_suite


def test_repeated_reference_campaign_proves_harness_not_agi(tmp_path):
    report = run_campaign(
        campaign_id="test-reference",
        core_agent_factory=ReferenceAgent,
        workspace_agent_factory=ReferenceWorkspaceAgent,
        runs=2,
        artifact_prefix="artifacts/test-reference",
    )
    assert report.all_development_tasks_passed is True
    assert len(report.runs) == 2
    assert len(report.records) == 48
    assert report.claim_evaluation["agi_claim_supported"] is False
    report.write(tmp_path)
    assert (tmp_path / "campaign.json").exists()
    assert len(json.loads((tmp_path / "ledger.json").read_text())) == 48


class FakeResponses:
    def __init__(self, output: str):
        self.output = output
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.output)


class FakeClient:
    def __init__(self, output: str):
        self.responses = FakeResponses(output)


def test_openai_workspace_adapter_returns_valid_json_action_without_exposing_expectations():
    client = FakeClient('{"kind":"tool","tool":"read_file","arguments":{"path":"numbers.json"}}')
    agent = OpenAIWorkspaceAgent(model="test-model", client=client)
    task = workspace_suite()[0]
    action = agent.act(
        task,
        state=SimpleNamespace(memory={}, procedure={}, tool_failures_seen=0),
        observation={"status": "start", "visible_files": ["numbers.json"]},
        trace=(),
    )
    assert action == WorkspaceAction("tool", "read_file", {"path": "numbers.json"})
    payload = client.responses.calls[0]["input"]
    assert "expected_json_files" not in payload
    assert task.task_id not in payload
    assert "numbers.json" in payload
