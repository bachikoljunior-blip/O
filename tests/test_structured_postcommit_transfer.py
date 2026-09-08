from __future__ import annotations

import json

from agi.structured_postcommit_transfer import run_structured_postcommit_transfer


def test_structured_postcommit_transfer_uses_unseen_support_and_fresh_durable_engines(tmp_path):
    report = run_structured_postcommit_transfer(tmp_path, "postcommit-transfer-test-seed")

    assert report["passed"] is True
    assert report["challenge_derived_after_refined_promotion"] is True
    assert report["challenge_support_overlap"] is False
    assert report["domain_chain"] == ["numeric", "object", "boolean"]
    assert report["challenge_count"] >= 3
    assert report["baseline_score"] == 0.0
    assert report["candidate_score"] == 1.0
    assert report["fresh_engine_count"] == report["challenge_count"]
    assert len(set(report["fresh_engine_runs"])) == report["fresh_engine_count"]
    assert report["durable_runs_persisted"] is True
    assert report["candidate_trial_state_unchanged"] is True
    assert report["refined_failed_hypothesis_retained"] is True
    assert report["external_negative_evidence_retained"] is True
    assert set(report["decisions"]) == {False, True}
    assert report["live_model_invocation_required"] is False
    assert "independent production AGI evidence" in report["claim_boundary"]

    evidence_dir = tmp_path / ".continual" / "evidence" / "structured-postcommit-transfer"
    evidence_paths = list(evidence_dir.glob("structured-postcommit-*.json"))
    assert len(evidence_paths) == 1
    persisted = json.loads(evidence_paths[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
