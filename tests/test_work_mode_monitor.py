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
    acquired = {
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
    if "user_input_inbox" in observed:
        acquired["user_input_inbox"] = deepcopy(observed["user_input_inbox"])
    return acquired


def test_fresh_owner_suppresses_duplicate_and_never_authorizes_mutation() -> None:
    decision = evaluate_work_mode_monitor(_state(), now=NOW, migration_present=True)

    assert decision.action == "suppress_duplicate"
    assert decision.recovery_eligible is False
    assert decision.mutation_authorized is False


def test_fresh_owner_surfaces_new_user_input_without_allowing_a_duplicate() -> None:
    state = _state()
    state["user_input_inbox"] = {"highest_acknowledged_revision": 7}

    pending = evaluate_work_mode_monitor(
        state,
        now=NOW,
        migration_present=True,
        latest_user_input_revision=8,
    )
    caught_up = evaluate_work_mode_monitor(
        state,
        now=NOW,
        migration_present=True,
        latest_user_input_revision=7,
    )

    assert pending.action == "suppress_duplicate_surface_user_input"
    assert pending.recovery_eligible is False
    assert pending.mutation_authorized is False
    assert pending.latest_user_input_revision == 8
    assert pending.acknowledged_user_input_revision == 7
    assert pending.unacknowledged_user_input is True
    assert caught_up.action == "suppress_duplicate"
    assert caught_up.unacknowledged_user_input is False


def test_stale_owner_keeps_new_user_input_visible_while_remaining_recoverable() -> None:
    state = _state(age_seconds=901)
    state["user_input_inbox"] = {"highest_acknowledged_revision": 7}

    decision = evaluate_work_mode_monitor(
        state,
        now=NOW,
        migration_present=True,
        latest_user_input_revision=8,
    )

    assert decision.action == "recover_stale"
    assert decision.recovery_eligible is True
    assert decision.mutation_authorized is False
    assert decision.latest_user_input_revision == 8
    assert decision.acknowledged_user_input_revision == 7
    assert decision.unacknowledged_user_input is True


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


def test_repository_gate_does_not_complete_but_user_objective_or_stop_does() -> None:
    marker = _state(status="completed")
    unverified = evaluate_work_mode_monitor(marker, now=NOW, migration_present=True)
    repository_gate_only = evaluate_work_mode_monitor(
        marker,
        now=NOW,
        migration_present=True,
        verified_external_goal=True,
    )
    objective_met = evaluate_work_mode_monitor(
        marker,
        now=NOW,
        migration_present=True,
        user_objective_met=True,
    )
    user_stopped = evaluate_work_mode_monitor(
        marker,
        now=NOW,
        migration_present=True,
        explicit_user_stop=True,
    )

    assert unverified.action == "recover_unverified_completion"
    assert unverified.recovery_eligible is True
    assert repository_gate_only.action == "recover_unverified_completion"
    assert repository_gate_only.verified_external_goal is True
    assert objective_met.action == "goal_complete"
    assert objective_met.user_objective_met is True
    assert user_stopped.action == "user_stopped"
    assert user_stopped.explicit_user_stop is True


def test_exact_two_phase_cas_proof_authorizes_recovery() -> None:
    observed = _state(status="checkpointed")
    acquired = _acquired(observed)

    authorization = authorize_work_mode_recovery(observed, acquired, _proof(), now=NOW)

    assert authorization.authorized is True
    assert authorization.lease_generation == observed["lease_generation"] + 1
    assert authorization.fence_token == acquired["fence_token"]


def test_recovery_acquisition_must_preserve_the_user_input_cursor() -> None:
    observed = _state(status="checkpointed")
    observed["user_input_inbox"] = {"highest_acknowledged_revision": 7}
    preserved = _acquired(observed)

    authorized = authorize_work_mode_recovery(
        observed,
        preserved,
        _proof(),
        now=NOW,
        latest_user_input_revision=8,
    )
    assert authorized.authorized is True

    silently_dropped = _acquired(observed)
    silently_dropped["user_input_inbox"]["highest_acknowledged_revision"] = 8
    dropped = authorize_work_mode_recovery(
        observed,
        silently_dropped,
        _proof(),
        now=NOW,
        latest_user_input_revision=8,
    )
    assert dropped.authorized is False
    assert "changed the acknowledged user input revision" in dropped.reason

    regressed = _acquired(observed)
    regressed["user_input_inbox"]["highest_acknowledged_revision"] = 6
    retreated = authorize_work_mode_recovery(
        observed,
        regressed,
        _proof(),
        now=NOW,
        latest_user_input_revision=8,
    )
    assert retreated.authorized is False
    assert "changed the acknowledged user input revision" in retreated.reason

    missing = _acquired(observed)
    del missing["user_input_inbox"]
    omitted = authorize_work_mode_recovery(
        observed,
        missing,
        _proof(),
        now=NOW,
        latest_user_input_revision=8,
    )
    assert omitted.authorized is False
    assert "user_input_inbox must be an object" in omitted.reason


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


def test_checked_in_monitor_targets_same_task_chat_before_hold_backoff() -> None:
    monitor = json.loads(
        (ROOT / "agi" / "WORK_MODE_MONITOR.json").read_text(encoding="utf-8")
    )

    automation = monitor["configured_automation"]
    policy = monitor["user_input_policy"]
    assert automation["id"] == "6a902927853481919931a1a1a1a7072d"
    assert automation["same_work_project_chat"] is True
    assert automation["destination_mode"] == "existing_task_chat"
    assert policy["task_chat_user_input_ingress_required"] is True
    assert policy["ingress_precedes_unchanged_hold_backoff"] is True
    assert policy["append_requires_expected_revision_and_blob_cas"] is True
    assert policy["append_requires_exact_remote_readback"] is True
    assert policy["new_input_forces_full_refresh"] is True
    assert policy["new_input_does_not_authorize_duplicate_writer"] is True


def test_checked_in_work_state_is_fresh_or_fenced_recovery_eligible() -> None:
    state = json.loads(
        (ROOT / "agi" / "WORK_EXECUTION_STATE.json").read_text(encoding="utf-8")
    )
    inbox = json.loads(
        (ROOT / "agi" / "USER_INPUT_INBOX.json").read_text(encoding="utf-8")
    )
    strategy = json.loads(
        (ROOT / state["strategy_path"]).read_text(encoding="utf-8")
    )
    continuity = json.loads(
        (ROOT / "agi" / "CONTINUOUS_EXECUTION_AUTHORIZATION.json").read_text(
            encoding="utf-8"
        )
    )
    heartbeat = datetime.fromisoformat(state["heartbeat_at"].replace("Z", "+00:00"))

    decision = evaluate_work_mode_monitor(
        state,
        now=heartbeat + timedelta(seconds=60),
        migration_present=True,
        latest_user_input_revision=inbox["revision"],
    )

    assert state["owner_kind"] in {"work_primary", "work_recovery_automation"}
    if state["owner_kind"] == "work_recovery_automation":
        assert state["lease_generation"] >= 1
        assert state["remote_persistence"]["verified_remote_readback"] is True
    assert state["active_run_id"] == state["primary_native_run"]["run_id"]
    assert (
        ROOT / ".continual" / "runs" / state["active_run_id"] / "snapshot.json"
    ).is_file()
    assert state["primary_run_contract"]["voluntary_exit_permitted"] is False
    assert state["external_evidence_state"]["agi_claim_supported"] is False
    assert strategy["strategy_is_assumed_correct"] is False
    assert strategy["execution_rules"]["unbounded_deferral_allowed"] is False
    assert strategy["claim_boundary"]["strategy_may_weaken_gate"] is False
    assert continuity["status"] == "active"
    assert continuity["mechanical_enforcement"][
        "checkpoint_transition_requires_guard_allow"
    ] is True
    assert continuity["mechanical_enforcement"][
        "state_local_forced_boundary_claim_is_evidence"
    ] is False

    if state["status"] == "running":
        acknowledged = state["user_input_inbox"]["highest_acknowledged_revision"]
        expected_action = (
            "suppress_duplicate_surface_user_input"
            if inbox["revision"] > acknowledged
            else "suppress_duplicate"
        )
        assert decision.action == expected_action
        assert decision.unacknowledged_user_input is (
            inbox["revision"] > acknowledged
        )
        assert decision.recovery_eligible is False
    else:
        assert state["status"] in {"checkpointed", "interrupted", "released"}
        assert decision.action == "recover_checkpoint"
        assert decision.recovery_eligible is True
    assert decision.mutation_authorized is False
