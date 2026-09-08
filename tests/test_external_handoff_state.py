from __future__ import annotations

import json
from pathlib import Path

from agi.external_claim import evaluate_external_ledger_claim
from agi.external_evaluation_request import validate_external_evaluation_request
from agi.external_source_artifact import build_git_archive_artifact
from agi.external_system_manifest import validate_public_system_manifest


ROOT = Path(__file__).resolve().parents[1]
HANDOFF = ROOT / "evidence" / "handoffs" / "work-recovery-v3"
SUBJECT_COMMIT = "f5f983604f8b7f4bb6ca4e8fc8f2523f7b88239f"


def _object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_checked_in_external_handoff_is_reproducible_and_ledger_bound() -> None:
    artifact = _object(HANDOFF / "artifact.json")
    manifest = _object(HANDOFF / "system-manifest.json")
    request = _object(HANDOFF / "evaluation-request.json")
    ledger = _object(ROOT / "evidence" / "external_ledger.json")

    rebuilt_artifact, _ = build_git_archive_artifact(ROOT, SUBJECT_COMMIT)
    assert rebuilt_artifact == artifact

    manifest_result = validate_public_system_manifest(manifest)
    request_result = validate_external_evaluation_request(request)
    assert manifest_result["commit_sha"] == SUBJECT_COMMIT
    assert manifest_result["artifact_sha256"] == artifact["artifact_sha256"]
    assert request_result["commit_sha"] == SUBJECT_COMMIT
    assert request_result["artifact_sha256"] == artifact["artifact_sha256"]
    assert request_result["system_manifest_sha256"] == manifest["manifest_sha256"]
    assert ledger["evaluation_requests"] == [request]
    assert ledger["challenges"] == []
    assert ledger["disclosures"] == []
    assert ledger["attestations"] == []


def test_checked_in_handoff_does_not_turn_readiness_into_an_agi_claim() -> None:
    ledger = _object(ROOT / "evidence" / "external_ledger.json")
    registry = _object(ROOT / "evidence" / "external_verifiers.json")

    report = evaluate_external_ledger_claim(
        ledger,
        registry,
        bridge_attestor_id="internal-validation-only",
        bridge_secret="nonproduction-test-value",
    )

    assert report["provenance_audit"]["clean"] is True
    assert report["agi_claim_supported"] is False
    assert report["external_attestations"] == []
    assert set(report["missing"]) == {
        "breadth",
        "transfer",
        "autonomy",
        "continual_learning",
        "self_improvement",
        "robustness",
    }
