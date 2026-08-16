from __future__ import annotations

import json

import pytest

from agi.longhorizon import ReferenceLongHorizonAgent
from agi.sandbox_protocol import (
    SandboxProtocolInstance,
    deterministic_sandbox_instances,
    run_sandbox_protocol,
    validate_sandbox_instances,
    verify_persisted_checkpoint,
)


def _reference_factory(_instance: SandboxProtocolInstance) -> ReferenceLongHorizonAgent:
    return ReferenceLongHorizonAgent()


def test_reference_sandbox_protocol_repeats_durable_recovery(tmp_path):
    report = run_sandbox_protocol(_reference_factory, sandbox_root=tmp_path / "sandbox")
    assert report.passed is True
    assert report.instance_count == 3
    assert report.passed_instances == 3
    assert report.verified_checkpoints == 3
    assert report.rollback_passes == 3
    assert report.retention_passes == 3
    assert report.protected_regression_passes == 3
    assert len({item.instance_commitment for item in report.instances}) == 3
    assert len(report.protocol_digest) == 64
    for item in report.instances:
        assert item.checkpoint_verified is True
        assert item.checkpoint_digest is not None
        assert item.passed is True


def test_protocol_checkpoint_verifier_detects_payload_tampering(tmp_path):
    root = tmp_path / "sandbox"
    report = run_sandbox_protocol(_reference_factory, sandbox_root=root)
    checkpoint = root / report.instances[0].instance_id / "checkpoint.json"
    raw = json.loads(checkpoint.read_text(encoding="utf-8"))
    raw["checkpoint"]["workspace"]["protected.txt"] = "TAMPERED"
    checkpoint.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")

    verification = verify_persisted_checkpoint(checkpoint)
    assert verification.valid is False
    assert verification.reason in {"protected baseline is missing from checkpoint", "checkpoint digest mismatch"}


def test_protocol_checkpoint_verifier_rejects_digest_rewrite_of_bad_baseline(tmp_path):
    root = tmp_path / "sandbox"
    report = run_sandbox_protocol(_reference_factory, sandbox_root=root)
    checkpoint = root / report.instances[0].instance_id / "checkpoint.json"
    raw = json.loads(checkpoint.read_text(encoding="utf-8"))
    raw["checkpoint"]["workspace"]["protected.txt"] = "TAMPERED"
    # Replacing the digest is not sufficient because the verifier also checks protocol invariants.
    import hashlib

    payload = json.dumps(
        raw["checkpoint"],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode()
    raw["digest"] = hashlib.sha256(payload).hexdigest()
    checkpoint.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")

    verification = verify_persisted_checkpoint(checkpoint)
    assert verification.valid is False
    assert verification.reason == "protected baseline is missing from checkpoint"


def test_sandbox_protocol_definition_is_deterministic_and_valid():
    first = deterministic_sandbox_instances()
    second = deterministic_sandbox_instances()
    assert first == second
    validation = validate_sandbox_instances(first)
    assert validation["valid"] is True
    assert validation["instance_count"] == 3
    assert len(set(validation["instance_commitments"])) == 3


def test_duplicate_or_short_protocol_is_rejected(tmp_path):
    duplicate = (
        SandboxProtocolInstance("same"),
        SandboxProtocolInstance("same"),
        SandboxProtocolInstance("other"),
    )
    validation = validate_sandbox_instances(duplicate)
    assert validation["valid"] is False
    with pytest.raises(ValueError, match="instance ids must be unique"):
        run_sandbox_protocol(_reference_factory, sandbox_root=tmp_path, instances=duplicate)

    with pytest.raises(ValueError, match="at least three"):
        deterministic_sandbox_instances(2)
