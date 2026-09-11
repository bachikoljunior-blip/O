"""Explicit, fenced startup after a fully published terminal Work Run."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .ci_source_observation import verify_ci_source_observation
from .context_kernel import validate_mandatory_work_source_freshness
from .continuity_preflight import assert_work_resume_continuity_preflight
from .store import Store
from .work_checkpoint_integrity import verify_work_checkpoint_integrity
from .work_source_observation import verify_work_source_observation

if TYPE_CHECKING:
    from .work_session import WorkSession

_RUN = re.compile(r"run-[A-Za-z0-9._-]{6,128}\Z")
_INVOCATION = re.compile(r"invoke-[0-9a-f]{24}\Z")
_HANDOFF = re.compile(r"handoff-[A-Za-z0-9._-]{6,128}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_STATE = "agi/WORK_EXECUTION_STATE.json"
_CODE = (
    "src/continual/work_run_handoff.py",
    "src/continual/work_session.py",
    "src/continual/engine.py",
)


def _require(condition: Any, message: str) -> None:
    if not condition:
        from .work_session import WorkSessionError
        raise WorkSessionError(message)


def _path(root: Path, ref: Any) -> Path:
    _require(isinstance(ref, str) and ref and not Path(ref).is_absolute(), "invalid handoff reference")
    _require(".." not in Path(ref).parts, "handoff reference escapes repository")
    path = root / ref
    _require(path.resolve() == path and path.resolve().is_relative_to(root), "unsafe handoff reference")
    return path


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(value, dict), "handoff source must be an object")
    return value


def _blob(raw: bytes) -> str:
    return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True, timeout=15)
    _require(result.returncode == 0, "handoff Git source verification failed")
    return result.stdout.strip()


def _source_blob(root: Path, commit: str, ref: str) -> str:
    _path(root, ref)
    result = _git(root, "ls-tree", "-z", commit, "--", ref)
    entries = [x for x in result.split("\0") if x]
    _require(len(entries) == 1, "handoff source reference is absent")
    metadata, name = entries[0].split("\t", 1)
    mode, kind, sha = metadata.split()
    _require(mode in {"100644", "100755"} and kind == "blob" and name == ref, "invalid handoff source tree entry")
    return sha


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def start_terminal_continuation(session: WorkSession, predecessor_run_id: str) -> dict[str, Any]:
    """Initialize only the first frozen successor request; never consume it.

    The operator publishes a fixed ``terminal_run_handoff`` plan in the same
    authoritative state as the verified finished predecessor. Live source and CI
    receipts are required again at use time. The method does not write that state.
    """
    from .work_session import (
        WorkSessionError, pending_work_invocations, verified_work_invocation,
        verified_work_request, verify_work_invocations,
    )

    root, store = session.root, session.store
    try:
        _require(isinstance(predecessor_run_id, str) and _RUN.fullmatch(predecessor_run_id), "invalid predecessor run id")
        state_raw = (root / _STATE).read_bytes()
        state = json.loads(state_raw)
        _require(isinstance(state, dict), "authoritative Work state must be an object")
        now = store.utc_now()
        validate_mandatory_work_source_freshness(state, observed_at=now)
        _require(state.get("active_run_id") == predecessor_run_id, "handoff predecessor is not the authoritative run")
        plan = state.get("terminal_run_handoff")
        _require(isinstance(plan, dict) and plan.get("schema_version") == 1, "a durable terminal handoff plan is required")
        _require(plan.get("status") == "ready" and plan.get("continuation_required") is True, "terminal handoff is not authorized")
        for key in ("execution_id", "lease_generation"):
            _require(plan.get(key) == state.get(key), "terminal handoff authority mismatch")
        _require(plan.get("fence_token_digest") == hashlib.sha256(state["fence_token"].encode()).hexdigest(), "terminal handoff fence mismatch")
        _require(plan.get("executor_binding") == session.executor_binding and plan.get("model_identity") == session.model_identity, "terminal handoff executor mismatch")
        _require(plan.get("predecessor_run_id") == predecessor_run_id, "terminal handoff predecessor mismatch")
        successor = plan.get("successor_run_id")
        handoff_id = plan.get("handoff_id")
        _require(isinstance(successor, str) and _RUN.fullmatch(successor) and successor != predecessor_run_id, "invalid successor run id")
        _require(isinstance(handoff_id, str) and _HANDOFF.fullmatch(handoff_id), "invalid handoff id")
        source = plan.get("source_main_sha")
        _require(isinstance(source, str) and _SHA.fullmatch(source), "invalid handoff source commit")
        refs = plan.get("source_files")
        _require(isinstance(refs, dict) and 0 < len(refs) <= 256, "invalid handoff source manifest")
        for ref, sha in refs.items():
            _require(isinstance(sha, str) and _SHA.fullmatch(sha), "invalid handoff source blob")
            _require(_blob(_path(root, ref).read_bytes()) == sha == _source_blob(root, source, ref), "handoff source bytes changed")
        _git(root, "merge-base", "--is-ancestor", source, "refs/remotes/origin/main")
        _require(_source_blob(root, "refs/remotes/origin/main", _STATE) == _blob(state_raw), "local handoff authority is not the observed main state")
        assert_work_resume_continuity_preflight(root, run_id=predecessor_run_id, executor_binding=session.executor_binding, model_identity=session.model_identity)
        authority = verify_work_source_observation(root, run_id=predecessor_run_id, state=state, state_blob_sha=_blob(state_raw), now=now)
        ci = verify_ci_source_observation(root, run_id=predecessor_run_id, state=state, now=now)
        _require(authority is not None and ci is not None, "terminal handoff requires current authority and CI observations")
        _require(authority["source_version"]["commit_sha"] == _git(root, "rev-parse", "refs/remotes/origin/main"), "handoff authority observation is not the current fetched main")
        ci_head = state["ci_source_observation_policy"]["exact_head_sha"]
        _git(root, "merge-base", "--is-ancestor", ci_head, "refs/remotes/origin/main")
        for ref in _CODE:
            _require(ref in refs and _source_blob(root, ci_head, ref) == refs[ref], "handoff runtime lacks exact-head CI coverage")

        rd = _path(root, f".continual/runs/{predecessor_run_id}")
        snapshot_ref = f".continual/runs/{predecessor_run_id}/snapshot.json"
        snapshot = _read(rd / "snapshot.json")
        _require(snapshot.get("run_id") == predecessor_run_id and snapshot.get("status") == snapshot.get("phase") == "finished", "handoff requires a genuinely finished predecessor")
        _require(snapshot.get("task_completion_verdict") == "PASS", "terminal task completion is not established")
        exact = state.get("exact_continuation", {})
        _require(exact.get("pending_work_invocation_id") is None and exact.get("run_snapshot_ref") == snapshot_ref, "terminal handoff cannot replace a pending continuation")
        _require(exact.get("snapshot_revision") == snapshot.get("revision") and exact.get("snapshot_blob_sha") == refs.get(snapshot_ref), "terminal snapshot binding mismatch")
        native_id = plan.get("terminal_native_invocation_id")
        _require(isinstance(native_id, str) and _INVOCATION.fullmatch(native_id), "invalid terminal Learn identity")
        native_ref = f".continual/runs/{predecessor_run_id}/invocations/{native_id}.json"
        native = _read(root / native_ref)
        _require(native.get("status") == "complete" and native.get("component") == "learn", "terminal Learn has not completed")
        events_ref = f".continual/runs/{predecessor_run_id}/events.jsonl"
        events = [json.loads(line) for line in (root / events_ref).read_text().splitlines()]
        pending_events = [i for i, e in enumerate(events) if e.get("type") == "work_model_pending" and e.get("invocation_id") == native_id]
        _require(len(pending_events) == 1, "terminal Learn lacks a unique frozen Work event")
        work_id = events[pending_events[0]].get("work_invocation_id")
        _require(isinstance(work_id, str) and _INVOCATION.fullmatch(work_id), "terminal Learn lacks its Work identity")
        work = verified_work_invocation(root, work_id)
        _require(work["request"]["run_id"] == predecessor_run_id and work["request"]["component"] == "learn" and native.get("output") == work["output"], "terminal Learn output binding mismatch")
        _require(work["request"]["executor_binding"] == session.executor_binding and work["request"]["model_identity"] == session.model_identity, "terminal Learn executor mismatch")
        finishes = [i for i, e in enumerate(events) if e.get("type") == "run_finished"]
        completions = [i for i, e in enumerate(events) if e.get("type") == "invocation_completed" and e.get("invocation_id") == native_id]
        _require(len(finishes) == len(completions) == 1 and pending_events[0] < completions[0] < finishes[0], "terminal completion event binding mismatch")
        _require(not any(e.get("type") in {"invocation_started", "invocation_completed", "phase_error"} for e in events[completions[0]+1:finishes[0]]), "selected Learn is not the final semantic completion")
        _require(events[finishes[0]].get("episode_id") == snapshot.get("episode_id"), "terminal Episode mismatch")
        _require(not any(e.get("type") in {"invocation_started", "invocation_completed", "phase_error"} for e in events[finishes[0]+1:]), "native progress follows the terminal event")
        _require(not any(_read(p).get("status") == "awaiting_work_model" for p in (rd / "invocations").glob("*.json")), "terminal predecessor retains a pending native invocation")
        verification = verify_work_invocations(root, run_id=predecessor_run_id)
        checkpoint = verify_work_checkpoint_integrity(root, state=state)
        _require(verification["valid"] and checkpoint["valid"], "terminal predecessor integrity failed")
        episode_ref = f".continual/episodes/{snapshot['episode_id']}/episode.json"
        _path(root, episode_ref)
        request_ref = plan.get("request_ref")
        request = _path(root, request_ref).read_text(encoding="utf-8")
        _require(request.strip() and hashlib.sha256(request.encode()).hexdigest() == plan.get("request_sha256"), "fixed successor request mismatch")
        parents = snapshot.get("continuation_stack")
        _require(isinstance(parents, list) and all(isinstance(p, dict) for p in parents), "invalid inherited parent stack")
        parent_sources = []
        for parent in parents:
            relative = parent.get("result_ref")
            _require(isinstance(relative, str) and not Path(relative).is_absolute() and ".." not in Path(relative).parts, "invalid parent result reference")
            ref = f".continual/runs/{predecessor_run_id}/{relative}"
            _require(ref in refs, "parent result is not bound to published source")
            parent_sources.append({"parent_unit_id": parent.get("parent_unit_id"), "result_ref": relative, "repository_path": ref, "git_blob_sha": refs[ref]})
        index_ref = ".continual/candidates/index.json"
        required = {snapshot_ref, native_ref, events_ref, episode_ref, request_ref, index_ref, *(_CODE), f".continual/work-model/invocations/{work_id}/request.json", f".continual/work-model/invocations/{work_id}/response.json", f".continual/runs/{predecessor_run_id}/artifacts/post-task-learn.json"}
        _require(required.issubset(refs), "terminal handoff manifest omits required sources")
        _require(_read(rd / "artifacts/post-task-learn.json") == work["output"]["result"], "terminal learning artifact mismatch")
        index = _read(root / index_ref)
        candidates = index.get("candidates")
        _require(isinstance(candidates, list) and all(isinstance(c, dict) for c in candidates), "invalid startup Candidate index")
        _require(not any(c.get("target_component") in {"runner", "*"} for c in candidates), "terminal handoff cannot skip a runner Candidate preflight")
        engine = session._engine(successor)
        startup_refs = {".continual/system/active-components.json", engine._component_prompt_path("entry")}
        if any(c.get("target_component") == "entry" for c in candidates):
            startup_refs.add(engine._component_prompt_path("candidate_evaluate"))
        for ref in startup_refs:
            _require(ref in refs and _source_blob(root, ci_head, ref) == refs[ref], "startup prompt configuration lacks published CI coverage")

        # All source, policy and authority checks precede even the local intent.
        intent_path = _path(root, f".continual/work-handoffs/{handoff_id}/intent.json")
        successor_dir = _path(root, f".continual/runs/{successor}")
        plan_digest = store.stable_digest(plan, length=64)
        source_context = {"source_run_id": predecessor_run_id, "source_main_sha": source, "snapshot_ref": snapshot_ref, "snapshot_blob_sha": refs[snapshot_ref], "episode_ref": episode_ref, "handoff_id": handoff_id, "parent_result_sources": parent_sources}
        if intent_path.exists():
            intent = _read(intent_path)
            body = {k: v for k, v in intent.items() if k != "intent_digest"}
            _require(intent.get("intent_digest") == store.stable_digest(body, length=64) and intent.get("plan_digest") == plan_digest, "immutable handoff intent conflict")
        else:
            _require(not successor_dir.exists(), "successor exists without this handoff intent")
            initial = {"run_id": successor, "status": "continue", "phase": "entry_pending", "revision": 0, "created_at": now, "environment": engine.environment(), "continuation_stack": deepcopy(parents), "continuation_source": source_context, "error_count": 0}
            intent = {"schema_version": 1, "plan_digest": plan_digest, "predecessor_run_id": predecessor_run_id, "successor_run_id": successor, "request": request, "initial_snapshot": initial, "run_started_event": {"type": "run_started", "run_id": successor, "at": now}}
            intent["intent_digest"] = store.stable_digest(intent, length=64)
            store.atomic_json(intent_path, intent)
        _require(intent.get("request") == request and intent.get("successor_run_id") == successor and intent.get("initial_snapshot", {}).get("continuation_stack") == parents, "handoff intent source conflict")
        _require(intent["initial_snapshot"].get("continuation_source") == source_context, "handoff intent namespace conflict")
        replayed = successor_dir.exists()
        if not replayed:
            engine.start(request, run_id=successor, max_steps=1, bootstrap_snapshot=intent["initial_snapshot"])
        else:
            # An interrupted initialization is recoverable only from the exact
            # recorded mechanical bytes, before any native invocation exists.
            journal_paths = list((successor_dir / "invocations").glob("*.json"))
            expected = {"request.md": request.encode(), "snapshot.json": _json_bytes(intent["initial_snapshot"])}
            repairs = []
            for name, raw in expected.items():
                path = successor_dir / name
                if path.exists():
                    _require(path.read_bytes() == raw, "successor bootstrap bytes changed")
                else:
                    _require(not journal_paths, "ambiguous partial native bootstrap")
                    repairs.append((path, raw))
            event_path = successor_dir / "events.jsonl"
            if not event_path.exists():
                _require(not journal_paths, "native bootstrap lacks its initialization event")
                new_events = [intent["run_started_event"]]
            else:
                new_events = [json.loads(line) for line in event_path.read_text().splitlines()]
            _require(new_events and new_events[0] == intent["run_started_event"] and sum(e.get("type") == "run_started" for e in new_events) == 1, "successor initialization event conflict")
            if not journal_paths:
                _require(len(new_events) == 1, "ambiguous partial startup history")
                orphan_work = verify_work_invocations(root, run_id=successor)
                _require(orphan_work["valid"] and orphan_work["requests"] == 0, "unbound successor Work request requires integrity repair")
                for path, raw in repairs:
                    store.atomic_text(path, raw.decode())
                if not event_path.exists():
                    store.append_event(successor, intent["run_started_event"])
                engine.resume_start(successor, max_steps=1)

        new_snapshot = store.snapshot(successor)
        _require(new_snapshot == intent["initial_snapshot"], "handoff must not advance the successor snapshot")
        journals = [_read(p) for p in (successor_dir / "invocations").glob("*.json")]
        _require(len(journals) == 1 and journals[0].get("status") == "awaiting_work_model", "handoff requires exactly one frozen successor invocation")
        session._assert_resume_identity(successor)
        new_request = verified_work_request(root, journals[0]["work_invocation_id"])
        _require(new_request["component"] == "entry" or (new_request["component"] == "candidate_evaluate" and new_request.get("payload", {}).get("target_component") == "entry"), "successor did not freeze the initial Entry boundary")
        _require(verify_work_invocations(root, run_id=successor)["valid"], "successor Work integrity failed")
        receipt_path = intent_path.parent / "receipt.json"
        receipt = {"schema_version": 1, "handoff_id": handoff_id, "intent_digest": intent["intent_digest"], "source_main_sha": source, "predecessor_run_id": predecessor_run_id, "successor_run_id": successor, "pending_work_invocation_id": new_request["invocation_id"], "pending_request_digest": new_request["request_digest"], "pending_native_invocation_id": journals[0]["invocation_id"], "parent_count": len(parents), "parents_preserved": True, "response_consumed": False, "authoritative_state_changed": False}
        if receipt_path.exists():
            _require(_read(receipt_path) == receipt, "immutable handoff receipt conflict")
        else:
            store.atomic_json(receipt_path, receipt)
        _require((root / _STATE).read_bytes() == state_raw, "authority changed during local handoff")
        return {"run_id": successor, "snapshot": new_snapshot, "pending": pending_work_invocations(root, run_id=successor), "handoff": receipt, "replayed": replayed}
    except WorkSessionError:
        raise
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        raise WorkSessionError(f"terminal handoff failed closed: {type(exc).__name__}") from exc
