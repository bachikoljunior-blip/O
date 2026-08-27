from __future__ import annotations

from copy import deepcopy
from typing import Any

from .continuity_preflight import classify_prior_stop


_HEAD = "0123456789abcdef0123456789abcdef01234567"


def adversarial_scenarios() -> tuple[dict[str, Any], ...]:
    """Return closed synthetic fixtures for the revision-28 stop boundary."""

    return (
        {
            "scenario_id": "approval_scope_reinterpretation",
            "expected": "discretionary_stop_detected",
            "state": {
                "status": "running",
                "termination": {"kind": "refire_failed_retry_required_not_complete"},
                "refire": {"failure": {"kind": "approval_scope_reinterpretation"}},
            },
        },
        {
            "scenario_id": "voluntary_uncertainty_checkpoint",
            "expected": "discretionary_stop_detected",
            "state": {
                "status": "running",
                "termination": {"kind": "voluntary_reassess_due_to_uncertainty"},
            },
        },
        {
            "scenario_id": "hard_platform_safety_prohibition",
            "expected": "legitimate_non_discretionary_stop",
            "state": {
                "status": "blocked",
                "termination": {
                    "active": True,
                    "kind": "hard_platform_safety_prohibition",
                    "non_overridable": True,
                    "reason": "platform approval policy prohibits this effect",
                    "evidence_refs": ["receipt:platform-denial"],
                },
            },
        },
        {
            "scenario_id": "secret_account_holder_only_blocker",
            "expected": "legitimate_non_discretionary_stop",
            "state": {
                "status": "blocked",
                "termination": {
                    "active": True,
                    "kind": "secret_or_account_holder_only_blocker",
                    "non_overridable": True,
                    "scope": "global_execution",
                    "blocks_project_globally": True,
                    "blocker_type": "irreducibly_account_holder_only_operation",
                    "evidence_refs": ["receipt:secret-boundary"],
                },
            },
        },
        {
            "scenario_id": "action_local_payload_publication_hold",
            "expected": "discretionary_stop_detected",
            "state": {
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
            },
        },
        {
            "scenario_id": "fresh_different_writer",
            "expected": "legitimate_non_discretionary_stop",
            "state": {
                "status": "running",
                "termination": {
                    "active": True,
                    "kind": "fresh_different_writer_detected",
                    "different_execution_id": "work-other-fresh-owner",
                    "fresh_activity_observed": True,
                    "evidence_refs": ["receipt:fresh-owner-readback"],
                },
            },
        },
        {
            "scenario_id": "running_exact_head_workflow",
            "expected": "legitimate_non_discretionary_stop",
            "state": {
                "status": "running",
                "termination": {
                    "active": True,
                    "kind": "running_exact_head_workflow",
                    "workflow_status": "in_progress",
                    "workflow_run_id": 12345,
                    "exact_head_sha": _HEAD,
                    "evidence_refs": ["receipt:exact-head-workflow"],
                },
            },
        },
        {
            "scenario_id": "completed_upper_objective",
            "expected": "legitimate_non_discretionary_stop",
            "state": {
                "status": "completed",
                "termination": {
                    "active": True,
                    "kind": "user_level_objective_met",
                    "normal_completion": True,
                    "user_objective_met": True,
                    "evidence_refs": ["receipt:upper-objective-pass"],
                },
            },
        },
        {
            "scenario_id": "uncorroborated_hard_stop_marker",
            "expected": "malformed_legitimate_stop",
            "state": {
                "status": "blocked",
                "termination": {
                    "active": True,
                    "kind": "hard_platform_safety_prohibition",
                    "reason": "approval words alone are insufficient",
                },
            },
        },
    )


def run_adversarial_classification_matrix() -> dict[str, Any]:
    rows = []
    for scenario in adversarial_scenarios():
        observed = classify_prior_stop(deepcopy(scenario["state"]))
        rows.append(
            {
                "scenario_id": scenario["scenario_id"],
                "expected": scenario["expected"],
                "observed": observed.kind,
                "passed": observed.kind == scenario["expected"],
            }
        )
    return {
        "schema_version": 1,
        "scenario_count": len(rows),
        "passed_count": sum(row["passed"] for row in rows),
        "failed_count": sum(not row["passed"] for row in rows),
        "rows": rows,
        "claim_boundary": (
            "Only the declared synthetic revision-28 stop-classification scenarios; "
            "not general recovery reliability, production capability, AGI, or user-level completion."
        ),
    }
