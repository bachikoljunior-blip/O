from __future__ import annotations

import json
from pathlib import Path

import continual.engine as engine_module
from agi.resumable_capability_campaign import run_resumable_capability_campaign


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-resumable-campaign-test"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(f"model call was not expected in deterministic campaign: {component}")


def test_capability_campaign_resumes_without_replaying_completed_stage(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)
    campaign_id = "hourly-resume-campaign-01"
    seeds = {
        "crossdomain_acquisition": "resume-crossdomain-101",
        "durable_transfer_rollback": "resume-durable-202",
        "external_tool_engine": "resume-external-303",
    }

    first = run_resumable_capability_campaign(
        runtime_repo,
        campaign_id,
        seeds=seeds,
        max_new_stages=1,
    )
    assert first["passed"] is False
    assert first["status"] == "in_progress"
    assert first["completed_stages"] == ["crossdomain_acquisition"]
    first_digest = first["stages"]["crossdomain_acquisition"]["result_digest"]
    first_attempts = first["stages"]["crossdomain_acquisition"]["attempts"]
    assert first_attempts == 1

    resumed = run_resumable_capability_campaign(runtime_repo, campaign_id, seeds=seeds)
    assert resumed["passed"] is True
    assert resumed["status"] == "completed"
    assert resumed["completed_count"] == 3
    assert resumed["resume_skipped_completed"] == 1
    assert resumed["last_advance_count"] == 2
    assert resumed["completed_stages"] == [
        "crossdomain_acquisition",
        "durable_transfer_rollback",
        "external_tool_engine",
    ]
    assert resumed["stages"]["crossdomain_acquisition"]["result_digest"] == first_digest
    assert resumed["stages"]["crossdomain_acquisition"]["attempts"] == first_attempts
    assert all(resumed["stages"][stage]["status"] == "completed" for stage in resumed["stage_order"])
    assert NeverModelClient.calls == []

    state_path = (
        runtime_repo
        / ".continual"
        / "system"
        / "capability-campaigns"
        / f"{campaign_id}.json"
    )
    state_text = state_path.read_text(encoding="utf-8")
    persisted = json.loads(state_text)
    assert persisted["digest"] == resumed["digest"]
    for raw_seed in seeds.values():
        assert raw_seed not in state_text
    assert "Independent production-grade external AGI evidence is still required" in persisted["claim_boundary"]

    replay = run_resumable_capability_campaign(runtime_repo, campaign_id, seeds=seeds)
    assert replay["passed"] is True
    assert replay["last_advance_count"] == 0
    assert replay["resume_skipped_completed"] == 3
    assert replay["stages"] == resumed["stages"]
