from __future__ import annotations

import json
from pathlib import Path

import pytest

from continual.recovery_audit import (
    RecoveryAuditError,
    build_recovery_intervention_audit,
)


RUN_ID = "run-work-recovery-gen9-durability-repair"
ROOT_INVOCATION = "invoke-4f67668fbf0f833b6421d09e"
WORK_OBSERVATION = "work-state-bc01e5a09ab16c087fb590ba"
CI_OBSERVATION = "ci-run-2360f7b9271b733feab99151"
MAIN_SHA = "cdf150658f093964ffc1e5a7930c46f25666a929"
EXACT_HEAD = "955a4f028c442416b98b9ba9bf1a0456b10c6035"


def _build(root: Path) -> dict:
    return build_recovery_intervention_audit(
        root,
        run_id=RUN_ID,
        root_invocation_id=ROOT_INVOCATION,
        work_observation_id=WORK_OBSERVATION,
        ci_observation_id=CI_OBSERVATION,
        expected_main_sha=MAIN_SHA,
        expected_exact_head_sha=EXACT_HEAD,
        executor_binding="current_chatgpt_work_session",
        model_identity="chatgpt-work-model-unverified",
    )


def test_authoritative_generation22_recovery_audit_reproduces() -> None:
    audit = _build(Path("."))
    assert audit["classification"] == "discretionary_stop_cause_eliminated_and_validated"
    assert audit["execution_binding"]["lease_generation"] == 22
    assert audit["execution_binding"]["policy_revision"] == 28
    assert audit["native_fail_closed_no_mutation"] is True
    assert audit["lost_local_candidate_used"] is False
    assert audit["root_refire"]["invocation_id"] == ROOT_INVOCATION
    assert audit["source_receipts"] == {
        "work_observation_id": WORK_OBSERVATION,
        "main_commit_sha": MAIN_SHA,
        "ci_observation_id": CI_OBSERVATION,
        "exact_head_sha": EXACT_HEAD,
    }
    assert "AGI" in audit["claim_boundary"]


def test_state_only_repair_is_rejected(tmp_path: Path) -> None:
    state_path = Path("agi/WORK_EXECUTION_STATE.json")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["refire"]["failure_resolution"]["state_reset_alone_claimed_as_repair"] = True
    target = tmp_path / "agi" / "WORK_EXECUTION_STATE.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(state), encoding="utf-8")
    for relative in (
        "agi/USER_INPUT_INBOX.json",
        "agi/USER_DIRECTIVE_EVENTS.json",
        "agi/WORK_STRATEGY.json",
    ):
        source = Path(relative)
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    with pytest.raises(RecoveryAuditError, match="state-only mutation"):
        _build(tmp_path)


def test_wrong_latest_main_or_exact_head_is_rejected() -> None:
    with pytest.raises(RecoveryAuditError, match="expected latest main"):
        build_recovery_intervention_audit(
            Path("."),
            run_id=RUN_ID,
            root_invocation_id=ROOT_INVOCATION,
            work_observation_id=WORK_OBSERVATION,
            ci_observation_id=CI_OBSERVATION,
            expected_main_sha="0" * 40,
            expected_exact_head_sha=EXACT_HEAD,
            executor_binding="current_chatgpt_work_session",
            model_identity="chatgpt-work-model-unverified",
        )
    with pytest.raises(RecoveryAuditError, match="expected exact head"):
        build_recovery_intervention_audit(
            Path("."),
            run_id=RUN_ID,
            root_invocation_id=ROOT_INVOCATION,
            work_observation_id=WORK_OBSERVATION,
            ci_observation_id=CI_OBSERVATION,
            expected_main_sha=MAIN_SHA,
            expected_exact_head_sha="0" * 40,
            executor_binding="current_chatgpt_work_session",
            model_identity="chatgpt-work-model-unverified",
        )
