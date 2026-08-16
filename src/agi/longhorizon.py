from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol


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


def _rollback(state: _State) -> None:
    if state.checkpoint is None:
        raise RuntimeError("rollback requested without checkpoint")
    expected_digest = state.checkpoint_digest
    restored = json.loads(json.dumps(state.checkpoint))
    state.phase = restored["phase"]
    state.workspace = dict(restored["workspace"])
    state.durable_memory = restored["durable_memory"]
    state.completed_phases = list(restored["completed_phases"])
    state.rollback_verified = _digest(restored) == expected_digest


def _observation(state: _State, *, failure: str | None = None) -> LongHorizonObservation:
    return LongHorizonObservation(
        status="failure" if failure else "ready",
        phase=state.phase,
        workspace=dict(state.workspace),
        durable_memory=json.loads(json.dumps(state.durable_memory)),
        checkpoint_digest=state.checkpoint_digest,
        failure=failure,
    )


def _advance(state: _State) -> None:
    # Recovery is exceptional: successful risky work proceeds directly to delayed retention.
    order = ["learn", "checkpoint", "intervene", "retain", "regression", "finish"]
    index = order.index(state.phase)
    if state.phase not in state.completed_phases:
        state.completed_phases.append(state.phase)
    state.phase = order[min(index + 1, len(order) - 1)]


def _apply(state: _State, action: LongHorizonAction) -> str | None:
    kind = action.kind
    payload = action.payload
    if state.phase == "learn":
        if kind != "remember" or "key" not in payload or "value" not in payload:
            return "expected durable remember action"
        state.durable_memory[str(payload["key"])] = payload["value"]
        state.workspace["protected.txt"] = "BASELINE-PROTECTED"
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
            state.workspace["protected.txt"] = "CORRUPTED"
            return "injected transient write failure after partial mutation"
        if kind != "write" or payload.get("path") != "result.txt":
            return "expected result write after recovery"
        state.workspace["result.txt"] = str(payload.get("content", ""))
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
        expected = payload.get("expected")
        state.retention_verified = key in state.durable_memory and state.durable_memory[key] == expected
        if not state.retention_verified:
            return "delayed retention check failed"
        _advance(state)
        return None

    if state.phase == "regression":
        if kind != "verify_protected":
            return "expected protected regression verification"
        state.protected_regression_verified = state.workspace.get("protected.txt") == "BASELINE-PROTECTED"
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
) -> LongHorizonResult:
    """Evaluate durable planning across fresh contexts, failure, rollback, and delayed retention.

    A fresh agent instance is created after the injected failure. Only the durable observation is
    carried across that boundary; private in-process model context is not. Reference agents validate
    the harness only and are not claim-grade AGI evidence.
    """
    if max_turns < 8:
        raise ValueError("max_turns must be at least 8")
    state = _State()
    agent = agent_factory()
    trace: list[dict[str, Any]] = []
    failure: str | None = None

    for _ in range(max_turns):
        obs = _observation(state, failure=failure)
        action = agent.act(obs)
        if not isinstance(action, LongHorizonAction):
            raise TypeError("long-horizon agent returned invalid action type")
        trace.append({"observation": asdict(obs), "action": asdict(action)})

        if failure:
            # Context is deliberately replaced after failure; recovery must use durable state only.
            if action.kind != "rollback":
                failure = "fresh context failed to choose rollback"
                continue
            state.phase = "recover"
            failure = _apply(state, action)
            agent = agent_factory()
            continue

        error = _apply(state, action)
        if error:
            failure = error
            if state.phase == "intervene" and state.injected_failures == 1:
                agent = agent_factory()
            continue

        if state.phase == "finish":
            final_obs = _observation(state)
            final_action = agent.act(final_obs)
            trace.append({"observation": asdict(final_obs), "action": asdict(final_action)})
            passed = (
                final_action.kind == "finish"
                and state.injected_failures == 1
                and state.restarts >= 1
                and state.rollback_verified
                and state.retention_verified
                and state.protected_regression_verified
                and "result.txt" in state.workspace
            )
            return LongHorizonResult(
                passed=passed,
                completed_phases=tuple(state.completed_phases),
                restarts=state.restarts,
                injected_failures=state.injected_failures,
                rollback_verified=state.rollback_verified,
                retention_verified=state.retention_verified,
                protected_regression_verified=state.protected_regression_verified,
                final_digest=_digest({"workspace": state.workspace, "memory": state.durable_memory}),
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
        final_digest=_digest({"workspace": state.workspace, "memory": state.durable_memory}),
        trace=tuple(trace),
    )


class ReferenceLongHorizonAgent:
    """Protocol reference for harness validation; deliberately not AGI evidence."""

    name = "reference-long-horizon-agent"

    def act(self, observation: LongHorizonObservation) -> LongHorizonAction:
        if observation.failure:
            return LongHorizonAction("rollback")
        if observation.phase == "learn":
            return LongHorizonAction("remember", {"key": "retained-rule", "value": "NUMERIC-SORT"})
        if observation.phase == "checkpoint":
            return LongHorizonAction("checkpoint")
        if observation.phase == "intervene":
            return LongHorizonAction("write", {"path": "result.txt", "content": "completed-after-recovery"})
        if observation.phase == "retain":
            return LongHorizonAction("recall", {"key": "retained-rule", "expected": "NUMERIC-SORT"})
        if observation.phase == "regression":
            return LongHorizonAction("verify_protected")
        return LongHorizonAction("finish")


def write_report(report: LongHorizonResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
