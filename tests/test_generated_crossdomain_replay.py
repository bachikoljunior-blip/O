from __future__ import annotations

import json
from pathlib import Path

import pytest

import continual.engine as engine_module
from continual.candidate_regression import CandidateIntegrityError
from agi.generated_crossdomain_cegis import run_generated_crossdomain_cegis_campaign
from agi.generated_crossdomain_replay import verify_generated_crossdomain_replay


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-generated-crossdomain-replay"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(f"model call was not expected in cross-domain replay: {component}")


def test_crossdomain_replay_is_restart_safe_and_does_not_consume_trial_budget(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    seed = "cross-family-14"
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)

    acquired = run_generated_crossdomain_cegis_campaign(runtime_repo, seed)
    assert acquired["passed"] is True

    candidate_dir = runtime_repo / ".continual" / "candidates" / acquired["candidate_id"]
    trial_file = next((candidate_dir / "trials").glob("*.json"))
    ledger_before = trial_file.read_bytes()
    candidate_before = (candidate_dir / "candidate.json").read_bytes()

    first = verify_generated_crossdomain_replay(runtime_repo, seed)
    second = verify_generated_crossdomain_replay(runtime_repo, seed)

    assert first["passed"] is True
    assert second["passed"] is True
    assert first["campaign_id"] == acquired["campaign_id"] == second["campaign_id"]
    assert first["candidate_id"] == acquired["candidate_id"] == second["candidate_id"]
    assert first["scope"] == acquired["scope"] == second["scope"]
    assert first["commitment"] == acquired["commitment"] == second["commitment"]
    assert first["source_evidence_digest"] == second["source_evidence_digest"]
    assert first["trial_attempt_count_before"] == 2
    assert first["trial_attempt_count_after"] == 2
    assert second["trial_attempt_count_before"] == 2
    assert second["trial_attempt_count_after"] == 2
    assert first["candidate_state_unchanged"] is True
    assert first["trial_ledger_unchanged"] is True
    assert first["replay_consumed_trial_budget"] is False
    assert all(item["score"] == 1.0 for item in first["replay_passes"])
    assert trial_file.read_bytes() == ledger_before
    assert (candidate_dir / "candidate.json").read_bytes() == candidate_before
    assert NeverModelClient.calls == []

    replay_evidence = json.loads(
        (
            runtime_repo
            / ".continual"
            / "evidence"
            / "generated-crossdomain-replay"
            / f"{first['campaign_id']}.json"
        ).read_text(encoding="utf-8")
    )
    assert replay_evidence["digest"] == second["digest"]
    assert seed not in json.dumps(replay_evidence, sort_keys=True)
    assert "independent production" in replay_evidence["claim_boundary"]


def test_crossdomain_replay_fails_closed_if_verified_candidate_body_changes(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    seed = "cross-family-2"
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)
    acquired = run_generated_crossdomain_cegis_campaign(runtime_repo, seed)
    candidate_path = (
        runtime_repo
        / ".continual"
        / "candidates"
        / acquired["candidate_id"]
        / "candidate.json"
    )
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate["description"] = "tampered after deterministic verification"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    with pytest.raises(CandidateIntegrityError):
        verify_generated_crossdomain_replay(runtime_repo, seed)
