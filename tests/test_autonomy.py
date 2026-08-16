from __future__ import annotations

import json
from pathlib import Path

from agi.autonomy import drive_autonomy, find_resumable_run


class FakeStore:
    def __init__(self, root: Path):
        self.root = root
        self.snapshots: dict[str, dict] = {}

    def snapshot(self, run_id: str) -> dict:
        return dict(self.snapshots[run_id])


class FakeEngine:
    def __init__(self, root: Path):
        self.root = root
        self.store = FakeStore(root)
        self.starts = 0
        self.resumes = 0

    def _persist(self, run_id: str, snapshot: dict) -> None:
        self.store.snapshots[run_id] = dict(snapshot)
        rd = self.root / ".continual" / "runs" / run_id
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")

    def start(self, request: str, *, max_steps: int = 64) -> str:
        self.starts += 1
        run_id = f"run-{self.starts}"
        self._persist(run_id, {"status": "continue", "phase": "root_pending", "created_at": f"0{self.starts}"})
        return run_id

    def resume(self, run_id: str, max_steps: int = 64):
        self.resumes += 1
        self._persist(run_id, {"status": "finished", "phase": "finished", "updated_at": f"1{self.resumes}"})
        return self.store.snapshot(run_id)


def test_driver_immediately_restarts_after_terminal_subtask(tmp_path: Path):
    engine = FakeEngine(tmp_path)
    result = drive_autonomy(tmp_path, cycles=4, max_steps=1, engine=engine)
    assert result.cycles_completed == 4
    assert engine.starts == 2
    assert engine.resumes == 2
    assert result.restarts == 1
    state = json.loads((tmp_path / "agi" / "AUTONOMY_STATE.json").read_text())
    assert state["status"] == "in_progress"
    assert state["active_run_id"] == result.active_run_id


def test_resumable_run_is_preferred_over_starting_new_one(tmp_path: Path):
    engine = FakeEngine(tmp_path)
    run_id = engine.start("x")
    result = drive_autonomy(tmp_path, cycles=1, max_steps=1, engine=engine)
    assert result.active_run_id == run_id
    assert engine.starts == 1
    assert engine.resumes == 1


def test_find_resumable_run_ignores_terminal_and_uses_latest(tmp_path: Path):
    runs = tmp_path / ".continual" / "runs"
    for name, status, stamp in [
        ("old", "continue", "2026-01-01T00:00:00Z"),
        ("done", "finished", "2026-03-01T00:00:00Z"),
        ("new", "continue", "2026-02-01T00:00:00Z"),
    ]:
        rd = runs / name
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "snapshot.json").write_text(json.dumps({"status": status, "updated_at": stamp}), encoding="utf-8")
    assert find_resumable_run(tmp_path) == "new"
