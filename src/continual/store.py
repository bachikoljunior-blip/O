from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.base = root / ".continual"

    def run_dir(self, run_id: str) -> Path:
        return self.base / "runs" / run_id

    def new_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:12]}"

    def atomic_json(self, path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def read_json(self, path: Path, default: Any = None) -> Any:
        if not path.exists():
            return default
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    def append_event(self, run_id: str, event: dict[str, Any]) -> None:
        path = self.run_dir(run_id) / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def snapshot(self, run_id: str) -> dict[str, Any]:
        return self.read_json(self.run_dir(run_id) / "snapshot.json", {})

    def write_snapshot(self, run_id: str, value: dict[str, Any]) -> None:
        old = self.snapshot(run_id)
        expected = value.pop("expected_revision", old.get("revision", 0))
        current = old.get("revision", 0)
        if expected != current:
            raise RuntimeError(f"revision conflict: expected={expected}, current={current}")
        value["revision"] = current + 1
        self.atomic_json(self.run_dir(run_id) / "snapshot.json", value)
