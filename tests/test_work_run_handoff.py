"""Terminal handoff tests use real native lifecycle records and local Git fixtures.

The authority/CI connector receipts below are explicitly synthetic test inputs;
they are not production CI or independent evaluation evidence.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from continual.ci_source_observation import (
    prepare_ci_source_observation, record_ci_source_observation_receipt,
)
from continual.continuity_preflight import ContinuityPreflightError
from continual.store import Store
from continual.work_session import WorkSession, WorkSessionError, submit_work_response
from continual.work_source_observation import (
    prepare_work_source_observation, record_work_source_observation_receipt,
)
from test_continuity_preflight import _install_policy, _valid_preflight
from test_work_session_bridge import _output, _root


OLD = "run-handoff-predecessor"
NEW = "run-handoff-successor"
HANDOFF = "handoff-terminal-test"
STATE = "agi/WORK_EXECUTION_STATE.json"
CODE = (
    "src/continual/work_run_handoff.py",
    "src/continual/work_session.py",
    "src/continual/engine.py",
)


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def _blob(raw: bytes) -> str:
    return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()


def _native_bytes(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in (root / ".continual").rglob("*") if p.is_file()}


def _publish_authority(root: Path, state: dict, session: WorkSession) -> None:
    store = Store(root)
    store.atomic_json(root / STATE, state)
    _git(root, "add", STATE)
    _git(root, "commit", "-qm", "Synthetic test authority", "--allow-empty")
    main = _git(root, "rev-parse", "HEAD")
    _git(root, "update-ref", "refs/remotes/origin/main", main)
    blob = _blob((root / STATE).read_bytes())
    authority = prepare_work_source_observation(
        root, run_id=OLD, state=state, state_blob_sha=blob,
        expected_commit_sha=main, model_identity=session.model_identity,
    )
    record_work_source_observation_receipt(
        root, run_id=OLD, observation_id=authority["observation_id"],
        request_digest=authority["request_digest"],
        executor_binding=session.executor_binding, model_identity=session.model_identity,
        commit_sha=main, blob_sha=blob, observed_at=store.utc_now(),
        projection={**{k: state[k] for k in ("status", "owner_kind", "execution_id", "lease_generation", "heartbeat_at")},
                    "fence_token_digest": store.stable_digest(state["fence_token"], length=64)},
    )
    ci = prepare_ci_source_observation(root, run_id=OLD, state=state, model_identity=session.model_identity)
    policy = state["ci_source_observation_policy"]
    record_ci_source_observation_receipt(
        root, run_id=OLD, observation_id=ci["observation_id"],
        request_digest=ci["request_digest"], executor_binding=session.executor_binding,
        model_identity=session.model_identity, observed_at=store.utc_now(),
        workflow_run={"id": 12345, "workflow_id": 678, "name": "test", "status": "completed",
                      "conclusion": "success", "head_sha": policy["exact_head_sha"]},
        jobs=[{**job, "status": "completed", "conclusion": "success"} for job in policy["required_jobs"]],
    )


@pytest.fixture
def terminal(tmp_path: Path):
    root = _root(tmp_path)
    session, store = WorkSession(root), Store(root)
    result = session.start("Complete this bounded unit; retain the parent task.", run_id=OLD)
    answered = []
    for _ in range(12):
        if result["snapshot"]["status"] == "finished":
            break
        assert len(result["pending"]) == 1
        request = result["pending"][0]
        output = _output(request["component"], request)
        if request["component"] == "execute":
            output["result"].update(next_execution_unit={"component": "task_evaluate", "goal": "Evaluate only the bounded unit"},
                                     continuation={"goal": "Retain this uncompleted parent"})
        submit_work_response(root, request["invocation_id"], output,
                             executor_binding=session.executor_binding, model_identity=session.model_identity)
        answered.append(request["invocation_id"])
        result = session.resume(OLD)
    assert result["snapshot"]["status"] == "finished"
    snapshot = result["snapshot"]
    assert len(snapshot["continuation_stack"]) == 1
    rd = root / ".continual/runs" / OLD
    native = next(json.loads(p.read_text()) for p in (rd / "invocations").glob("*.json")
                  if json.loads(p.read_text())["component"] == "learn")
    assert native["status"] == "complete" and "work_invocation_id" not in native
    for ref in CODE:
        (root / ref).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(ref), root / ref)
    request_ref = "artifacts/terminal-successor-request.md"
    store.atomic_text(root / request_ref, "Continue the preserved parent request from its recorded source.\n")
    state = _install_policy(root, OLD)
    state.pop("termination")
    state.pop("refire")
    state.update(owner_kind="work_recovery_automation", heartbeat_at=store.utc_now(), stale_after_seconds=900)
    preflight = _valid_preflight(root, state, OLD)
    preflight.update(classification="no_discretionary_stop_detected", root_causes=[], remediations=[], validations=[])
    state["start_of_run_continuity_preflight"] = preflight
    store.atomic_json(root / ".continual/candidates/index.json", {"candidates": []})
    store.atomic_json(root / ".continual/system/active-components.json", {})
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Terminal handoff test")
    _git(root, "config", "user.email", "handoff-test@example.invalid")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "Genuine bounded native lifecycle with synthetic test inputs")
    source = _git(root, "rev-parse", "HEAD")
    refs = [f".continual/runs/{OLD}/snapshot.json", f".continual/runs/{OLD}/events.jsonl",
            f".continual/runs/{OLD}/invocations/{native['invocation_id']}.json",
            f".continual/runs/{OLD}/artifacts/post-task-learn.json",
            f".continual/episodes/{snapshot['episode_id']}/episode.json",
            f".continual/work-model/invocations/{answered[-1]}/request.json",
            f".continual/work-model/invocations/{answered[-1]}/response.json",
            ".continual/candidates/index.json", ".continual/system/active-components.json",
            "prompts/entry.md", "prompts/candidate_evaluate.md", request_ref, *CODE]
    refs.extend(f".continual/runs/{OLD}/{p['result_ref']}" for p in snapshot["continuation_stack"])
    manifest = {ref: _blob((root / ref).read_bytes()) for ref in refs}
    state["exact_continuation"] = {
        "pending_work_invocation_id": None, "run_snapshot_ref": refs[0],
        "snapshot_revision": snapshot["revision"], "snapshot_blob_sha": manifest[refs[0]],
        "snapshot_head_sha": source, "snapshot_branch": "main", "native_phase": "finished",
    }
    state["primary_native_run"] = {"run_id": OLD, "answered_invocations": answered}
    state["terminal_run_handoff"] = {
        "schema_version": 1, "status": "ready", "continuation_required": True,
        "handoff_id": HANDOFF, "predecessor_run_id": OLD, "successor_run_id": NEW,
        "execution_id": state["execution_id"], "lease_generation": state["lease_generation"],
        "fence_token_digest": hashlib.sha256(state["fence_token"].encode()).hexdigest(),
        "executor_binding": session.executor_binding, "model_identity": session.model_identity,
        "source_main_sha": source, "source_files": manifest,
        "terminal_native_invocation_id": native["invocation_id"], "request_ref": request_ref,
        "request_sha256": hashlib.sha256((root / request_ref).read_bytes()).hexdigest(),
    }
    state["authoritative_source_observation_policy"] = {
        "required": True, "repository_full_name": "owner/repo", "ref": "main",
        "max_age_seconds": 300, "executor_binding": session.executor_binding,
    }
    state["ci_source_observation_policy"] = {
        "required": True, "repository_full_name": "owner/repo", "exact_head_sha": source,
        "workflow_run_id": 12345, "workflow_id": 678, "max_age_seconds": 300,
        "executor_binding": session.executor_binding,
        "required_jobs": [{"id": i + 11, "name": f"pytest shard {i} of 4"} for i in range(4)] + [{"id": 15, "name": "test"}],
    }
    _publish_authority(root, state, session)
    return root, state, session


def test_terminal_handoff_preserves_parents_and_retries_without_consuming(terminal):
    root, state, session = terminal
    before, authority = _native_bytes(root), (root / STATE).read_bytes()
    result = session.start_continuation(OLD)
    assert result["run_id"] == NEW and result["replayed"] is False
    assert result["snapshot"]["revision"] == 0 and len(result["pending"]) == 1
    request = result["pending"][0]
    assert request["component"] == "entry"
    inherited = request["payload"]["inherited_continuation"]
    assert inherited["parents"] == Store(root).snapshot(OLD)["continuation_stack"]
    assert inherited["source"]["source_run_id"] == OLD
    for binding in inherited["source"]["parent_result_sources"]:
        assert _blob((root / binding["repository_path"]).read_bytes()) == binding["git_blob_sha"]
    assert all((root / ref).read_bytes() == raw for ref, raw in before.items())
    after = _native_bytes(root)
    retried = session.start_continuation(OLD)
    assert retried["replayed"] is True and retried["handoff"] == result["handoff"]
    assert _native_bytes(root) == after
    submit_work_response(root, request["invocation_id"], _output("entry", request),
                         executor_binding=session.executor_binding, model_identity=session.model_identity)
    answered = _native_bytes(root)
    assert session.start_continuation(OLD)["pending"] == []
    assert _native_bytes(root) == answered and (root / STATE).read_bytes() == authority
    with pytest.raises(ContinuityPreflightError, match="native run mismatch"):
        session.resume(NEW)


@pytest.mark.parametrize("damage", ["fence", "generation", "model", "plan_status", "no_work_receipt", "no_ci_receipt", "stale_receipts", "unacknowledged_inbox", "changed_source", "missing_parent", "existing_successor", "missing_manifest"])
def test_invalid_handoff_fails_before_any_native_effect(terminal, damage):
    root, state, session = terminal
    plan = state["terminal_run_handoff"]
    if damage in {"fence", "generation", "model", "plan_status"}:
        key, value = {"fence": ("fence_token_digest", "0" * 64), "generation": ("lease_generation", 999),
                      "model": ("model_identity", "another-model"), "plan_status": ("status", "publication_pending")}[damage]
        plan[key] = value
        _publish_authority(root, state, session)
    elif damage in {"no_work_receipt", "no_ci_receipt", "stale_receipts"}:
        category = "work-source-observations" if damage == "no_work_receipt" else "ci-source-observations"
        for path in (root / ".continual/runs" / OLD / category).glob("*/receipt.json"):
            if damage == "stale_receipts":
                receipt = json.loads(path.read_text())
                receipt["observed_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
                Store(root).atomic_json(path, receipt)
            else:
                path.unlink()
    elif damage == "unacknowledged_inbox":
        state["user_input_inbox"]["highest_acknowledged_revision"] -= 1
        _publish_authority(root, state, session)
    elif damage == "changed_source":
        (root / plan["request_ref"]).write_text("unpublished replacement")
    elif damage in {"missing_parent", "missing_manifest"}:
        ref = next(ref for ref in plan["source_files"] if ref.endswith("-result.json")) if damage == "missing_parent" else CODE[0]
        plan["source_files"].pop(ref)
        _publish_authority(root, state, session)
    else:
        (root / ".continual/runs" / NEW).mkdir()
    before, authority = _native_bytes(root), (root / STATE).read_bytes()
    with pytest.raises(WorkSessionError):
        session.start_continuation(OLD)
    assert _native_bytes(root) == before and (root / STATE).read_bytes() == authority
    assert not (root / ".continual/work-handoffs" / HANDOFF).exists()


@pytest.mark.parametrize("conflict", [False, True])
def test_interrupted_initialization_repairs_only_unambiguous_intent(terminal, monkeypatch, conflict):
    root, state, session = terminal
    from continual.engine import Engine

    original = Engine.start

    def interrupt(engine, request, *, run_id, **kwargs):
        rd = root / ".continual/runs" / run_id
        rd.mkdir()
        if conflict:
            (rd / "snapshot.json").write_text('{"unexpected": true}\n')
        raise OSError("simulated interruption after directory creation")

    monkeypatch.setattr(Engine, "start", interrupt)
    with pytest.raises(WorkSessionError):
        session.start_continuation(OLD)
    monkeypatch.setattr(Engine, "start", original)
    before = _native_bytes(root)
    if conflict:
        with pytest.raises(WorkSessionError, match="bootstrap bytes changed"):
            session.start_continuation(OLD)
        assert _native_bytes(root) == before
        assert not (root / ".continual/runs" / NEW / "request.md").exists()
    else:
        result = session.start_continuation(OLD)
        assert result["replayed"] is True and len(result["pending"]) == 1
        assert session.start_continuation(OLD)["handoff"] == result["handoff"]


def _republish_sources(root, state, session):
    """Create a new locally committed test source after a deliberate mutation."""
    plan = state["terminal_run_handoff"]
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "Synthetic source variation")
    source = _git(root, "rev-parse", "HEAD")
    plan["source_main_sha"] = source
    plan["source_files"] = {ref: _blob((root / ref).read_bytes()) for ref in plan["source_files"]}
    state["ci_source_observation_policy"]["exact_head_sha"] = source
    state["exact_continuation"]["snapshot_head_sha"] = source
    state["exact_continuation"]["snapshot_blob_sha"] = plan["source_files"][state["exact_continuation"]["run_snapshot_ref"]]
    _publish_authority(root, state, session)


@pytest.mark.parametrize("damage", ["unfinished", "duplicate_finish", "different_learn_result", "runner_candidate"])
def test_published_but_nonterminal_or_inconsistent_source_is_rejected(terminal, damage):
    root, state, session = terminal
    rd = root / ".continual/runs" / OLD
    store = Store(root)
    if damage == "unfinished":
        snapshot = store.snapshot(OLD)
        snapshot.update(status="continue", phase="post_task_learn_pending")
        store.atomic_json(rd / "snapshot.json", snapshot)
    elif damage == "duplicate_finish":
        store.append_event(OLD, {"type": "run_finished", "episode_id": store.snapshot(OLD)["episode_id"]})
    elif damage == "different_learn_result":
        store.atomic_json(rd / "artifacts/post-task-learn.json", {"decision": "a replacement"})
    else:
        store.atomic_json(root / ".continual/candidates/index.json", {"candidates": [{"candidate_id": "runner-test", "target_component": "runner"}]})
    _republish_sources(root, state, session)
    before = _native_bytes(root)
    with pytest.raises(WorkSessionError):
        session.start_continuation(OLD)
    assert _native_bytes(root) == before


def test_entry_candidate_remains_the_first_unconsumed_native_boundary(terminal):
    root, state, session = terminal
    Store(root).atomic_json(root / ".continual/candidates/index.json", {
        "candidates": [{"candidate_id": "entry-test", "target_component": "entry", "expected_scope": "test"}],
    })
    _republish_sources(root, state, session)
    result = session.start_continuation(OLD)
    request = result["pending"][0]
    assert request["component"] == "candidate_evaluate"
    assert request["payload"]["target_component"] == "entry"
    assert request["payload"]["execution_unit"]["inherited_continuation"]["parents"] == Store(root).snapshot(OLD)["continuation_stack"]
    after = _native_bytes(root)
    assert session.start_continuation(OLD)["handoff"] == result["handoff"]
    assert _native_bytes(root) == after
