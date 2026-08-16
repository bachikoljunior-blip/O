from pathlib import Path

from continual.store import Store


def test_atomic_snapshot_and_revision(tmp_path: Path):
    s = Store(tmp_path)
    run_id = "run-test"
    rd = s.run_dir(run_id)
    rd.mkdir(parents=True)
    s.atomic_json(rd / "snapshot.json", {"revision": 0, "status": "continue"})
    snap = s.snapshot(run_id)
    snap.update({"status": "finished", "expected_revision": 0})
    s.write_snapshot(run_id, snap)
    assert s.snapshot(run_id)["revision"] == 1
    assert s.snapshot(run_id)["status"] == "finished"


def test_append_event(tmp_path: Path):
    s = Store(tmp_path)
    s.append_event("run-test", {"type": "x"})
    text = (s.run_dir("run-test") / "events.jsonl").read_text()
    assert '"type": "x"' in text
