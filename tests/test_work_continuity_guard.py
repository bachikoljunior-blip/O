from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from agi.work_continuity_guard import evaluate_work_checkpoint_transition


NOW = datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc)


def _authorization() -> dict:
    return {
        "schema_version": 3,
        "status": "active",
        "scope": "primary_o_and_successor_recovery_execution",
        "authorization": {
            "primary_execution_may_not_stop_by_discretion_while_safe_executable_work_exists": True,
            "root_must_select_a_next_safe_falsifiable_unit_when_objective_unmet": True,
            "checkpoint_is_continuation_not_permission_wait": True,
        },
    }


def _running() -> dict:
    return {
        "schema_version": 1,
        "status": "running",
        "owner_kind": "work_recovery_automation",
        "execution_id": "work-primary-20260826T090000Z",
        "lease_generation": 20,
        "fence_token": "fence-token-0123456789abcdef0123456789abcdef",
    }


def _checkpoint(*, safe_work_exists: bool = True) -> dict:
    state = deepcopy(_running())
    state["status"] = "checkpointed"
    state["termination"] = {
        "kind": "automation_task_run_boundary",
        "observed_at": NOW.isoformat(),
        "normal_completion": False,
        "voluntary": False,
        "safe_work_exists": safe_work_exists,
        "forced_boundary_receipt_id": "runtime-receipt-0001",
    }
    return state


def _receipt(**overrides: object) -> dict:
    receipt = {
        "schema_version": 1,
        "receipt_id": "runtime-receipt-0001",
        "kind": "automation_task_run_boundary",
        "source": "automation_runtime",
        "verified": True,
        "external_to_primary_model": True,
        "execution_id": "work-primary-20260826T090000Z",
        "lease_generation": 20,
        "observed_at": (NOW - timedelta(seconds=2)).isoformat(),
    }
    receipt.update(overrides)
    return receipt


def test_self_declared_forced_boundary_cannot_end_a_run_with_safe_work() -> None:
    decision = evaluate_work_checkpoint_transition(
        _running(),
        _checkpoint(),
        _authorization(),
        now=NOW,
    )

    assert decision.allowed is False
    assert decision.action == "continue_running_or_select_next_unit"
    assert decision.safe_work_exists is True
    assert decision.forced_boundary_verified is False
    assert "self-declared forced termination is insufficient" in decision.reason


def test_fresh_external_runtime_receipt_allows_a_real_forced_boundary_checkpoint() -> None:
    decision = evaluate_work_checkpoint_transition(
        _running(),
        _checkpoint(),
        _authorization(),
        forced_boundary_receipt=_receipt(),
        now=NOW,
    )

    assert decision.allowed is True
    assert decision.action == "persist_forced_boundary_checkpoint"
    assert decision.forced_boundary_verified is True


def test_mismatched_or_model_authored_receipt_is_rejected() -> None:
    mismatched = evaluate_work_checkpoint_transition(
        _running(),
        _checkpoint(),
        _authorization(),
        forced_boundary_receipt=_receipt(lease_generation=21),
        now=NOW,
    )
    model_authored = evaluate_work_checkpoint_transition(
        _running(),
        _checkpoint(),
        _authorization(),
        forced_boundary_receipt=_receipt(
            source="primary_model",
            external_to_primary_model=False,
        ),
        now=NOW,
    )

    assert mismatched.allowed is False
    assert "lease_generation mismatch" in mismatched.reason
    assert model_authored.allowed is False
    assert "external runtime" in model_authored.reason


def test_old_or_future_runtime_receipt_is_rejected() -> None:
    old = evaluate_work_checkpoint_transition(
        _running(),
        _checkpoint(),
        _authorization(),
        forced_boundary_receipt=_receipt(
            observed_at=(NOW - timedelta(seconds=301)).isoformat()
        ),
        now=NOW,
    )
    future = evaluate_work_checkpoint_transition(
        _running(),
        _checkpoint(),
        _authorization(),
        forced_boundary_receipt=_receipt(
            observed_at=(NOW + timedelta(seconds=121)).isoformat()
        ),
        now=NOW,
    )

    assert old.allowed is False
    assert "too old" in old.reason
    assert future.allowed is False
    assert "future" in future.reason


def test_voluntary_or_unspecified_termination_is_rejected_before_receipt_check() -> None:
    voluntary = _checkpoint()
    voluntary["termination"]["voluntary"] = True
    unspecified = _checkpoint()
    del unspecified["termination"]["voluntary"]

    first = evaluate_work_checkpoint_transition(
        _running(),
        voluntary,
        _authorization(),
        forced_boundary_receipt=_receipt(),
        now=NOW,
    )
    second = evaluate_work_checkpoint_transition(
        _running(),
        unspecified,
        _authorization(),
        forced_boundary_receipt=_receipt(),
        now=NOW,
    )

    assert first.allowed is False
    assert second.allowed is False
    assert "voluntary or unspecified" in first.reason
    assert "voluntary or unspecified" in second.reason


def test_no_safe_work_requires_external_blockers_and_machine_resume_condition() -> None:
    proposed = _checkpoint(safe_work_exists=False)
    proposed["termination"] = {
        "kind": "no_safe_executable_work",
        "observed_at": NOW.isoformat(),
        "normal_completion": False,
        "voluntary": False,
        "safe_work_exists": False,
        "blocking_facts": ["all currently authorized provider routes are unavailable"],
        "attempted_alternatives": ["continued repository-only validation"],
        "blocking_receipts": [
            {
                "source_ref": "agi/observations/provider-capacity.json",
                "externally_observable": True,
            }
        ],
        "exact_resume_condition": {
            "kind": "source_revision_changes",
            "source_ref": "agi/observations/provider-capacity.json",
        },
    }

    allowed = evaluate_work_checkpoint_transition(
        _running(), proposed, _authorization(), now=NOW
    )
    incomplete = deepcopy(proposed)
    incomplete["termination"]["blocking_receipts"] = []
    denied = evaluate_work_checkpoint_transition(
        _running(), incomplete, _authorization(), now=NOW
    )

    assert allowed.allowed is True
    assert allowed.action == "persist_externally_blocked_checkpoint"
    assert denied.allowed is False
    assert "blocking receipts" in denied.reason


def test_checkpoint_cannot_change_writer_binding_or_bypass_inactive_authorization() -> None:
    rebound = _checkpoint()
    rebound["execution_id"] = "work-other-20260826T090000Z"
    inactive = _authorization()
    inactive["status"] = "inactive"

    changed_writer = evaluate_work_checkpoint_transition(
        _running(), rebound, _authorization(), now=NOW
    )
    no_authority = evaluate_work_checkpoint_transition(
        _running(), _checkpoint(), inactive, now=NOW
    )

    assert changed_writer.allowed is False
    assert "changed execution_id" in changed_writer.reason
    assert no_authority.allowed is False
    assert "authorization is missing or inactive" in no_authority.reason


def test_generation20_style_self_attestation_is_explicitly_not_a_precedent() -> None:
    proposed = _checkpoint()
    proposed["termination"].update(
        {
            "kind": "platform_process_boundary",
            "forced_boundary_receipt_id": "",
            "exact_resume_condition": "next scheduled monitor run",
        }
    )

    decision = evaluate_work_checkpoint_transition(
        _running(), proposed, _authorization(), now=NOW
    )

    assert decision.allowed is False
    assert decision.action == "continue_running_or_select_next_unit"
