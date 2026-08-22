from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from agi.work_mode_monitor import (
    WorkModeCASProof,
    authorize_work_mode_recovery,
    evaluate_work_mode_monitor,
)


NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]
OLD_BLOB = "1" * 40
ACQUIRE_COMMIT = "2" * 40
ACQUIRE_BLOB = "3" * 40
FINAL_COMMIT = "4" * 40
FINAL_BLOB = "5" * 40


def _state(*, status: str = "running", age_seconds: int = 60, generation: int = 7) -> dict:
    return {
        "schema_version": 1,
        "status": status,
        "owner_kind": "work_primary",
        "execution_id": "work-primary-20260822T205900JST",
        "predecessor_execution_id": None,
        "lease_generation": generation,
        "fence_token": "primary-fence-token-0123456789abcdef",
        "heartbeat_at": (NOW - timedelta(seconds=age_seconds)).isoformat(),
        "stale_after_seconds": 900,
    }


def _proof() -> WorkModeCASProof:
    return WorkModeCASProof(
        observed_blob_sha=OLD_BLOB,
        acquisition_commit_sha=ACQUIRE_COMMIT,
        acquisition_blob_sha=ACQUIRE_BLOB,
        finalization_commit_sha=FINAL_COMMIT,
        readback_commit_sha=FINAL_COMMIT,
        readback_blob_sha=FINAL_BLOB,
    )


def _acquired(observed: dict) -> dict:
    return {
        "schema_version": 1,
        "status": "running",
        "owner_kind": "work_recovery_automation",
        "execution_id": "work-recovery-20260822T210000JST",
        "predecessor_execution_id": observed["execution_id"],
        "lease_generation": observed["lease_generation"] + 1,
        "fence_token": "recovery-fence-token-fedcba9876543210",
        "heartbeat_at": (NOW - timedelta(seconds=5)).isoformat(),
        "stale_after_seconds": 900,
        "expected_blob_sha": OLD_BLOB,
        "result_commit_sha": ACQUIRE_COMMIT,
        "result_blob_sha": ACQUIRE_BLOB,
        "finalize_expected_blob_sha": ACQUIRE_BLOB,
        "readback_of_commit_sha": ACQUIRE_COMMIT,
        "verified_remote_readback": True,
    }


def test_fresh_owner_suppresses_duplicate_and_never_authorizes_mutation() -> None:
    decision = evaluate_work_mode_monitor(_state(), now=NOW, migration_present=True)

    assert decision.action == "suppress_duplicate"
    assert decision.recovery_eligible is False
    assert decision.mutation_authorized is False


def test_stale_and_checkpointed_states_are_only_recovery_eligible() -> None:
    stale = evaluate_work_mode_monitor(
        _state(age_seconds=901), now=NOW, migration_present=True
    )
    checkpointed = evaluate_work_mode_monitor(
        _state(status="checkpointed"), now=NOW, migration_present=True
    )

    assert stale.action == "recover_stale"
    assert stale.recovery_eligible is True
    assert stale.mutation_authorized is False
    assert checkpointed.action == "recover_checkpoint"
    assert checkpointed.recovery_eligible is True
    assert checkpointed.mutation_authorized is False


def test_missing_migration_bootstraps_instead_of_deadlocking() -> None:
    decision = evaluate_work_mode_monitor(None, now=NOW, migration_present=False)

    assert decision.action == "bootstrap_recovery"
    assert decision.recovery_eligible is True
    assert decision.mutation_authorized is False


def test_malformed_or_future_skewed_state_fails_closed() -> None:
    malformed = _state(age_seconds=901)
    malformed["lease_generation"] = "7"
    future = _state(age_seconds=-121)

    assert (
        evaluate_work_mode_monitor(malformed, now=NOW, migration_present=True).action
        == "unsafe_state"
    )
    assert (
        evaluate_work_mode_monitor(future, now=NOW, migration_present=True).action
        == "unsafe_state"
    )


def test_unverified_completion_recovers_but_strict_external_gate_completes() -> None:
    marker = _state(status="completed")
    unverified = evaluate_work_mode_monitor(marker, now=NOW, migration_present=True)
    verified = evaluate_work_mode_monitor(
        marker,
        now=NOW,
        migration_present=True,
        verified_external_goal=True,
    )

    assert unverified.action == "recover_unverified_completion"
    assert unverified.recovery_eligible is True
    assert verified.action == "goal_complete"
    assert verified.verified_external_goal is True


def test_exact_two_phase_cas_proof_authorizes_recovery() -> None:
    observed = _state(status="checkpointed")
    acquired = _acquired(observed)

    authorization = authorize_work_mode_recovery(observed, acquired, _proof(), now=NOW)

    assert authorization.authorized is True
    assert authorization.lease_generation == observed["lease_generation"] + 1
    assert authorization.fence_token == acquired["fence_token"]


def test_generation_fence_and_remote_readback_mismatches_are_rejected() -> None:
    observed = _state(status="checkpointed")

    skipped = _acquired(observed)
    skipped["lease_generation"] += 1
    assert not authorize_work_mode_recovery(observed, skipped, _proof(), now=NOW).authorized

    replayed_fence = _acquired(observed)
    replayed_fence["fence_token"] = observed["fence_token"]
    assert not authorize_work_mode_recovery(
        observed, replayed_fence, _proof(), now=NOW
    ).authorized

    unverified = _acquired(observed)
    unverified["verified_remote_readback"] = False
    assert not authorize_work_mode_recovery(observed, unverified, _proof(), now=NOW).authorized

    wrong_readback = deepcopy(_proof())
    wrong_readback = WorkModeCASProof(
        **{**wrong_readback.__dict__, "readback_commit_sha": "6" * 40}
    )
    assert not authorize_work_mode_recovery(
        observed, _acquired(observed), wrong_readback, now=NOW
    ).authorized


def test_checked_in_work_state_is_a_fresh_primary_single_writer_lease() -> None:
    state = json.loads(
        (ROOT / "agi" / "WORK_EXECUTION_STATE.json").read_text(encoding="utf-8")
    )
    strategy = json.loads(
        (ROOT / state["strategy_path"]).read_text(encoding="utf-8")
    )
    heartbeat = datetime.fromisoformat(state["heartbeat_at"].replace("Z", "+00:00"))

    decision = evaluate_work_mode_monitor(
        state,
        now=heartbeat + timedelta(seconds=60),
        migration_present=True,
    )

    assert state["owner_kind"] == "work_primary"
    assert state["active_run_id"] == "run-work-mode-handoff-v2"
    assert state["primary_run_contract"]["voluntary_exit_permitted"] is False
    assert state["external_evidence_state"]["agi_claim_supported"] is False
    assert strategy["strategy_is_assumed_correct"] is False
    assert strategy["execution_rules"]["unbounded_deferral_allowed"] is False
    assert strategy["claim_boundary"]["strategy_may_weaken_gate"] is False
    assert decision.action == "suppress_duplicate"
    assert decision.recovery_eligible is False
    assert decision.mutation_authorized is False
