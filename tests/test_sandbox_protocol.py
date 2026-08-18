from __future__ import annotations

import hashlib
import json

import pytest

from agi.longhorizon import LongHorizonTask, ReferenceLongHorizonAgent
from agi.sandbox_protocol import (
    MIN_REPEATED_INSTANCES,
    SandboxProtocolInstance,
    deterministic_sandbox_instances,
    run_sandbox_protocol,
    validate_sandbox_instances,
    verify_persisted_checkpoint,
)


def _reference_factory(_instance: SandboxProtocolInstance) -> ReferenceLongHorizonAgent:
    return ReferenceLongHorizonAgent()


def _rewrite_digest(raw):
    payload = json.dumps(
        raw["checkpoint"],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode()
    raw["digest"] = hashlib.sha256(payload).hexdigest()


def test_reference_sandbox_protocol_repeats_varied_durable_recovery(tmp_path):
    protocol = deterministic_sandbox_instances()
    report = run_sandbox_protocol(
        _reference_factory,
        sandbox_root=tmp_path / "sandbox",
        instances=protocol,
    )
    assert report.passed is True
    assert report.instance_count == MIN_REPEATED_INSTANCES == 3
    assert report.varied_task_count == 3
    assert report.passed_instances == 3
    assert report.verified_checkpoints == 3
    assert report.rollback_passes == 3
    assert report.retention_passes == 3
    assert report.protected_regression_passes == 3
    assert len({item.instance_commitment for item in report.instances}) == 3
    assert len({item.task_commitment for item in report.instances}) == 3
    assert len({item.final_digest for item in report.instances}) == 3
    assert len(report.protocol_digest) == 64
    for item in report.instances:
        assert item.checkpoint_verified is True
        assert item.checkpoint_digest is not None
        assert item.passed is True


def test_protocol_checkpoint_verifier_detects_task_specific_payload_tampering(tmp_path):
    protocol = deterministic_sandbox_instances()
    root = tmp_path / "sandbox"
    report = run_sandbox_protocol(_reference_factory, sandbox_root=root, instances=protocol)
    instance = protocol[0]
    checkpoint = root / report.instances[0].instance_id / "checkpoint.json"
    raw = json.loads(checkpoint.read_text(encoding="utf-8"))
    raw["checkpoint"]["workspace"][instance.task.protected_path] = "TAMPERED"
    checkpoint.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")

    verification = verify_persisted_checkpoint(checkpoint, task=instance.task)
    assert verification.valid is False
    assert verification.reason == "task-specific protected baseline is missing from checkpoint"


def test_protocol_checkpoint_verifier_rejects_digest_rewrite_of_bad_baseline(tmp_path):
    protocol = deterministic_sandbox_instances()
    root = tmp_path / "sandbox"
    report = run_sandbox_protocol(_reference_factory, sandbox_root=root, instances=protocol)
    instance = protocol[0]
    checkpoint = root / report.instances[0].instance_id / "checkpoint.json"
    raw = json.loads(checkpoint.read_text(encoding="utf-8"))
    raw["checkpoint"]["workspace"][instance.task.protected_path] = "TAMPERED"
    _rewrite_digest(raw)
    checkpoint.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")

    verification = verify_persisted_checkpoint(checkpoint, task=instance.task)
    assert verification.valid is False
    assert verification.reason == "task-specific protected baseline is missing from checkpoint"


def test_protocol_checkpoint_verifier_rejects_rewritten_retained_memory(tmp_path):
    protocol = deterministic_sandbox_instances()
    root = tmp_path / "sandbox"
    report = run_sandbox_protocol(_reference_factory, sandbox_root=root, instances=protocol)
    instance = protocol[1]
    checkpoint = root / report.instances[1].instance_id / "checkpoint.json"
    raw = json.loads(checkpoint.read_text(encoding="utf-8"))
    raw["checkpoint"]["durable_memory"][instance.task.memory_key] = "REWRITTEN"
    _rewrite_digest(raw)
    checkpoint.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")

    verification = verify_persisted_checkpoint(checkpoint, task=instance.task)
    assert verification.valid is False
    assert verification.reason == "task-specific retained memory is missing from checkpoint"


def test_sandbox_protocol_definition_is_deterministic_varied_and_valid():
    first = deterministic_sandbox_instances()
    second = deterministic_sandbox_instances()
    assert first == second
    validation = validate_sandbox_instances(first)
    assert validation["valid"] is True
    assert validation["instance_count"] == MIN_REPEATED_INSTANCES
    assert validation["varied_task_count"] == 3
    assert len(set(validation["instance_commitments"])) == 3
    assert len(set(validation["task_commitments"])) == 3
    assert len({item.task.memory_key for item in first}) == 3
    assert len({item.task.result_path for item in first}) == 3
    assert len({item.task.protected_path for item in first}) == 3


def test_duplicate_or_unvaried_protocol_is_rejected(tmp_path):
    varied = deterministic_sandbox_instances()
    duplicate_id = (
        varied[0],
        SandboxProtocolInstance("longhorizon-01", task=varied[1].task),
        varied[2],
    )
    validation = validate_sandbox_instances(duplicate_id)
    assert validation["valid"] is False
    with pytest.raises(ValueError, match="instance ids must be unique"):
        run_sandbox_protocol(_reference_factory, sandbox_root=tmp_path, instances=duplicate_id)

    same_task = LongHorizonTask()
    unvaried = tuple(
        SandboxProtocolInstance(f"same-task-{index}", task=same_task)
        for index in range(1, 4)
    )
    validation = validate_sandbox_instances(unvaried)
    assert validation["valid"] is False
    assert "task variants must be distinct" in validation["reasons"]
    with pytest.raises(ValueError, match="task variants must be distinct"):
        run_sandbox_protocol(_reference_factory, sandbox_root=tmp_path, instances=unvaried)

    with pytest.raises(ValueError, match="at least three"):
        deterministic_sandbox_instances(MIN_REPEATED_INSTANCES - 1)
