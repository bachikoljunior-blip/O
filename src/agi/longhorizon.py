from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class LongHorizonTask:
    """Deterministic task parameters for one long-horizon recovery evaluation."""

    task_id: str = "default-longhorizon"
    memory_key: str = "retained-rule"
    memory_value: Any = "NUMERIC-SORT"
    protected_path: str = "protected.txt"
    protected_value: str = "BASELINE-PROTECTED"
    corrupted_value: str = "CORRUPTED"
    result_path: str = "result.txt"
    result_content: str = "completed-after-recovery"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def commitment(self) -> str:
        return _digest(self.to_dict())


@dataclass(frozen=True)
class LongHorizonAction:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LongHorizonObservation:
    status: str
    phase: str
    workspace: dict[str, str]
    durable_memory: dict[str, Any]
    checkpoint_digest: str | None
    failure: str | None = None
    task_instruction: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LongHorizonResult:
    passed: bool
    completed_phases: tuple[str, ...]
    restarts: int
    injected_failures: int
    rollback_verified: bool
    retention_verified: bool
    protected_regression_verified: bool
    final_digest: str
    trace: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LongHorizonAgent(Protocol):
    name: str

    def act(self, observation: LongHorizonObservation) -> LongHorizonAction: ...


@dataclass
class _State:
    phase: str = "learn"
    workspace: dict[str, str] = field(default_factory=dict)
    durable_memory: dict[str, Any] = field(default_factory=dict)
    checkpoint: dict[str, Any] | None = None
    checkpoint_digest: str | None = None
    trusted_checkpoint_digest: str | None = None
    completed_phases: list[str] = field(default_factory=list)
    restarts: int = 0
    injected_failures: int = 0
    rollback_verified: bool = False
    retention_verified: bool = False
    protected_regression_verified: bool = False
    transient_failure_pending: bool = True


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _checkpoint(state: _State) -> None:
    state.checkpoint = {
        "phase": state.phase,
        "workspace": dict(state.workspace),
        "durable_memory": json.loads(json.dumps(state.durable_memory)),
        "completed_phases": list(state.completed_phases),
    }
    state.checkpoint_digest = _digest(state.checkpoint)
    # This commitment is evaluator-owned state, not part of the mutable checkpoint envelope.
    state.trusted_checkpoint_digest = state.checkpoint_digest


def _persist_checkpoint(state: _State, path: Path) -> None:
    if state.checkpoint is None or state.checkpoint_digest is None:
        raise RuntimeError("cannot persist an empty checkpoint")
    payload = {"checkpoint": state.checkpoint, "digest": state.checkpoint_digest}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _reload_checkpoint(state: _State, path: Path) -> str | None:
    """Reload only a checkpoint that still matches the pre-failure evaluator commitment."""
    trusted_digest = state.trusted_checkpoint_digest
    if trusted_digest is None:
        return "trusted checkpoint commitment is missing"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "durable checkpoint reload failed"
    if not isinstance(raw, dict):
        return "durable checkpoint envelope is invalid"
    checkpoint = raw.get("checkpoint")
    stored_digest = raw.get("digest")
    if not isinstance(checkpoint, dict) or not isinstance(stored_digest, str):
        return "durable checkpoint envelope is invalid"
    computed_digest = _digest(checkpoint)
    if stored_digest != trusted_digest or computed_digest != trusted_digest:
        return "durable checkpoint does not match trusted commitment"
    state.checkpoint = checkpoint
    state.checkpoint_digest = stored_digest
    return None


def _rollback(state: _State) -> None:
    if state.checkpoint is None:
        raise RuntimeError("rollback requested without checkpoint")
    expected_digest = state.trusted_checkpoint_digest or state.checkpoint_digest
    restored = json.loads(json.dumps(state.checkpoint))
    state.phase = restored["phase"]
    state.workspace = dict(restored["workspace"])
    state.durable_memory = restored["durable_memory"]
    state.completed_phases = list(restored["completed_phases"])
    state.rollback_verified = expected_digest is not None and _digest(restored) == expected_digest


def _instruction(task: LongHorizonTask, phase: str) -> dict[str, Any]:
    if phase == "learn":
        return {"remember": {"key": task.memory_key, "value": task.memory_value}}
    if phase == "checkpoint":
        return {"checkpoint": True}
    if phase == "intervene":
        return {"write": {"path": task.result_path, "content": task.result_content}}
    if phase == "retain":
        # The expected value is deliberately not re-exposed here. It must survive in durable state.
        return {"recall_key": task.memory_key}
    if phase == "regression":
        return {"verify_protected_path": task.protected_path}
    if phase == "finish":
        return {"finish": True}
    return {}


def _observation(
    state: _State,
    task: LongHorizonTask,
    *,
    failure: str | None = None,
) -> LongHorizonObservation:
    return LongHorizonObservation(
        status="failure" if failure else "ready",
        phase=state.phase,
        workspace=dict(state.workspace),
        durable_memory=json.loads(json.dumps(state.durable_memory)),
        checkpoint_digest=state.checkpoint_digest or state.trusted_checkpoint_digest,
        failure=failure,
        task_instruction=_instruction(task, state.phase),
    )


def _advance(state: _State) -> None:
    # Recovery is exceptional: successful risky work proceeds directly to delayed retention.
    order = ["learn", "checkpoint", "intervene", "retain", "regression", "finish"]
    index = order.index(state.phase)
    if state.phase not in state.completed_phases:
        state.completed_phases.append(state.phase)
    state.phase = order[min(index + 1, len(order) - 1)]


def _apply(state: _State, action: LongHorizonAction, task: LongHorizonTask) -> str | None:
    kind = action.kind
    payload = action.payload
    if state.phase == "learn":
        if kind != "remember" or "key" not in payload or "value" not in payload:
            return "expected durable remember action"
        if str(payload["key"]) != task.memory_key or payload["value"] != task.memory_value:
            return "remember action did not satisfy task specification"
        state.durable_memory[task.memory_key] = payload["value"]
        state.workspace[task.protected_path] = task.protected_value
        _advance(state)
        return None

    if state.phase == "checkpoint":
        if kind != "checkpoint":
            return "expected checkpoint before risky work"
        _checkpoint(state)
        _advance(state)
        return None

    if state.phase == "intervene":
        if state.transient_failure_pending:
            state.transient_failure_pending = False
            state.injected_failures += 1
            state.workspace[task.protected_path] = task.corrupted_value
            return "injected transient write failure after partial mutation"
        if kind != "write" or payload.get("path") != task.result_path:
            return "expected task-specific result write after recovery"
        if str(payload.get("content", "")) != task.result_content:
            return "result content did not satisfy task specification"
        state.workspace[task.result_path] = task.result_content
        _advance(state)
        return None

    if state.phase == "recover":
        if kind != "rollback":
            return "expected rollback after injected failure"
        _rollback(state)
        state.phase = "intervene"
        state.restarts += 1
        return None

    if state.phase == "retain":
        if kind != "recall":
            return "expected delayed recall"
        key = str(payload.get("key", ""))
        reported = payload["value"] if "value" in payload else payload.get("expected")
        state.retention_verified = bool(
            key == task.memory_key
            and key in state.durable_memory
            and state.durable_memory[key] == task.memory_value
            and reported == task.memory_value
        )
        if not state.retention_verified:
            return "delayed retention check failed"
        _advance(state)
        return None

    if state.phase == "regression":
        if kind != "verify_protected":
            return "expected protected regression verification"
        requested_path = str(payload.get("path", task.protected_path))
        if requested_path != task.protected_path:
            return "protected regression verification targeted the wrong path"
        state.protected_regression_verified = state.workspace.get(task.protected_path) == task.protected_value
        if not state.protected_regression_verified:
            return "protected regression detected"
        _advance(state)
        return None

    if state.phase == "finish":
        return None if kind == "finish" else "expected finish"

    return f"unknown phase: {state.phase}"


def run_long_horizon(
    agent_factory: Callable[[], LongHorizonAgent],
    *,
    max_turns: int = 24,
    checkpoint_path: Path | None = None,
    before_checkpoint_reload: Callable[[Path], None] | None = None,
    task: LongHorizonTask | None = None,
) -> LongHorizonResult:
    """Evaluate durable planning across fresh contexts, failure, rollback, and delayed retention.

    The task can vary memory, protected state, and post-recovery output without putting the delayed
    recall answer back into the retain-phase instruction. The checkpoint is persisted atomically
    before risky work, and a fresh agent context may reload it only if it still matches the
    evaluator-owned pre-failure commitment.

    Reference agents validate the harness only and are not claim-grade AGI evidence.
    """
    if max_turns < 8:
        raise ValueError("max_turns must be at least 8")
    active_task = task or LongHorizonTask()
    state = _State()
    agent = agent_factory()
    trace: list[dict[str, Any]] = []
    failure: str | None = None
    reload_fault_injected = False

    for _ in range(max_turns):
        obs = _observation(state, active_task, failure=failure)
        action = agent.act(obs)
        if not isinstance(action, LongHorizonAction):
            raise TypeError("long-horizon agent returned invalid action type")
        trace.append({"observation": asdict(obs), "action": asdict(action)})

        if failure:
            if action.kind != "rollback":
                failure = "fresh context failed to choose rollback"
                continue
            if checkpoint_path is not None and state.checkpoint is None:
                if before_checkpoint_reload is not None and not reload_fault_injected:
                    before_checkpoint_reload(checkpoint_path)
                    reload_fault_injected = True
                reload_error = _reload_checkpoint(state, checkpoint_path)
                if reload_error:
                    failure = reload_error
                    agent = agent_factory()
                    continue
            if state.checkpoint is None:
                failure = "rollback requested without durable checkpoint"
                agent = agent_factory()
                continue
            state.phase = "recover"
            failure = _apply(state, action, active_task)
            agent = agent_factory()
            continue

        previous_phase = state.phase
        error = _apply(state, action, active_task)
        if error is None and previous_phase == "checkpoint" and checkpoint_path is not None:
            _persist_checkpoint(state, checkpoint_path)

        if error:
            failure = error
            if state.phase == "intervene" and state.injected_failures == 1:
                if checkpoint_path is not None:
                    state.checkpoint = None
                    state.checkpoint_digest = None
                agent = agent_factory()
            continue

        if state.phase == "finish":
            final_obs = _observation(state, active_task)
            final_action = agent.act(final_obs)
            trace.append({"observation": asdict(final_obs), "action": asdict(final_action)})
            passed = (
                final_action.kind == "finish"
                and state.injected_failures == 1
                and state.restarts >= 1
                and state.rollback_verified
                and state.retention_verified
                and state.protected_regression_verified
                and state.workspace.get(active_task.result_path) == active_task.result_content
            )
            return LongHorizonResult(
                passed=passed,
                completed_phases=tuple(state.completed_phases),
                restarts=state.restarts,
                injected_failures=state.injected_failures,
                rollback_verified=state.rollback_verified,
                retention_verified=state.retention_verified,
                protected_regression_verified=state.protected_regression_verified,
                final_digest=_digest(
                    {
                        "task_commitment": active_task.commitment(),
                        "workspace": state.workspace,
                        "memory": state.durable_memory,
                    }
                ),
                trace=tuple(trace),
            )

    return LongHorizonResult(
        passed=False,
        completed_phases=tuple(state.completed_phases),
        restarts=state.restarts,
        injected_failures=state.injected_failures,
        rollback_verified=state.rollback_verified,
        retention_verified=state.retention_verified,
        protected_regression_verified=state.protected_regression_verified,
        final_digest=_digest(
            {
                "task_commitment": active_task.commitment(),
                "workspace": state.workspace,
                "memory": state.durable_memory,
            }
        ),
        trace=tuple(trace),
    )


class ReferenceLongHorizonAgent:
    """Protocol reference for harness validation; deliberately not AGI evidence."""

    name = "reference-long-horizon-agent"

    def act(self, observation: LongHorizonObservation) -> LongHorizonAction:
        if observation.failure:
            return LongHorizonAction("rollback")
        if observation.phase == "learn":
            remember = observation.task_instruction.get("remember", {})
            return LongHorizonAction("remember", {"key": remember.get("key"), "value": remember.get("value")})
        if observation.phase == "checkpoint":
            return LongHorizonAction("checkpoint")
        if observation.phase == "intervene":
            write = observation.task_instruction.get("write", {})
            return LongHorizonAction("write", {"path": write.get("path"), "content": write.get("content")})
        if observation.phase == "retain":
            key = str(observation.task_instruction.get("recall_key", ""))
            return LongHorizonAction("recall", {"key": key, "value": observation.durable_memory.get(key)})
        if observation.phase == "regression":
            return LongHorizonAction(
                "verify_protected",
                {"path": observation.task_instruction.get("verify_protected_path")},
            )
        return LongHorizonAction("finish")


def write_report(report: LongHorizonResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
