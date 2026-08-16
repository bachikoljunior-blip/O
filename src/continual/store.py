from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RevisionConflict(RuntimeError):
    """Raised when a snapshot writer is based on a stale revision."""


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.base = self.root / ".continual"

    def run_dir(self, run_id: str) -> Path:
        return self.base / "runs" / run_id

    def new_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def utc_now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def stable_digest(value: Any, length: int = 24) -> str:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:length]

    def atomic_bytes(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def atomic_text(self, path: Path, content: str) -> None:
        self.atomic_bytes(path, content.encode("utf-8"))

    def atomic_json(self, path: Path, value: Any) -> None:
        content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self.atomic_text(path, content)

    def read_json(self, path: Path, default: Any = None) -> Any:
        if not path.exists():
            return deepcopy(default)
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    def append_jsonl(self, path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def append_event(self, run_id: str, event: dict[str, Any]) -> None:
        record = dict(event)
        record.setdefault("at", self.utc_now())
        record.setdefault("run_id", run_id)
        self.append_jsonl(self.run_dir(run_id) / "events.jsonl", record)

    def snapshot(self, run_id: str) -> dict[str, Any]:
        value = self.read_json(self.run_dir(run_id) / "snapshot.json", {})
        if not isinstance(value, dict):
            raise ValueError(f"snapshot must be an object: {run_id}")
        return value

    def write_snapshot(self, run_id: str, value: dict[str, Any]) -> dict[str, Any]:
        old = self.snapshot(run_id)
        new_value = deepcopy(value)
        expected = new_value.pop("expected_revision", old.get("revision", 0))
        current = old.get("revision", 0)
        if expected != current:
            raise RevisionConflict(f"revision conflict: expected={expected}, current={current}")
        new_value["revision"] = current + 1
        new_value["updated_at"] = self.utc_now()
        self.atomic_json(self.run_dir(run_id) / "snapshot.json", new_value)
        return new_value
