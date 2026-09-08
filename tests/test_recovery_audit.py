from __future__ import annotations

import hashlib
import json
import shutil
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
EXECUTION_ID = "work-recovery-20260826T213331747Z-e52ebacf244e3612dc86f26b692dac27"
FENCE_TOKEN = (
    "work-recovery-fence-v22-"
    "45f0fd11afbd944c3c49bd370d0a5739894734581e2ad7b235250a1286211256"
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _copy_audit_input(root: Path, relative: str) -> None:
    source = Path(relative)
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _generation22_audit_root(root: Path) -> Path:
    """Materialize immutable generation-22 audit inputs in an isolated root.

    The repository-level Work state is intentionally mutable and now belongs to a
    later generation.  Historical audit regression must therefore not read that
    live pointer as though it were still the generation-22 record under test.
    """

    policy_documents = {
        "agi/USER_INPUT_INBOX.json": {"revision": 28},
        "agi/USER_DIRECTIVE_EVENTS.json": {
            "source": {"revision": 28},
            "atoms": [
                {"atom_id": "r27-start-of-run-discretionary-stop-preflight"},
                {"atom_id": "r27-repair-unauthorized-stop-before-new-work"},
                {"atom_id": "r28-eliminate-and-validate-cause-before-resume"},
                {"atom_id": "r28-resume-fails-closed-without-causal-remediation"},
            ],
        },
        "agi/WORK_STRATEGY.json": {
            "execution_rules": {
                "start_of_run_continuity_preflight_required": True,
                "repair_discretionary_stop_before_unrelated_work": True,
                "eliminate_discretionary_stop_cause_before_resume": True,
                "resume_without_validated_causal_remediation": False,
            }
        },
    }
    for relative, document in policy_documents.items():
        _write_json(root / relative, document)
    policy_bindings = {
        relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
        for relative in policy_documents
    }
    state = {
        "execution_id": EXECUTION_ID,
        "lease_generation": 22,
        "fence_token": FENCE_TOKEN,
        "active_run_id": RUN_ID,
        "start_of_run_continuity_preflight": {
            "schema_version": 1,
            "policy_revision": 28,
            "execution_id": EXECUTION_ID,
            "lease_generation": 22,
            "fence_token_digest": hashlib.sha256(FENCE_TOKEN.encode()).hexdigest(),
            "run_id": RUN_ID,
            "executor_binding": "current_chatgpt_work_session",
            "model_identity": "chatgpt-work-model-unverified",
            "evaluated_at": "2026-08-26T23:42:57.090Z",
            "idempotency_key": "generation22-recovery-audit-fixture",
            "classification": "discretionary_stop_cause_eliminated_and_validated",
            "policy_bindings": policy_bindings,
            "root_causes": [
                {
                    "cause_id": (
                        "broad-safe-work-authorization-reinterpreted-as-file-specific-gap"
                    ),
                    "mechanism": "standing safe-work authority was reclassified",
                    "evidence_refs": ["agi/evidence/generation22-revision28-recovery-audit.json"],
                },
                {
                    "cause_id": "native-resume-lacked-causal-stop-preflight",
                    "mechanism": "native resume lacked a causal entry guard",
                    "evidence_refs": ["https://github.com/bachikoljunior-blip/O/pull/319"],
                },
            ],
            "remediations": [
                {
                    "status": "merged",
                    "artifact_refs": ["https://github.com/bachikoljunior-blip/O/pull/319"],
                }
            ],
            "validations": [
                {
                    "status": "passed",
                    "evidence_refs": ["exact-head:955a4f028c442416b98b9ba9bf1a0456b10c6035"],
                }
            ],
            "recurrence_guard": {
                "status": "enforced",
                "entrypoint": "WorkSession.resume",
            },
            "evidence_refs": ["agi/evidence/generation22-revision28-recovery-audit.json"],
            "resume_authorized": True,
        },
        "refire": {
            "failure": {
                "kind": "github_write_safety_review_requires_explicit_policy_state_approval"
            },
            "failure_resolution": {
                "status": "cause_eliminated_and_validated",
                "state_reset_alone_claimed_as_repair": False,
                "native_resume_fail_closed_digest_before": (
                    "da53ce4cd77da4f7724438662383097ce41284c231a366d4b23b45bd5ff7f4b4"
                ),
                "native_resume_fail_closed_digest_after": (
                    "da53ce4cd77da4f7724438662383097ce41284c231a366d4b23b45bd5ff7f4b4"
                ),
            },
        },
    }
    _write_json(root / "agi/WORK_EXECUTION_STATE.json", state)
    for relative in (
        f".continual/work-model/invocations/{ROOT_INVOCATION}/request.json",
        f".continual/work-model/invocations/{ROOT_INVOCATION}/response.json",
        (
            f".continual/runs/{RUN_ID}/work-source-observations/"
            f"{WORK_OBSERVATION}/receipt.json"
        ),
        (
            f".continual/runs/{RUN_ID}/ci-source-observations/"
            f"{CI_OBSERVATION}/receipt.json"
        ),
    ):
        _copy_audit_input(root, relative)
    return root


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


def test_authoritative_generation22_recovery_audit_reproduces(tmp_path: Path) -> None:
    audit = _build(_generation22_audit_root(tmp_path))
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
    root = _generation22_audit_root(tmp_path)
    state_path = root / "agi/WORK_EXECUTION_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["refire"]["failure_resolution"]["state_reset_alone_claimed_as_repair"] = True
    _write_json(state_path, state)
    with pytest.raises(RecoveryAuditError, match="state-only mutation"):
        _build(root)


def test_wrong_latest_main_or_exact_head_is_rejected(tmp_path: Path) -> None:
    root = _generation22_audit_root(tmp_path)
    with pytest.raises(RecoveryAuditError, match="expected latest main"):
        build_recovery_intervention_audit(
            root,
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
            root,
            run_id=RUN_ID,
            root_invocation_id=ROOT_INVOCATION,
            work_observation_id=WORK_OBSERVATION,
            ci_observation_id=CI_OBSERVATION,
            expected_main_sha=MAIN_SHA,
            expected_exact_head_sha="0" * 40,
            executor_binding="current_chatgpt_work_session",
            model_identity="chatgpt-work-model-unverified",
        )
