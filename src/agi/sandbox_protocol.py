from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .longhorizon import LongHorizonAgent, LongHorizonResult, LongHorizonTask, run_long_horizon


_INSTANCE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_REQUIRED_CHECKPOINT_KEYS = {"phase", "workspace", "durable_memory", "completed_phases"}


@dataclass(frozen=True)
class SandboxProtocolInstance:
    instance_id: str
    max_turns: int = 24
    task: LongHorizonTask = field(default_factory=LongHorizonTask)

    def commitment(self) -> str:
        return _digest(
            {
                "instance_id": self.instance_id,
                "max_turns": self.max_turns,
                "task_commitment": self.task.commitment(),
            }
        )


@dataclass(frozen=True)
class CheckpointVerification:
    valid: bool
    digest: str | None
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SandboxInstanceResult:
    instance_id: str
    task_id: str
    task_commitment: str
    instance_commitment: str
    checkpoint_path: str
    checkpoint_verified: bool
    checkpoint_digest: str | None
    passed: bool
    rollback_verified: bool
    retention_verified: bool
    protected_regression_verified: bool
    final_digest: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SandboxProtocolReport:
    passed: bool
    instance_count: int
    varied_task_count: int
    passed_instances: int
    verified_checkpoints: int
    rollback_passes: int
    retention_passes: int
    protected_regression_passes: int
    protocol_digest: str
    instances: tuple[SandboxInstanceResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _task_for_index(index: int) -> LongHorizonTask:
    return LongHorizonTask(
        task_id=f"retention-variant-{index:02d}",
        memory_key=f"retained-rule-{index:02d}",
        memory_value=f"RULE-{index:02d}-VALUE-{index * 17:03d}",
        protected_path=f"protected-{index:02d}.txt",
        protected_value=f"BASELINE-PROTECTED-{index:02d}",
        corrupted_value=f"CORRUPTED-{index:02d}",
        result_path=f"result-{index:02d}.txt",
        result_content=f"completed-after-recovery-{index:02d}",
    )


def deterministic_sandbox_instances(count: int = 3) -> tuple[SandboxProtocolInstance, ...]:
    """Return deterministic but semantically varied long-horizon task instances."""
    if count < 3:
        raise ValueError("sandbox protocol requires at least three repeated instances")
    return tuple(
        SandboxProtocolInstance(
            instance_id=f"longhorizon-{index:02d}",
            task=_task_for_index(index),
        )
        for index in range(1, count + 1)
    )


def validate_sandbox_instances(instances: Iterable[SandboxProtocolInstance]) -> dict[str, Any]:
    items = tuple(instances)
    reasons: list[str] = []
    ids = [item.instance_id for item in items]
    task_ids = [item.task.task_id for item in items]
    task_commitments = [item.task.commitment() for item in items]
    memory_keys = [item.task.memory_key for item in items]
    if len(items) < 3:
        reasons.append("at least three repeated instances are required")
    if len(ids) != len(set(ids)):
        reasons.append("instance ids must be unique")
    if len(task_ids) != len(set(task_ids)):
        reasons.append("task ids must be unique")
    if len(task_commitments) != len(set(task_commitments)):
        reasons.append("task variants must be distinct")
    if len(memory_keys) != len(set(memory_keys)):
        reasons.append("retained memory keys must vary across instances")
    for item in items:
        if not _INSTANCE_ID.fullmatch(item.instance_id):
            reasons.append(f"invalid instance id: {item.instance_id}")
        if item.max_turns < 8:
            reasons.append(f"{item.instance_id} max_turns must be at least 8")
        task = item.task
        if not task.task_id or not task.memory_key or not task.protected_path or not task.result_path:
            reasons.append(f"{item.instance_id} task fields must be non-empty")
        if task.protected_path == task.result_path:
            reasons.append(f"{item.instance_id} protected and result paths must differ")
        if task.protected_value == task.corrupted_value:
            reasons.append(f"{item.instance_id} corruption must differ from protected baseline")
    return {
        "valid": not reasons,
        "instance_count": len(items),
        "varied_task_count": len(set(task_commitments)),
        "instance_commitments": [item.commitment() for item in items],
        "task_commitments": task_commitments,
        "reasons": reasons,
    }


def verify_persisted_checkpoint(
    path: Path,
    *,
    task: LongHorizonTask | None = None,
) -> CheckpointVerification:
    """Verify checkpoint integrity plus task-specific retained/protected preconditions."""
    active_task = task or LongHorizonTask()
    if not path.exists():
        return CheckpointVerification(False, None, "checkpoint file is missing")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return CheckpointVerification(False, None, f"checkpoint is unreadable: {type(exc).__name__}")
    if not isinstance(raw, dict):
        return CheckpointVerification(False, None, "checkpoint envelope must be an object")
    checkpoint = raw.get("checkpoint")
    stored_digest = raw.get("digest")
    if not isinstance(checkpoint, dict):
        return CheckpointVerification(False, None, "checkpoint payload must be an object")
    if not isinstance(stored_digest, str):
        return CheckpointVerification(False, None, "checkpoint digest must be a string")
    missing = sorted(_REQUIRED_CHECKPOINT_KEYS - checkpoint.keys())
    if missing:
        return CheckpointVerification(False, None, f"checkpoint payload missing keys: {', '.join(missing)}")
    if checkpoint.get("phase") != "checkpoint":
        return CheckpointVerification(False, None, "checkpoint was not captured at the checkpoint phase")
    workspace = checkpoint.get("workspace")
    durable_memory = checkpoint.get("durable_memory")
    completed = checkpoint.get("completed_phases")
    if not isinstance(workspace, dict) or not isinstance(durable_memory, dict) or not isinstance(completed, list):
        return CheckpointVerification(False, None, "checkpoint payload has invalid field types")
    if workspace.get(active_task.protected_path) != active_task.protected_value:
        return CheckpointVerification(False, None, "task-specific protected baseline is missing from checkpoint")
    if durable_memory.get(active_task.memory_key) != active_task.memory_value:
        return CheckpointVerification(False, None, "task-specific retained memory is missing from checkpoint")
    computed = _digest(checkpoint)
    if computed != stored_digest:
        return CheckpointVerification(False, computed, "checkpoint digest mismatch")
    return CheckpointVerification(True, computed, None)


def _instance_result(
    instance: SandboxProtocolInstance,
    checkpoint_path: Path,
    result: LongHorizonResult,
    verification: CheckpointVerification,
) -> SandboxInstanceResult:
    passed = bool(result.passed and verification.valid)
    return SandboxInstanceResult(
        instance_id=instance.instance_id,
        task_id=instance.task.task_id,
        task_commitment=instance.task.commitment(),
        instance_commitment=instance.commitment(),
        checkpoint_path=checkpoint_path.as_posix(),
        checkpoint_verified=verification.valid,
        checkpoint_digest=verification.digest,
        passed=passed,
        rollback_verified=result.rollback_verified,
        retention_verified=result.retention_verified,
        protected_regression_verified=result.protected_regression_verified,
        final_digest=result.final_digest,
    )


def run_sandbox_protocol(
    agent_factory: Callable[[SandboxProtocolInstance], LongHorizonAgent],
    *,
    sandbox_root: Path,
    instances: Iterable[SandboxProtocolInstance] | None = None,
) -> SandboxProtocolReport:
    """Run repeated isolated, varied long-horizon tasks with durable checkpoint verification.

    Every instance varies the retained key/value, protected state, and required post-recovery output.
    The delayed-recall answer is not repeated in its retain-phase instruction. The wrapper requires
    durable rollback, retained-memory recovery, and protected-regression success on every variant.
    This remains harness/reference evidence only.
    """
    protocol = tuple(instances) if instances is not None else deterministic_sandbox_instances()
    validation = validate_sandbox_instances(protocol)
    if not validation["valid"]:
        raise ValueError("invalid sandbox protocol: " + "; ".join(validation["reasons"]))

    sandbox_root.mkdir(parents=True, exist_ok=True)
    results: list[SandboxInstanceResult] = []
    for instance in protocol:
        instance_dir = sandbox_root / instance.instance_id
        checkpoint_path = instance_dir / "checkpoint.json"
        if checkpoint_path.exists():
            checkpoint_path.unlink()
        result = run_long_horizon(
            lambda instance=instance: agent_factory(instance),
            max_turns=instance.max_turns,
            checkpoint_path=checkpoint_path,
            task=instance.task,
        )
        verification = verify_persisted_checkpoint(checkpoint_path, task=instance.task)
        results.append(_instance_result(instance, checkpoint_path, result, verification))

    passed_instances = sum(item.passed for item in results)
    verified_checkpoints = sum(item.checkpoint_verified for item in results)
    rollback_passes = sum(item.rollback_verified for item in results)
    retention_passes = sum(item.retention_verified for item in results)
    protected_regression_passes = sum(item.protected_regression_verified for item in results)
    instance_count = len(results)
    varied_task_count = len({item.task_commitment for item in results})
    digest_payload = [
        {
            "instance_commitment": item.instance_commitment,
            "task_commitment": item.task_commitment,
            "checkpoint_digest": item.checkpoint_digest,
            "final_digest": item.final_digest,
            "passed": item.passed,
        }
        for item in results
    ]
    passed = bool(
        instance_count >= 3
        and varied_task_count == instance_count
        and passed_instances == instance_count
        and verified_checkpoints == instance_count
        and rollback_passes == instance_count
        and retention_passes == instance_count
        and protected_regression_passes == instance_count
    )
    return SandboxProtocolReport(
        passed=passed,
        instance_count=instance_count,
        varied_task_count=varied_task_count,
        passed_instances=passed_instances,
        verified_checkpoints=verified_checkpoints,
        rollback_passes=rollback_passes,
        retention_passes=retention_passes,
        protected_regression_passes=protected_regression_passes,
        protocol_digest=_digest(digest_payload),
        instances=tuple(results),
    )


def write_protocol_report(report: SandboxProtocolReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
