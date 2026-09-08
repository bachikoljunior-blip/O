from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .continuity_preflight import assert_work_resume_continuity_preflight


class RecoveryAuditError(ValueError):
    """Raised when a recovery audit cannot be reproduced from durable records."""


_SHA = re.compile(r"^[0-9a-f]{40}$")
_DISCRETIONARY_SIGNALS = ("approval", "permission", "discretion")


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RecoveryAuditError(f"missing or malformed audit input: {path}") from exc
    if not isinstance(value, dict):
        raise RecoveryAuditError(f"audit input must be an object: {path}")
    return value


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RecoveryAuditError(f"{field} must be an object")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecoveryAuditError(f"{field} must be non-empty text")
    return value.strip()


def _sha(value: Any, field: str) -> str:
    exact = _text(value, field).lower()
    if not _SHA.fullmatch(exact):
        raise RecoveryAuditError(f"{field} must be a full lowercase SHA")
    return exact


def _invocation_pair(root: Path, invocation_id: str) -> dict[str, str]:
    directory = root / ".continual" / "work-model" / "invocations" / invocation_id
    request = _load(directory / "request.json")
    response = _load(directory / "response.json")
    if request.get("invocation_id") != invocation_id:
        raise RecoveryAuditError("Root request invocation identity mismatch")
    if response.get("invocation_id") != invocation_id:
        raise RecoveryAuditError("Root response invocation identity mismatch")
    if response.get("request_digest") != request.get("request_digest"):
        raise RecoveryAuditError("Root request/response digest mismatch")
    if request.get("component") != "root":
        raise RecoveryAuditError("audited invocation is not Root")
    if response.get("executor_binding") != request.get("executor_binding"):
        raise RecoveryAuditError("Root executor binding mismatch")
    if response.get("model_identity") != request.get("model_identity"):
        raise RecoveryAuditError("Root model identity mismatch")
    return {
        "invocation_id": invocation_id,
        "request_digest": _text(request.get("request_digest"), "request_digest"),
        "response_digest": _text(response.get("response_digest"), "response_digest"),
        "output_digest": _text(response.get("output_digest"), "output_digest"),
    }


def _work_receipt(root: Path, run_id: str, observation_id: str) -> dict[str, Any]:
    path = (
        root
        / ".continual"
        / "runs"
        / run_id
        / "work-source-observations"
        / observation_id
        / "receipt.json"
    )
    receipt = _load(path)
    if receipt.get("record_type") != "work_source_observation_receipt":
        raise RecoveryAuditError("invalid Work-state receipt type")
    if receipt.get("status") != "succeeded" or receipt.get("run_id") != run_id:
        raise RecoveryAuditError("Work-state receipt did not succeed for this run")
    return receipt


def _ci_receipt(root: Path, run_id: str, observation_id: str) -> dict[str, Any]:
    path = (
        root
        / ".continual"
        / "runs"
        / run_id
        / "ci-source-observations"
        / observation_id
        / "receipt.json"
    )
    receipt = _load(path)
    if receipt.get("record_type") != "ci_source_observation_receipt":
        raise RecoveryAuditError("invalid CI receipt type")
    if receipt.get("status") != "succeeded" or receipt.get("run_id") != run_id:
        raise RecoveryAuditError("CI receipt did not succeed for this run")
    projection = _mapping(receipt.get("projection"), "ci_receipt.projection")
    workflow = _mapping(projection.get("workflow_run"), "ci_receipt.workflow_run")
    jobs = projection.get("required_jobs")
    if workflow.get("status") != "completed" or workflow.get("conclusion") != "success":
        raise RecoveryAuditError("exact-head workflow did not succeed")
    if not isinstance(jobs, list) or not jobs:
        raise RecoveryAuditError("CI receipt lacks required jobs")
    if any(
        not isinstance(job, Mapping)
        or job.get("status") != "completed"
        or job.get("conclusion") != "success"
        for job in jobs
    ):
        raise RecoveryAuditError("a required exact-head job did not succeed")
    return receipt


def build_recovery_intervention_audit(
    root: Path,
    *,
    run_id: str,
    root_invocation_id: str,
    work_observation_id: str,
    ci_observation_id: str,
    expected_main_sha: str,
    expected_exact_head_sha: str,
    executor_binding: str,
    model_identity: str,
) -> dict[str, Any]:
    """Reproduce one causal recovery audit only from checked durable records."""

    root = root.resolve()
    state = _load(root / "agi" / "WORK_EXECUTION_STATE.json")
    preflight_report = assert_work_resume_continuity_preflight(
        root,
        run_id=run_id,
        executor_binding=executor_binding,
        model_identity=model_identity,
    )
    refire = _mapping(state.get("refire"), "state.refire")
    failure = _mapping(refire.get("failure"), "state.refire.failure")
    serialized_failure = json.dumps(failure, ensure_ascii=False, sort_keys=True).lower()
    if not any(signal in serialized_failure for signal in _DISCRETIONARY_SIGNALS):
        raise RecoveryAuditError("historical failure is not a discretionary approval stop")
    resolution = _mapping(refire.get("failure_resolution"), "state.refire.failure_resolution")
    if resolution.get("status") != "cause_eliminated_and_validated":
        raise RecoveryAuditError("historical stop cause is not eliminated and validated")
    if resolution.get("state_reset_alone_claimed_as_repair") is not False:
        raise RecoveryAuditError("state-only mutation cannot satisfy causal repair")
    before = _text(
        resolution.get("native_resume_fail_closed_digest_before"),
        "failure_resolution.digest_before",
    )
    after = _text(
        resolution.get("native_resume_fail_closed_digest_after"),
        "failure_resolution.digest_after",
    )
    if before != after:
        raise RecoveryAuditError("fail-closed validation mutated native records")

    invocation = _invocation_pair(root, root_invocation_id)
    work_receipt = _work_receipt(root, run_id, work_observation_id)
    observed_main = _sha(
        _mapping(work_receipt.get("source_version"), "work_receipt.source_version").get(
            "commit_sha"
        ),
        "work_receipt.commit_sha",
    )
    if observed_main != _sha(expected_main_sha, "expected_main_sha"):
        raise RecoveryAuditError("audit is not bound to the expected latest main")
    ci_receipt = _ci_receipt(root, run_id, ci_observation_id)
    observed_head = _sha(
        _mapping(
            _mapping(ci_receipt.get("projection"), "ci_receipt.projection").get(
                "workflow_run"
            ),
            "ci_receipt.workflow_run",
        ).get("head_sha"),
        "ci_receipt.head_sha",
    )
    if observed_head != _sha(expected_exact_head_sha, "expected_exact_head_sha"):
        raise RecoveryAuditError("CI receipt is not bound to the expected exact head")

    preflight = _mapping(
        state.get("start_of_run_continuity_preflight"),
        "state.start_of_run_continuity_preflight",
    )
    causes = preflight.get("root_causes")
    if not isinstance(causes, list) or len(causes) < 2:
        raise RecoveryAuditError("audit requires both decision and entrypoint root causes")
    cause_ids = sorted(
        _text(_mapping(item, "preflight.root_cause").get("cause_id"), "cause_id")
        for item in causes
    )
    return {
        "schema_version": 1,
        "audit_scope": "generation22/revision28-causal-recovery-audit-reconstruction-v1",
        "source_authority": "latest_remote_main_and_immutable_native_records_only",
        "execution_binding": {
            "execution_id": state.get("execution_id"),
            "lease_generation": state.get("lease_generation"),
            "fence_token_digest": preflight.get("fence_token_digest"),
            "run_id": run_id,
            "executor_binding": executor_binding,
            "model_identity": model_identity,
            "policy_revision": preflight.get("policy_revision"),
        },
        "classification": "discretionary_stop_cause_eliminated_and_validated",
        "root_cause_ids": cause_ids,
        "native_fail_closed_no_mutation": True,
        "native_record_digest": before,
        "root_refire": invocation,
        "source_receipts": {
            "work_observation_id": work_observation_id,
            "main_commit_sha": observed_main,
            "ci_observation_id": ci_observation_id,
            "exact_head_sha": observed_head,
        },
        "preflight_report": preflight_report,
        "reviewed_publication_paths": [
            "src/continual/recovery_audit.py",
            "tests/test_recovery_audit.py",
            f".continual/work-model/invocations/{root_invocation_id}/request.json",
            f".continual/work-model/invocations/{root_invocation_id}/response.json",
        ],
        "lost_local_candidate_used": False,
        "negative_evidence": [
            "The prior execution stopped on an approval-scope reinterpretation despite standing safe-work authorization.",
            "Before PR 319, native Work resume lacked a causal-remediation entry guard.",
            "The reconstructed audit is internal repository evidence, not independent production evidence.",
        ],
        "claim_boundary": (
            "This audit establishes only the checked generation-22 stop classification, "
            "merged causal guard, exact source bindings, and one Root request/response pair. "
            "It does not establish broader recovery reliability, external production "
            "capability, AGI, or user-level completion."
        ),
    }
