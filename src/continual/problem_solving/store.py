"""Durable event log and repairable projections."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .model import EvidenceReplayError, UNFINISHED, _canon, _hash, _id, _now


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

    def replay(self, session_id: str, repair: bool = False) -> dict[str, Any]:
        path = self.event_path(session_id)
        if not path.exists():
            raise EvidenceReplayError(f"missing event log: {session_id}")
        previous: str | None = None
        final: dict[str, Any] | None = None
        for expected, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            record = json.loads(line)
            digest = record.pop("event_digest", None)
            if record.get("sequence") != expected or record.get("previous_event_digest") != previous:
                raise EvidenceReplayError(
                    f"event sequence/hash predecessor mismatch at line {expected}"
                )
            if digest != _hash(record):
                raise EvidenceReplayError(f"event digest mismatch at line {expected}")
            final = copy.deepcopy(record["state"])
            final["event_head_digest"] = digest
            previous = digest
        if final is None:
            raise EvidenceReplayError("empty event log")
        projection = self.state_path(session_id)
        valid = (
            projection.exists()
            and _canon(json.loads(projection.read_text(encoding="utf-8"))) == _canon(final)
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
