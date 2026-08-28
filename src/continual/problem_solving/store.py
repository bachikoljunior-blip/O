"""Durable event log, admission replay, and repairable projections."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

from .model import (
    ALL_PHASES,
    OBSERVE_PHASES,
    EvidenceReplayError,
    UNFINISHED,
    _canon,
    _hash,
    _id,
    _now,
    _path,
    _target,
    _time,
)


class Store:
    """The hash-chained log is authoritative; JSON state/control are projections."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.sessions = self.root / "sessions"
        self.events = self.root / "events"
        self.sessions.mkdir(parents=True, exist_ok=True)
        self.events.mkdir(parents=True, exist_ok=True)
        self.control = self.root / "control.json"

    def state_path(self, session_id: str) -> Path:
        return self.sessions / f"{_id(session_id, 'session_id')}.json"

    def event_path(self, session_id: str) -> Path:
        return self.events / f"{_id(session_id, 'session_id')}.jsonl"

    @staticmethod
    def _write(path: Path, value: Mapping[str, Any]) -> None:
        fd, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def read_control(self) -> dict[str, Any]:
        if self.control.exists():
            return json.loads(self.control.read_text(encoding="utf-8"))
        return {
            "schema_version": 1,
            "mode": "normal",
            "active_session_id": None,
            "last_session_id": None,
            "generation": 0,
            "updated_at": _now(),
        }

    def write_control(self, mode: str, active: str | None, last: str | None) -> None:
        old = self.read_control()
        self._write(
            self.control,
            {
                "schema_version": 1,
                "mode": mode,
                "active_session_id": active,
                "last_session_id": last,
                "generation": int(old.get("generation", 0)) + 1,
                "updated_at": _now(),
            },
        )

    def append(
        self,
        state: dict[str, Any],
        phase: str,
        stage: str,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        previous = state.get("event_head_digest")
        state["event_count"] += 1
        state["updated_at"] = _now()
        snapshot = copy.deepcopy(state)
        snapshot["event_head_digest"] = previous
        body = {
            "schema_version": 1,
            "session_id": state["session_id"],
            "sequence": state["event_count"],
            "previous_event_digest": previous,
            "recorded_at": state["updated_at"],
            "phase": phase,
            "stage": stage,
            "payload": copy.deepcopy(dict(payload or {})),
            "state": snapshot,
        }
        record = {**body, "event_digest": _hash(body)}
        with self.event_path(state["session_id"]).open("a", encoding="utf-8") as stream:
            stream.write(_canon(record) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        state["event_head_digest"] = record["event_digest"]
        self._write(self.state_path(state["session_id"]), state)

    @staticmethod
    def _ancestors(state: Mapping[str, Any], node_id: str) -> list[str]:
        result: list[str] = []
        current: str | None = node_id
        nodes = state.get("nodes", {})
        while current is not None:
            if current not in nodes:
                raise EvidenceReplayError(f"admission references unknown node: {current}")
            result.append(current)
            current = nodes[current].get("parent_id")
        return list(reversed(result))

    @classmethod
    def _validate_admission(
        cls,
        record: Mapping[str, Any],
        previous_state: Mapping[str, Any] | None,
    ) -> None:
        payload = record.get("payload")
        if not isinstance(payload, Mapping) or "admission" not in payload:
            raise EvidenceReplayError("admitted event lacks admission evidence")
        admission = payload.get("admission")
        if not isinstance(admission, Mapping) or admission.get("admitted") is not True:
            raise EvidenceReplayError("invalid admission evidence")
        phase = record.get("phase")
        if phase not in ALL_PHASES or admission.get("phase") != phase:
            raise EvidenceReplayError("admission phase mismatch")
        expected_mode = "observe" if phase in OBSERVE_PHASES else "exclusive"
        if admission.get("mode") != expected_mode:
            raise EvidenceReplayError("admission mode mismatch")
        state = record.get("state")
        if not isinstance(state, Mapping):
            raise EvidenceReplayError("admitted event lacks state snapshot")
        if admission.get("session_id") != record.get("session_id"):
            raise EvidenceReplayError("admission session mismatch")
        node_id = admission.get("node_id")
        if not isinstance(node_id, str):
            raise EvidenceReplayError("admission node is invalid")
        cls._ancestors(state, node_id)
        paths = admission.get("paths")
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            raise EvidenceReplayError("admission paths are invalid")
        try:
            normalized_paths = sorted({_path(path) for path in paths})
        except ValueError as exc:
            raise EvidenceReplayError(f"admission path is unsafe: {exc}") from exc
        if normalized_paths != paths:
            raise EvidenceReplayError("admission paths are not canonical")
        target = admission.get("target")
        if target is not None:
            try:
                normalized_target = _target(target)
            except ValueError as exc:
                raise EvidenceReplayError(f"admission target is invalid: {exc}") from exc
            if normalized_target != target:
                raise EvidenceReplayError("admission target is not canonical")

        admission_state = copy.deepcopy(dict(state))
        admission_state["event_count"] = int(admission_state["event_count"]) - 1
        admission_state["updated_at"] = (
            previous_state.get("updated_at")
            if previous_state is not None
            else record["recorded_at"]
        )
        admission_state["event_head_digest"] = record.get("previous_event_digest")
        if admission.get("state_digest") != _hash(admission_state):
            raise EvidenceReplayError("admission state digest mismatch")

        claim = admission.get("claim")
        if expected_mode == "observe":
            if claim is not None:
                raise EvidenceReplayError("observe admission must not own a claim")
            return
        if claim == {"role": "session_coordinator"}:
            return
        if not isinstance(claim, Mapping):
            raise EvidenceReplayError("exclusive admission lacks an owner")
        claim_id = claim.get("claim_id")
        current_claim = admission_state.get("claims", {}).get(claim_id)
        if not isinstance(current_claim, Mapping) or dict(current_claim) != dict(claim):
            raise EvidenceReplayError("admission claim snapshot differs from session state")
        if claim.get("status") != "active":
            raise EvidenceReplayError("admission claim is not active")
        if target != claim.get("target"):
            raise EvidenceReplayError("admission target differs from claim")
        recorded_at = _time(str(record.get("recorded_at")))
        expires_at = _time(str(claim.get("heartbeat_at"))) + timedelta(
            seconds=int(claim.get("stale_after_seconds", 0))
        )
        if recorded_at > expires_at:
            raise EvidenceReplayError("admission claim was stale")
        if claim.get("node_id") not in cls._ancestors(admission_state, node_id):
            raise EvidenceReplayError("admission claim does not own the node")
        reserved = claim.get("reserved_paths")
        if not isinstance(reserved, list):
            raise EvidenceReplayError("admission claim paths are invalid")
        for path in normalized_paths:
            if not any(path == root or path.startswith(root + "/") for root in reserved):
                raise EvidenceReplayError("admission path is outside the claim")

    def replay(self, session_id: str, repair: bool = False) -> dict[str, Any]:
        path = self.event_path(session_id)
        if not path.exists():
            raise EvidenceReplayError(f"missing event log: {session_id}")
        previous_digest: str | None = None
        previous_state: dict[str, Any] | None = None
        final: dict[str, Any] | None = None
        for expected, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            record = json.loads(line)
            digest = record.pop("event_digest", None)
            if (
                record.get("sequence") != expected
                or record.get("previous_event_digest") != previous_digest
            ):
                raise EvidenceReplayError(
                    f"event sequence/hash predecessor mismatch at line {expected}"
                )
            if digest != _hash(record):
                raise EvidenceReplayError(f"event digest mismatch at line {expected}")
            if record.get("stage") == "admitted":
                self._validate_admission(record, previous_state)
            final = copy.deepcopy(record["state"])
            final["event_head_digest"] = digest
            previous_state = copy.deepcopy(final)
            previous_digest = digest
        if final is None:
            raise EvidenceReplayError("empty event log")
        projection = self.state_path(session_id)
        valid = (
            projection.exists()
            and _canon(json.loads(projection.read_text(encoding="utf-8")))
            == _canon(final)
        )
        if not valid:
            if not repair:
                raise EvidenceReplayError("state projection differs from event replay")
            self._write(projection, final)
        return final

    def unfinished(self) -> list[dict[str, Any]]:
        return [
            state
            for path in sorted(self.events.glob("*.jsonl"))
            if (state := self.replay(path.stem, True))["status"] in UNFINISHED
        ]
