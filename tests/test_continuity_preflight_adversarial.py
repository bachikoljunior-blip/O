from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from continual.continuity_adversarial import (
    adversarial_scenarios,
    run_adversarial_classification_matrix,
)
from continual.continuity_preflight import ContinuityPreflightError
from continual.work_session import WorkSession, submit_work_response


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _install_policy(root: Path, run_id: str, scenario_state: dict) -> None:
    _write(root / "agi/USER_INPUT_INBOX.json", {"revision": 28})
    _write(
        root / "agi/USER_DIRECTIVE_EVENTS.json",
        {
            "source": {"revision": 28},
            "atoms": [
                {"atom_id": "r27-start-of-run-discretionary-stop-preflight"},
                {"atom_id": "r27-repair-unauthorized-stop-before-new-work"},
                {"atom_id": "r28-eliminate-and-validate-cause-before-resume"},
                {"atom_id": "r28-resume-fails-closed-without-causal-remediation"},
            ],
        },
    )
    _write(
        root / "agi/WORK_STRATEGY.json",
        {
            "execution_rules": {
                "start_of_run_continuity_preflight_required": True,
                "repair_discretionary_stop_before_unrelated_work": True,
                "eliminate_discretionary_stop_cause_before_resume": True,
                "resume_without_validated_causal_remediation": False,
            }
        },
    )
    state = {
        **scenario_state,
        "execution_id": "work-adversarial-test",
        "lease_generation": 28,
        "fence_token": "adversarial-fence",
        "active_run_id": run_id,
    }
    state["start_of_run_continuity_preflight"] = {
        "schema_version": 1,
        "policy_revision": 28,
        "execution_id": state["execution_id"],
        "lease_generation": state["lease_generation"],
        "fence_token_digest": hashlib.sha256(state["fence_token"].encode()).hexdigest(),
        "run_id": run_id,
        "executor_binding": "current_chatgpt_work_session",
        "model_identity": "chatgpt-work-model-unverified",
        "evaluated_at": "2026-08-27T00:55:00Z",
        "idempotency_key": f"adversarial:{run_id}",
        "classification": "no_discretionary_stop_detected",
        "policy_bindings": {
            relative: _digest(root / relative)
            for relative in (
                "agi/USER_INPUT_INBOX.json",
                "agi/USER_DIRECTIVE_EVENTS.json",
                "agi/WORK_STRATEGY.json",
            )
        },
        "evidence_refs": ["test:adversarial-matrix"],
        "resume_authorized": True,
    }
    _write(root / "agi/WORK_EXECUTION_STATE.json", state)


def _entry_output(request: dict) -> dict:
    return {
        "result": {"objective": "adversarial preflight mutation proof"},
        "local_learn": {"decision": "NO_CHANGE", "candidates": []},
        "fragment": {"component": "entry", "observations": [request["invocation_id"]]},
    }


def test_adversarial_matrix_is_exact_and_deterministic() -> None:
    first = run_adversarial_classification_matrix()
    second = run_adversarial_classification_matrix()
    assert first == second
    assert first["scenario_count"] == 8
    assert first["passed_count"] == 8
    assert first["failed_count"] == 0


@pytest.mark.parametrize("scenario", adversarial_scenarios(), ids=lambda item: item["scenario_id"])
def test_every_unresolved_stop_rejects_before_native_mutation(
    tmp_path: Path, scenario: dict
) -> None:
    root = tmp_path / scenario["scenario_id"]
    shutil.copytree(Path("prompts"), root / "prompts")
    run_id = f"run-{scenario['scenario_id']}"
    session = WorkSession(root)
    started = session.start("freeze native record", run_id=run_id)
    request = started["pending"][0]
    submit_work_response(
        root,
        request["invocation_id"],
        _entry_output(request),
        executor_binding="current_chatgpt_work_session",
        model_identity="chatgpt-work-model-unverified",
    )
    _install_policy(root, run_id, scenario["state"])
    snapshot_path = root / ".continual" / "runs" / run_id / "snapshot.json"
    before = snapshot_path.read_bytes()

    with pytest.raises(ContinuityPreflightError):
        session.resume(run_id)

    assert snapshot_path.read_bytes() == before
