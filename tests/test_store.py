from __future__ import annotations

from pathlib import Path

import pytest

from continual.store import RevisionConflict, Store


def test_snapshot_revision_is_atomic_and_does_not_mutate_input(tmp_path: Path):
    store = Store(tmp_path)
    run_id = "run-test"
    run_dir = store.run_dir(run_id)
    run_dir.mkdir(parents=True)
    store.atomic_json(run_dir / "snapshot.json", {"revision": 0, "status": "continue"})

    supplied = {"revision": 0, "status": "finished", "expected_revision": 0}
    written = store.write_snapshot(run_id, supplied)

    assert supplied["expected_revision"] == 0
    assert supplied["revision"] == 0
    assert written["revision"] == 1
    assert store.snapshot(run_id)["status"] == "finished"


def test_stale_snapshot_is_rejected(tmp_path: Path):
    store = Store(tmp_path)
    run_id = "run-test"
    run_dir = store.run_dir(run_id)
    run_dir.mkdir(parents=True)
    store.atomic_json(run_dir / "snapshot.json", {"revision": 2, "status": "continue"})
    with pytest.raises(RevisionConflict):
        store.write_snapshot(run_id, {"status": "finished", "expected_revision": 1})


def test_append_event_adds_run_and_timestamp(tmp_path: Path):
    store = Store(tmp_path)
    store.append_event("run-test", {"type": "x"})
    text = (store.run_dir("run-test") / "events.jsonl").read_text(encoding="utf-8")
    assert '"type": "x"' in text
    assert '"run_id": "run-test"' in text
    assert '"at":' in text
