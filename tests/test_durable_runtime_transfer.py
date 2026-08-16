from __future__ import annotations

import json
from pathlib import Path

import pytest

import continual.engine as engine_module
from agi.durable_runtime_transfer import run_durable_runtime_transfer_campaign
from continual.candidate_control import (
    CandidateScopeControlError,
    resume_verified_candidate_scope,
    suspend_verified_candidate_scope,
)


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-durable-runtime-test"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(f"model call was not expected in deterministic durable campaign: {component}")


def test_unseen_runtime_work_units_gain_persists_across_runs_and_supports_safe_rollback(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    seed = "durable-runtime-transfer-family-seed-14"
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)

    report = run_durable_runtime_transfer_campaign(runtime_repo, seed)

    assert report["passed"] is True
    assert report["seed_value_persisted"] is False
    assert report["raw_work_unit_inputs_persisted"] is False
    assert report["raw_expected_answers_persisted"] is False
    assert report["acquisition_probe_overlap"] is False
    assert report["work_unit_commitment_verified"] is True
    assert report["baseline"]["score"] == 0.0
    assert report["baseline"]["available_count"] == 0
    assert report["post_acquisition"]["score"] == 1.0
    assert report["while_suspended"]["score"] == 0.0
    assert report["while_suspended"]["available_count"] == 0
    assert report["after_resume"]["score"] == 1.0
    assert report["protected_before"]["score"] == 1.0
    assert report["protected_after_acquisition"]["score"] == 1.0
    assert report["protected_while_target_suspended"]["score"] == 1.0
    assert report["protected_after_resume"]["score"] == 1.0
    assert report["target_trial_budget_unchanged_by_runtime_and_control"] is True
    assert report["protected_prior_trial_budget_unchanged"] is True
    assert report["separate_durable_run_count"] == len(report["run_ids"])
    assert len(set(report["run_ids"])) >= 8
    assert NeverModelClient.calls == []

    evidence_path = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "durable-runtime-transfer"
        / f"{report['campaign_id']}.json"
    )
    persisted_text = evidence_path.read_text(encoding="utf-8")
    persisted = json.loads(persisted_text)
    assert persisted["digest"] == report["digest"]
    assert seed not in persisted_text
    assert "expected" not in persisted_text
    assert all("input_sha256" in item for item in persisted["post_acquisition"]["results"])


def test_resume_fails_closed_if_suspended_candidate_semantics_change(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    seed = "durable-runtime-transfer-tamper-seed-9"
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)
    report = run_durable_runtime_transfer_campaign(runtime_repo, seed)

    suspended = suspend_verified_candidate_scope(
        runtime_repo,
        candidate_id=report["candidate_id"],
        scope=report["scope"],
        reason="tamper-control-test",
    )
    assert suspended["state"] == "SUSPENDED_FOR_SCOPE"

    candidate_path = (
        runtime_repo
        / ".continual"
        / "candidates"
        / report["candidate_id"]
        / "candidate.json"
    )
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate["acquired_program"]["description"] = "tampered after verified suspension"
    candidate_path.write_text(
        json.dumps(candidate, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(CandidateScopeControlError, match="regression binding failed"):
        resume_verified_candidate_scope(
            runtime_repo,
            candidate_id=report["candidate_id"],
            scope=report["scope"],
            reason="must fail after semantic tamper",
        )
