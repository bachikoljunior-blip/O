from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from continual.continuity_preflight import (
    ContinuityPreflightError,
    assert_work_resume_continuity_preflight,
    classify_prior_stop,
)
from continual.work_session import WorkSession, submit_work_response


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_blob_digest(path: Path) -> str:
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def _root(tmp_path: Path) -> Path:
    shutil.copytree(Path("prompts"), tmp_path / "prompts")
    return tmp_path


def _entry_output(request: dict) -> dict:
    return {
        "result": {"objective": "test causal resume preflight"},
        "local_learn": {"decision": "NO_CHANGE", "candidates": []},
        "fragment": {"component": "entry", "observations": ["entry completed"]},
    }


def _install_policy(root: Path, run_id: str) -> dict:
    inbox = {"schema_version": 1, "revision": 29, "entries": []}
    ledger = {
        "schema_version": 1,
        "source": {"revision": 29},
        "atoms": [
            {"atom_id": "r27-start-of-run-discretionary-stop-preflight"},
            {"atom_id": "r27-repair-unauthorized-stop-before-new-work"},
            {"atom_id": "r28-eliminate-and-validate-cause-before-resume"},
            {"atom_id": "r28-resume-fails-closed-without-causal-remediation"},
            {"atom_id": "r29-task-chat-input-exactly-once-cas"},
            {"atom_id": "r29-remove-stop-recurrence-causes"},
            {"atom_id": "r29-never-fabricate-lost-local-continuation"},
            {"atom_id": "r29-general-repair-is-not-payload-authorization"},
        ],
    }
    strategy = {
        "execution_rules": {
            "start_of_run_continuity_preflight_required": True,
            "repair_discretionary_stop_before_unrelated_work": True,
            "eliminate_discretionary_stop_cause_before_resume": True,
            "resume_without_validated_causal_remediation": False,
        }
    }
    _write(root / "agi/USER_INPUT_INBOX.json", inbox)
    _write(root / "agi/USER_DIRECTIVE_EVENTS.json", ledger)
    _write(root / "agi/WORK_STRATEGY.json", strategy)
    fence = "test-fence-token"
    state = {
        "schema_version": 1,
        "status": "running",
        "execution_id": "work-recovery-test-execution",
        "lease_generation": 28,
        "fence_token": fence,
        "active_run_id": run_id,
        "user_input_inbox": {"highest_acknowledged_revision": 29},
        "termination": {"kind": "refire_failed_retry_required_not_complete"},
        "refire": {
            "failure": {
                "kind": "github_write_requires_explicit_policy_approval",
            }
        },
    }
    _write(root / "agi/WORK_EXECUTION_STATE.json", state)
    return state


def _valid_preflight(root: Path, state: dict, run_id: str) -> dict:
    return {
        "schema_version": 1,
        "policy_revision": 29,
        "execution_id": state["execution_id"],
        "lease_generation": state["lease_generation"],
        "fence_token_digest": hashlib.sha256(state["fence_token"].encode()).hexdigest(),
        "run_id": run_id,
        "executor_binding": "current_chatgpt_work_session",
        "model_identity": "chatgpt-work-model-unverified",
        "evaluated_at": "2026-08-26T22:50:00Z",
        "idempotency_key": "continuity-preflight:test:g28",
        "classification": "discretionary_stop_cause_eliminated_and_validated",
        "policy_bindings": {
            relative: _digest(root / relative)
            for relative in (
                "agi/USER_INPUT_INBOX.json",
                "agi/USER_DIRECTIVE_EVENTS.json",
                "agi/WORK_STRATEGY.json",
            )
        },
        "root_causes": [
            {
                "cause_id": "approval-scope-reinterpretation",
                "mechanism": "broad safe-work authorization was reinterpreted as insufficient",
                "evidence_refs": ["state.refire.failure"],
            }
        ],
        "remediations": [
            {
                "status": "implemented",
                "artifact_refs": ["src/continual/continuity_preflight.py"],
            }
        ],
        "validations": [
            {
                "status": "passed",
                "evidence_refs": ["tests/test_continuity_preflight.py"],
            }
        ],
        "recurrence_guard": {
            "status": "enforced",
            "entrypoint": "WorkSession.resume",
        },
        "evidence_refs": ["agi/USER_INPUT_INBOX.json#revision-28"],
        "resume_authorized": True,
    }


def test_resume_fails_before_native_mutation_without_causal_remediation(tmp_path: Path) -> None:
    root = _root(tmp_path)
    run_id = "run-causal-preflight"
    session = WorkSession(root)
    started = session.start("freeze entry before policy activation", run_id=run_id)
    request = started["pending"][0]
    submit_work_response(
        root,
        request["invocation_id"],
        _entry_output(request),
        executor_binding="current_chatgpt_work_session",
        model_identity="chatgpt-work-model-unverified",
    )
    state = _install_policy(root, run_id)
    before = (root / ".continual/runs" / run_id / "snapshot.json").read_bytes()

    with pytest.raises(ContinuityPreflightError, match="must be an object"):
        session.resume(run_id)
    assert (root / ".continual/runs" / run_id / "snapshot.json").read_bytes() == before

    state["start_of_run_continuity_preflight"] = _valid_preflight(root, state, run_id)
    state["start_of_run_continuity_preflight"]["remediations"] = []
    _write(root / "agi/WORK_EXECUTION_STATE.json", state)
    with pytest.raises(ContinuityPreflightError, match="remediation evidence"):
        session.resume(run_id)
    assert (root / ".continual/runs" / run_id / "snapshot.json").read_bytes() == before


def test_resume_proceeds_only_after_cause_elimination_validation(tmp_path: Path) -> None:
    root = _root(tmp_path)
    run_id = "run-causal-preflight-pass"
    session = WorkSession(root)
    started = session.start("freeze entry before validated repair", run_id=run_id)
    request = started["pending"][0]
    submit_work_response(
        root,
        request["invocation_id"],
        _entry_output(request),
        executor_binding="current_chatgpt_work_session",
        model_identity="chatgpt-work-model-unverified",
    )
    state = _install_policy(root, run_id)
    state["start_of_run_continuity_preflight"] = _valid_preflight(root, state, run_id)
    _write(root / "agi/WORK_EXECUTION_STATE.json", state)

    resumed = session.resume(run_id, max_steps=1)
    assert resumed["snapshot"]["phase"] == "root_pending"
    assert resumed["pending"] == []


def test_action_local_publication_hold_is_not_a_global_legitimate_stop() -> None:
    classification = classify_prior_stop(
        {
            "status": "running",
            "termination": {
                "active": True,
                "kind": "secret_or_account_holder_only_blocker",
                "non_overridable": True,
                "scope": "individual_effect",
                "blocks_project_globally": False,
                "blocker_type": "payload_specific_github_publication_authorization",
                "evidence_refs": ["receipt:payload-review-denial"],
            },
        }
    )

    assert classification.kind == "discretionary_stop_detected"
    assert "action-local blocker" in classification.reason


def test_unacknowledged_current_inbox_revision_rejects_before_native_mutation(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    run_id = "run-unacknowledged-input"
    session = WorkSession(root)
    started = session.start("freeze entry before input acknowledgement", run_id=run_id)
    request = started["pending"][0]
    submit_work_response(
        root,
        request["invocation_id"],
        _entry_output(request),
        executor_binding="current_chatgpt_work_session",
        model_identity="chatgpt-work-model-unverified",
    )
    state = _install_policy(root, run_id)
    state["start_of_run_continuity_preflight"] = _valid_preflight(
        root, state, run_id
    )
    state["user_input_inbox"]["highest_acknowledged_revision"] = 28
    _write(root / "agi/WORK_EXECUTION_STATE.json", state)
    snapshot_path = root / ".continual" / "runs" / run_id / "snapshot.json"
    before = snapshot_path.read_bytes()

    with pytest.raises(
        ContinuityPreflightError,
        match="current inbox revision is unacknowledged",
    ):
        session.resume(run_id)

    assert snapshot_path.read_bytes() == before


def test_pending_resume_requires_remote_durable_request_and_snapshot(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    run_id = "run-remote-durable-continuation"
    session = WorkSession(root)
    started = session.start("freeze one exact request", run_id=run_id)
    request = started["pending"][0]
    state = _install_policy(root, run_id)
    state["start_of_run_continuity_preflight"] = _valid_preflight(
        root, state, run_id
    )
    snapshot_path = root / ".continual" / "runs" / run_id / "snapshot.json"
    request_ref = (
        f'.continual/work-model/invocations/{request["invocation_id"]}/request.json'
    )
    request_path = root / request_ref
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    state["exact_continuation"] = {
        "run_snapshot_ref": snapshot_path.relative_to(root).as_posix(),
        "snapshot_branch": "main",
        "snapshot_head_sha": "c" * 40,
        "snapshot_blob_sha": _git_blob_digest(snapshot_path),
        "snapshot_revision": snapshot["revision"],
        "native_phase": snapshot["phase"],
        "pending_work_invocation_id": request["invocation_id"],
        "pending_request_ref": request_ref,
        "pending_request_digest": request["request_digest"],
        "pending_request_blob_sha": None,
    }
    _write(root / "agi/WORK_EXECUTION_STATE.json", state)
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in (root / ".continual").rglob("*")
        if path.is_file()
    }

    with pytest.raises(
        ContinuityPreflightError,
        match="pending_request_blob_sha must be a non-empty string",
    ):
        assert_work_resume_continuity_preflight(
            root,
            run_id=run_id,
            executor_binding="current_chatgpt_work_session",
            model_identity="chatgpt-work-model-unverified",
        )
    assert {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in (root / ".continual").rglob("*")
        if path.is_file()
    } == before

    request_blob = _git_blob_digest(request_path)
    state["exact_continuation"]["pending_request_blob_sha"] = request_blob
    state["continuation_durability"] = {
        "schema_version": 1,
        "status": "remote_main_readback_verified",
        "verified_remote_readback": True,
        "verified_at": "2026-08-27T02:45:00Z",
        "execution_id": state["execution_id"],
        "lease_generation": state["lease_generation"],
        "fence_token_digest": hashlib.sha256(
            state["fence_token"].encode("utf-8")
        ).hexdigest(),
        "source_main_sha": state["exact_continuation"]["snapshot_head_sha"],
        "pending_work_invocation_id": request["invocation_id"],
        "pending_request_ref": request_ref,
        "pending_request_digest": request["request_digest"],
        "pending_request_blob_sha": request_blob,
        "run_snapshot_ref": state["exact_continuation"]["run_snapshot_ref"],
        "snapshot_blob_sha": state["exact_continuation"]["snapshot_blob_sha"],
        "snapshot_revision": snapshot["revision"],
        "native_phase": snapshot["phase"],
    }
    _write(root / "agi/WORK_EXECUTION_STATE.json", state)

    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Continuity Test")
    _git(root, "config", "user.email", "continuity-test@example.invalid")
    _git(root, "commit", "--quiet", "--allow-empty", "-m", "Remote main baseline")
    _git(root, "update-ref", "refs/remotes/origin/main", _git(root, "rev-parse", "HEAD"))

    with pytest.raises(
        ContinuityPreflightError,
        match="continuation source commit is not reachable from origin/main",
    ):
        assert_work_resume_continuity_preflight(
            root,
            run_id=run_id,
            executor_binding="current_chatgpt_work_session",
            model_identity="chatgpt-work-model-unverified",
        )
    assert {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in (root / ".continual").rglob("*")
        if path.is_file()
    } == before

    _git(
        root,
        "add",
        "--",
        request_ref,
        state["exact_continuation"]["run_snapshot_ref"],
    )
    _git(root, "commit", "--quiet", "-m", "Persist exact continuation")
    source_main_sha = _git(root, "rev-parse", "HEAD")
    state["exact_continuation"]["snapshot_head_sha"] = source_main_sha
    state["continuation_durability"]["source_main_sha"] = source_main_sha
    _write(root / "agi/WORK_EXECUTION_STATE.json", state)

    with pytest.raises(
        ContinuityPreflightError,
        match="continuation source commit is not reachable from origin/main",
    ):
        assert_work_resume_continuity_preflight(
            root,
            run_id=run_id,
            executor_binding="current_chatgpt_work_session",
            model_identity="chatgpt-work-model-unverified",
        )
    _git(root, "update-ref", "refs/remotes/origin/main", source_main_sha)

    result = assert_work_resume_continuity_preflight(
        root,
        run_id=run_id,
        executor_binding="current_chatgpt_work_session",
        model_identity="chatgpt-work-model-unverified",
    )

    assert result["continuation_durability"]["status"] == (
        "remote_main_readback_verified"
    )
    assert result["continuation_durability"]["pending_request_blob_sha"] == (
        request_blob
    )

    state["exact_continuation"]["run_snapshot_ref"] = "../../outside.json"
    _write(root / "agi/WORK_EXECUTION_STATE.json", state)
    with pytest.raises(ContinuityPreflightError, match="escapes the repository"):
        assert_work_resume_continuity_preflight(
            root,
            run_id=run_id,
            executor_binding="current_chatgpt_work_session",
            model_identity="chatgpt-work-model-unverified",
        )
