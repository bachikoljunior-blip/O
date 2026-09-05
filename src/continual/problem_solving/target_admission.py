"""Session-local target/path admission extensions.

This module keeps target identity and per-phase repository paths inside the
transient problem-solving session. It does not create a global control plane.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any, Iterable, Mapping

from .model import (
    ALL_PHASES,
    TERMINAL,
    ClaimConflictError,
    PhaseAdmissionError,
    SessionStateError,
    _id,
    _now,
    _overlap,
    _path,
    _scope,
    _target,
)
from .session import ProblemSolvingSession


def _configure_execution(
    self: ProblemSolvingSession,
    target: str | None,
    phase_paths: Mapping[str, Iterable[str]] | None,
) -> None:
    self._execution_target = _target(target) if target is not None else None
    raw = phase_paths or {}
    unknown = sorted(set(raw) - ALL_PHASES)
    if unknown:
        raise PhaseAdmissionError(f"unknown phase path keys: {','.join(unknown)}")
    self._execution_phase_paths = {
        phase: tuple(paths) for phase, paths in raw.items()
    }


def _paths_for(self: ProblemSolvingSession, phase: str) -> tuple[str, ...]:
    return tuple(getattr(self, "_execution_phase_paths", {}).get(phase, ()))


def _claim_work(
    self: ProblemSolvingSession,
    *,
    worker_id: str,
    node_id: str,
    scope: str,
    target: str | None = None,
    reserved_paths: Iterable[str] = (),
    stale_after_seconds: int = 900,
    claim_id: str | None = None,
) -> dict[str, Any]:
    if self.state["status"] not in {"active", "interrupted"}:
        raise SessionStateError("session is not claimable")
    _id(worker_id, "worker_id")
    if node_id not in self.state["nodes"]:
        raise ValueError("unknown node")
    if isinstance(stale_after_seconds, bool) or stale_after_seconds < 1:
        raise ValueError("stale_after_seconds must be positive")

    normalized_scope = _scope(scope)
    normalized_target = _target(target) if target is not None else _target(
        f"node:{node_id}"
    )
    paths = sorted({_path(path) for path in reserved_paths})
    self._expire_stale_claims()
    for other in self._fresh():
        reasons: list[str] = []
        if self._within(node_id, other["node_id"]) or self._within(
            other["node_id"], node_id
        ):
            reasons.append("node")
        if _overlap(normalized_scope, other["scope"]):
            reasons.append("scope")
        if normalized_target == other.get("target"):
            reasons.append("target")
        if any(
            _overlap(path, reserved)
            for path in paths
            for reserved in other["reserved_paths"]
        ):
            reasons.append("path")
        if reasons:
            raise ClaimConflictError(
                f"collision with {other['claim_id']}: {','.join(reasons)}"
            )

    identifier = claim_id or f"claim-{uuid.uuid4().hex}"
    _id(identifier, "claim_id")
    stamp = _now()
    claim = {
        "claim_id": identifier,
        "worker_id": worker_id,
        "node_id": node_id,
        "scope": normalized_scope,
        "target": normalized_target,
        "reserved_paths": paths,
        "started_at": stamp,
        "heartbeat_at": stamp,
        "stale_after_seconds": stale_after_seconds,
        "status": "active",
    }
    self.state["claims"][identifier] = claim
    self.store.append(self.state, "claim", "created", {"claim": claim})
    return copy.deepcopy(claim)


def install() -> None:
    if getattr(ProblemSolvingSession, "_target_admission_installed", False):
        return

    original_admit = ProblemSolvingSession._admit
    original_step = ProblemSolvingSession.step

    def admit(
        self: ProblemSolvingSession,
        phase: str,
        node_id: str,
        worker_id: str | None,
        claim_id: str | None,
        paths: Iterable[str] = (),
    ) -> dict[str, Any]:
        declared_paths = tuple(paths) or self._paths_for(phase)
        result = original_admit(
            self, phase, node_id, worker_id, claim_id, declared_paths
        )
        target = getattr(self, "_execution_target", None)
        if result["mode"] == "exclusive" and worker_id is not None:
            claim = result.get("claim")
            if not isinstance(claim, dict):
                raise PhaseAdmissionError("exclusive admission lacks a claim")
            if target is None:
                target = claim.get("target")
                self._execution_target = target
            if target != claim.get("target"):
                raise PhaseAdmissionError("target differs from claim")
        result["target"] = target
        return result

    def step(
        self: ProblemSolvingSession,
        hooks,
        *,
        worker_id: str | None = None,
        claim_id: str | None = None,
        target: str | None = None,
        phase_paths: Mapping[str, Iterable[str]] | None = None,
    ) -> bool:
        self._configure_execution(target, phase_paths)
        return original_step(
            self, hooks, worker_id=worker_id, claim_id=claim_id
        )

    def run(
        self: ProblemSolvingSession,
        hooks,
        *,
        max_steps: int = 100,
        worker_id: str | None = None,
        claim_id: str | None = None,
        target: str | None = None,
        phase_paths: Mapping[str, Iterable[str]] | None = None,
    ) -> dict[str, Any]:
        for _ in range(max_steps):
            before = self.state["event_count"]
            progressed = self.step(
                hooks,
                worker_id=worker_id,
                claim_id=claim_id,
                target=target,
                phase_paths=phase_paths,
            )
            if self.state["status"] in TERMINAL or (
                not progressed and self.state["event_count"] == before
            ):
                break
        return copy.deepcopy(self.state)

    ProblemSolvingSession._configure_execution = _configure_execution
    ProblemSolvingSession._paths_for = _paths_for
    ProblemSolvingSession.claim_work = _claim_work
    ProblemSolvingSession._admit = admit
    ProblemSolvingSession.step = step
    ProblemSolvingSession.run = run
    ProblemSolvingSession._target_admission_installed = True
