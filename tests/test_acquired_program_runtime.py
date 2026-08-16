from __future__ import annotations

import json
from pathlib import Path

import pytest

import continual.engine as engine_module
from agi.acquired_program_runtime import run_acquired_program_runtime_campaign


class NeverExecuteModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-execute-test-model"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(f"model call was not expected in mechanical campaign: {component}")


def test_hidden_campaign_measures_persistent_before_after_gain_and_retains_negative_evidence(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    seed = "secret-heldout-runtime-seed-731"
    NeverExecuteModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverExecuteModelClient)

    report = run_acquired_program_runtime_campaign(runtime_repo, seed)

    assert report["passed"] is True
    assert report["commitment_verified"] is True
    assert report["seed_persisted"] is False
    assert report["heldout_answers_exposed_to_learner"] is False
    assert report["contamination_events"] == []
    assert report["inactive_before_regression"] is True
    assert report["baseline_score"] == 0.0
    assert report["isolated_candidate_score"] == 1.0
    assert report["post_restart_runtime_score"] == 1.0
    assert report["failed_protected_regression"]["adopt_candidate"] is False
    assert report["promotion"]["adopt_candidate"] is True
    assert report["negative_evidence_retained"] is True
    assert report["runtime_replay_reused"] is True
    assert all(item["success"] for item in report["candidate_results"])
    assert all(item["success"] for item in report["runtime_results"])
    assert all(item["program_kind"] == "acquired_program" for item in report["runtime_results"])
    assert NeverExecuteModelClient.calls == []

    candidate_path = (
        runtime_repo
        / ".continual"
        / "candidates"
        / report["candidate_id"]
        / "candidate.json"
    )
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert len(candidate["contradictory_evidence"]) == 1
    assert len(candidate["regression_decision_refs"]) == 2
    assert candidate["scope_states"][report["scope"]] == "VERIFIED_FOR_SCOPE"

    trial_files = list((candidate_path.parent / "trials").glob("*.json"))
    assert len(trial_files) == 1
    ledger = json.loads(trial_files[0].read_text(encoding="utf-8"))
    assert [item["attempt"] for item in ledger["attempts"]] == [1, 2]
    assert [item["state"] for item in ledger["attempts"]] == ["COMPLETED", "COMPLETED"]

    evidence_path = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "acquired-program-runtime"
        / f"{report['campaign_id']}.json"
    )
    persisted_text = evidence_path.read_text(encoding="utf-8")
    persisted = json.loads(persisted_text)
    assert persisted["digest"] == report["digest"]
    assert seed not in persisted_text
    assert "expected" not in persisted["runtime_results"][0]
    assert "query_sha256" in persisted["runtime_results"][0]
    assert "internally produced" in persisted["claim_boundary"]


def test_verified_campaign_replay_is_trial_idempotent_and_still_rejects_semantic_conflicts(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    seed = "secret-heldout-runtime-replay-seed-912"
    NeverExecuteModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverExecuteModelClient)

    first = run_acquired_program_runtime_campaign(runtime_repo, seed)
    candidate_path = (
        runtime_repo
        / ".continual"
        / "candidates"
        / first["candidate_id"]
        / "candidate.json"
    )
    evidence_path = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "acquired-program-runtime"
        / f"{first['campaign_id']}.json"
    )
    candidate_before = candidate_path.read_text(encoding="utf-8")
    evidence_before = evidence_path.read_text(encoding="utf-8")
    trials_before = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted((candidate_path.parent / "trials").glob("*.json"))
    }

    replay = run_acquired_program_runtime_campaign(runtime_repo, seed)

    assert replay["passed"] is True
    assert replay["digest"] == first["digest"]
    assert candidate_path.read_text(encoding="utf-8") == candidate_before
    assert evidence_path.read_text(encoding="utf-8") == evidence_before
    assert {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted((candidate_path.parent / "trials").glob("*.json"))
    } == trials_before
    assert NeverExecuteModelClient.calls == []

    conflicting = json.loads(candidate_before)
    conflicting["acquired_program"]["description"] = "tampered semantic description"
    candidate_path.write_text(
        json.dumps(conflicting, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="conflicts with campaign"):
        run_acquired_program_runtime_campaign(runtime_repo, seed)
