from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import continual.engine as engine_module
from continual.engine import Engine, WorkProviderMismatch, _SAFE_UNIT_ID
from continual.work_session import WorkSession

from test_engine import FakeModelClient, make_engine


def _work_root(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[1]
    shutil.copytree(source / "prompts", tmp_path / "prompts")
    shutil.copytree(source / ".continual" / "system", tmp_path / ".continual" / "system")
    (tmp_path / ".continual" / "candidates").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".continual" / "candidates" / "index.json").write_text(
        '{"schema_version": 2, "candidates": []}\n', encoding="utf-8"
    )
    return tmp_path


def _register_execute_candidate(engine: Engine, runtime_repo: Path) -> None:
    cdir = runtime_repo / ".continual" / "candidates" / "candidate-execute"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "prompt.md").write_text("EXECUTE OVERLAY", encoding="utf-8")
    candidate = {
        "candidate_id": "candidate-execute",
        "target_component": "execute",
        "prompt_path": ".continual/candidates/candidate-execute/prompt.md",
        "prompt_mode": "overlay",
        "status": "candidate",
    }
    engine.store.atomic_json(cdir / "candidate.json", candidate)
    engine.store.atomic_json(
        engine.candidate_index_path,
        {"schema_version": 2, "candidates": [candidate]},
    )


def test_schedule_unit_rejects_traversal_unit_id(runtime_repo: Path, monkeypatch):
    engine = make_engine(runtime_repo, monkeypatch)
    run_id = engine.store.new_id("run")
    run_dir = engine.store.run_dir(run_id)
    run_dir.mkdir(parents=True)
    snapshot = {"run_id": run_id, "revision": 0, "status": "continue", "continuation_stack": []}
    engine.store.atomic_json(run_dir / "snapshot.json", snapshot)

    active_path = runtime_repo / ".continual" / "system" / "active-components.json"
    active_before = active_path.read_text(encoding="utf-8")

    malicious = {
        "component": "execute",
        "goal": "overwrite the active-prompt pointer",
        "unit_id": "../../../system/active-components",
    }
    engine._schedule_unit(run_id, snapshot, malicious)

    # The protected active-components pointer was not overwritten by the unit dict.
    assert active_path.read_text(encoding="utf-8") == active_before

    # The unit was written under a safe derived id inside the run's execution-units dir.
    written = list((run_dir / "execution-units").glob("*.json"))
    assert written, "unit file should have been written"
    for path in written:
        assert ".." not in path.name
        stem = path.stem
        assert _SAFE_UNIT_ID.fullmatch(stem)
        stored = json.loads(path.read_text(encoding="utf-8"))
        assert _SAFE_UNIT_ID.fullmatch(stored["unit_id"])


def test_safe_unit_id_pattern_rejects_separators_and_traversal():
    assert _SAFE_UNIT_ID.fullmatch("unit-6ca357218da14d4c3243d3c3")
    assert _SAFE_UNIT_ID.fullmatch("unit-abc_DEF-123")
    for bad in ("../escape", "a/b", "..", "unit/../x", "unit.with.dots", ""):
        assert _SAFE_UNIT_ID.fullmatch(bad) is None


def test_preflight_identity_is_stable_across_environment_change(runtime_repo: Path, monkeypatch):
    engine = make_engine(runtime_repo, monkeypatch)
    _register_execute_candidate(engine, runtime_repo)

    run_id = engine.store.new_id("run")
    run_dir = engine.store.run_dir(run_id)
    run_dir.mkdir(parents=True)
    frozen_env = {"model": "fake-test-model", "repository_commit": "frozen-at-start"}
    engine.store.atomic_json(
        run_dir / "snapshot.json",
        {"run_id": run_id, "revision": 0, "status": "continue", "environment": frozen_env},
    )

    live = {"commit": "head-1"}
    monkeypatch.setattr(
        engine,
        "environment",
        lambda: {"model": "fake-test-model", "repository_commit": live["commit"]},
    )

    unit = {"component": "execute", "goal": "do the unit", "scope": "s", "unit_id": "unit-x"}
    first = engine._preflight(run_id, "execute", unit)

    # Simulate HEAD advancing (e.g. a commit landed) between resume steps.
    live["commit"] = "head-2"
    second = engine._preflight(run_id, "execute", unit)

    assert first == second
    # candidate_evaluate must have been called exactly once: the second preflight
    # replays the frozen result rather than minting a new invocation.
    assert [name for name, _ in engine.model.calls].count("candidate_evaluate") == 1


def test_default_provider_refuses_awaiting_work_model_invocation(tmp_path: Path):
    root = _work_root(tmp_path)
    session = WorkSession(root)
    session.start("freeze exactly one Work request", run_id="run-guard-test")

    inv_dir = root / ".continual" / "runs" / "run-guard-test" / "invocations"
    journal_path = next(inv_dir.glob("*.json"))
    before = json.loads(journal_path.read_text(encoding="utf-8"))
    assert before["status"] == "awaiting_work_model"

    fake = FakeModelClient(root)
    assert not getattr(fake, "provides_work_responses", False)
    engine = Engine(root, model=fake)

    result = engine.resume("run-guard-test")

    # The non-Work provider neither fabricated a response nor advanced the run.
    assert fake.calls == []
    assert result["phase"] == "entry_pending"
    after = json.loads(journal_path.read_text(encoding="utf-8"))
    assert after["status"] == "awaiting_work_model"
    response_files = list(inv_dir.parent.glob("**/response.json"))
    assert response_files == []


def test_work_client_marks_itself_as_work_provider():
    from continual.work_session import WorkModelClient

    assert WorkModelClient.provides_work_responses is True
