from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from continual.continuity_preflight import ContinuityPreflightError
from continual.work_session import WorkSession, submit_work_response


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    inbox = {"schema_version": 1, "revision": 28, "entries": []}
    ledger = {
        "schema_version": 1,
        "source": {"revision": 28},
        "atoms": [
            {"atom_id": "r27-start-of-run-discretionary-stop-preflight"},
            {"atom_id": "r27-repair-unauthorized-stop-before-new-work"},
            {"atom_id": "r28-eliminate-and-validate-cause-before-resume"},
            {"atom_id": "r28-resume-fails-closed-without-causal-remediation"},
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
        "policy_revision": 28,
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
