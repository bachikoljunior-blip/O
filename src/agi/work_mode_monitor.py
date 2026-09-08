from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


_SHA = re.compile(r"^[0-9a-f]{40,64}$")
_EXECUTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,191}$")
_RECOVERABLE_STATUSES = {"released", "interrupted", "checkpointed"}
_COMPLETION_STATUSES = {"completed", "goal_complete", "verified_agi"}


class WorkModeMonitorError(ValueError):
    """Raised when monitor inputs cannot be interpreted without guessing."""


@dataclass(frozen=True)
class WorkModeMonitorDecision:
    action: str
    reason: str
    status: str
    heartbeat_at: str | None
    heartbeat_age_seconds: float | None
    stale_after_seconds: int | None
    lease_generation: int | None
    execution_id: str | None
    owner_kind: str | None
    recovery_eligible: bool
    mutation_authorized: bool
    verified_external_goal: bool
    user_objective_met: bool
    explicit_user_stop: bool
    latest_user_input_revision: int | None
    acknowledged_user_input_revision: int | None
    unacknowledged_user_input: bool

    def descriptor(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkModeCASProof:
    observed_blob_sha: str
    acquisition_commit_sha: str
    acquisition_blob_sha: str
    finalization_commit_sha: str
    readback_commit_sha: str
    readback_blob_sha: str


@dataclass(frozen=True)
class WorkModeRecoveryAuthorization:
    authorized: bool
    reason: str
    execution_id: str | None
    lease_generation: int | None
    fence_token: str | None

    def descriptor(self) -> dict[str, Any]:
        return asdict(self)


def _parse_timestamp(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise WorkModeMonitorError(f"{field} must be a non-empty ISO-8601 timestamp")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise WorkModeMonitorError(f"{field} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WorkModeMonitorError(f"{field} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _parse_now(now: datetime | str) -> datetime:
    if isinstance(now, datetime):
        if now.tzinfo is None or now.utcoffset() is None:
            raise WorkModeMonitorError("now must be timezone-aware")
        return now.astimezone(timezone.utc)
    return _parse_timestamp(now, field="now")


def _stale_after(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 60 <= value <= 86_400:
        raise WorkModeMonitorError("stale_after_seconds must be an integer from 60 through 86400")
    return value


def _generation(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise WorkModeMonitorError("lease_generation must be a non-negative integer")
    return value


def _user_input_revision(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise WorkModeMonitorError(f"{field} must be a non-negative integer")
    return value


def _acknowledged_user_input_revision(state: Mapping[str, Any]) -> int:
    inbox = state.get("user_input_inbox")
    if not isinstance(inbox, Mapping):
        raise WorkModeMonitorError(
            "user_input_inbox must be an object when latest user input is observed"
        )
    return _user_input_revision(
        inbox.get("highest_acknowledged_revision"),
        field="user_input_inbox.highest_acknowledged_revision",
    )


def _required_text(state: Mapping[str, Any], key: str) -> str:
    value = state.get(key)
    if not isinstance(value, str) or not value.strip():
        raise WorkModeMonitorError(f"{key} must be a non-empty string")
    return value.strip()


def _execution_id(state: Mapping[str, Any]) -> str:
    value = _required_text(state, "execution_id")
    if not _EXECUTION_ID.fullmatch(value):
        raise WorkModeMonitorError("execution_id has an invalid format")
    return value


def _fence_token(state: Mapping[str, Any]) -> str:
    value = _required_text(state, "fence_token")
    if len(value) < 24 or len(value) > 256 or any(character.isspace() for character in value):
        raise WorkModeMonitorError("fence_token must be an opaque 24-256 character token")
    return value


def _decision(
    *,
    action: str,
    reason: str,
    status: str = "",
    heartbeat_at: str | None = None,
    heartbeat_age_seconds: float | None = None,
    stale_after_seconds: int | None = None,
    lease_generation: int | None = None,
    execution_id: str | None = None,
    owner_kind: str | None = None,
    recovery_eligible: bool = False,
    verified_external_goal: bool = False,
    user_objective_met: bool = False,
    explicit_user_stop: bool = False,
    latest_user_input_revision: int | None = None,
    acknowledged_user_input_revision: int | None = None,
    unacknowledged_user_input: bool = False,
) -> WorkModeMonitorDecision:
    return WorkModeMonitorDecision(
        action=action,
        reason=reason,
        status=status,
        heartbeat_at=heartbeat_at,
        heartbeat_age_seconds=heartbeat_age_seconds,
        stale_after_seconds=stale_after_seconds,
        lease_generation=lease_generation,
        execution_id=execution_id,
        owner_kind=owner_kind,
        recovery_eligible=recovery_eligible,
        mutation_authorized=False,
        verified_external_goal=verified_external_goal,
        user_objective_met=user_objective_met,
        explicit_user_stop=explicit_user_stop,
        latest_user_input_revision=latest_user_input_revision,
        acknowledged_user_input_revision=acknowledged_user_input_revision,
        unacknowledged_user_input=unacknowledged_user_input,
    )


def evaluate_work_mode_monitor(
    state: Mapping[str, Any] | None,
    *,
    now: datetime | str,
    migration_present: bool,
    verified_external_goal: bool = False,
    user_objective_met: bool = False,
    explicit_user_stop: bool = False,
    latest_user_input_revision: int | None = None,
    max_future_heartbeat_skew_seconds: int = 120,
) -> WorkModeMonitorDecision:
    """Classify Work-mode liveness without granting mutation authority.

    Recovery eligibility and mutation authorization are intentionally separate. Even a stale lease
    remains read-only until :func:`authorize_work_mode_recovery` validates an exact generation+1
    acquisition, a new fence, the expected prior blob, and a two-phase remote readback proof.
    """

    if not isinstance(migration_present, bool):
        raise WorkModeMonitorError("migration_present must be boolean")
    for field, value in (
        ("verified_external_goal", verified_external_goal),
        ("user_objective_met", user_objective_met),
        ("explicit_user_stop", explicit_user_stop),
    ):
        if not isinstance(value, bool):
            raise WorkModeMonitorError(f"{field} must be boolean")
    if (
        not isinstance(max_future_heartbeat_skew_seconds, int)
        or isinstance(max_future_heartbeat_skew_seconds, bool)
        or not 0 <= max_future_heartbeat_skew_seconds <= 3_600
    ):
        raise WorkModeMonitorError(
            "max_future_heartbeat_skew_seconds must be an integer from 0 through 3600"
        )
    if latest_user_input_revision is not None:
        latest_user_input_revision = _user_input_revision(
            latest_user_input_revision,
            field="latest_user_input_revision",
        )
    now_utc = _parse_now(now)

    if explicit_user_stop:
        return _decision(
            action="user_stopped",
            reason="the user explicitly stopped the Work execution",
            verified_external_goal=verified_external_goal,
            explicit_user_stop=True,
            latest_user_input_revision=latest_user_input_revision,
        )
    if user_objective_met:
        return _decision(
            action="goal_complete",
            reason="the user's actual upper-level objective was independently established as met",
            verified_external_goal=verified_external_goal,
            user_objective_met=True,
            latest_user_input_revision=latest_user_input_revision,
        )

    if not migration_present or state is None:
        return _decision(
            action="bootstrap_recovery",
            reason="Work migration or execution state is absent and requires non-duplicating bootstrap",
            status="missing",
            recovery_eligible=True,
            latest_user_input_revision=latest_user_input_revision,
        )
    if not isinstance(state, Mapping):
        return _decision(action="unsafe_state", reason="Work execution state must be an object")

    status_value = state.get("status")
    if not isinstance(status_value, str) or not status_value.strip():
        return _decision(action="unsafe_state", reason="status must be a non-empty string")
    status = status_value.strip().lower()

    try:
        lease_generation = _generation(state.get("lease_generation"))
        execution_id = _execution_id(state)
        owner_kind = _required_text(state, "owner_kind")
        _fence_token(state)
        stale_after = _stale_after(state.get("stale_after_seconds"))
        heartbeat = _parse_timestamp(state.get("heartbeat_at"), field="heartbeat_at")
        acknowledged_user_input_revision = (
            _acknowledged_user_input_revision(state)
            if latest_user_input_revision is not None
            else None
        )
    except WorkModeMonitorError as exc:
        return _decision(
            action="unsafe_state",
            reason=str(exc),
            status=status,
            latest_user_input_revision=latest_user_input_revision,
        )

    if (
        latest_user_input_revision is not None
        and acknowledged_user_input_revision is not None
        and acknowledged_user_input_revision > latest_user_input_revision
    ):
        return _decision(
            action="unsafe_state",
            reason="acknowledged user input revision exceeds the latest observed revision",
            status=status,
            latest_user_input_revision=latest_user_input_revision,
            acknowledged_user_input_revision=acknowledged_user_input_revision,
        )
    unacknowledged_user_input = bool(
        latest_user_input_revision is not None
        and acknowledged_user_input_revision is not None
        and latest_user_input_revision > acknowledged_user_input_revision
    )

    age = (now_utc - heartbeat).total_seconds()
    common = {
        "status": status,
        "heartbeat_at": heartbeat.isoformat(),
        "heartbeat_age_seconds": age,
        "stale_after_seconds": stale_after,
        "lease_generation": lease_generation,
        "execution_id": execution_id,
        "owner_kind": owner_kind,
        "latest_user_input_revision": latest_user_input_revision,
        "acknowledged_user_input_revision": acknowledged_user_input_revision,
        "unacknowledged_user_input": unacknowledged_user_input,
    }
    if age < -max_future_heartbeat_skew_seconds:
        return _decision(
            action="unsafe_state",
            reason="heartbeat is implausibly far in the future",
            **common,
        )

    if status == "running":
        if age <= stale_after:
            if unacknowledged_user_input:
                return _decision(
                    action="suppress_duplicate_surface_user_input",
                    reason=(
                        "the running Work owner has a fresh heartbeat, so no duplicate "
                        "writer is allowed, but newer user input must be surfaced to it"
                    ),
                    **common,
                )
            return _decision(
                action="suppress_duplicate",
                reason="the running Work owner has a fresh heartbeat",
                **common,
            )
        return _decision(
            action="recover_stale",
            reason="the running Work heartbeat is older than its persisted stale threshold",
            recovery_eligible=True,
            **common,
        )

    if status in _RECOVERABLE_STATUSES:
        return _decision(
            action="recover_checkpoint",
            reason=f"lease status {status} permits fenced continuation",
            recovery_eligible=True,
            **common,
        )

    if status in _COMPLETION_STATUSES:
        return _decision(
            action="recover_unverified_completion",
            reason="completion is not backed by the user's actual objective being met or an explicit user stop",
            verified_external_goal=verified_external_goal,
            recovery_eligible=True,
            **common,
        )

    return _decision(
        action="unsafe_state",
        reason=f"unrecognized Work lease status: {status}",
        **common,
    )


def _valid_sha(value: str) -> bool:
    return bool(_SHA.fullmatch(value))


def _denied(
    reason: str,
    *,
    state: Mapping[str, Any] | None = None,
) -> WorkModeRecoveryAuthorization:
    execution_id = state.get("execution_id") if isinstance(state, Mapping) else None
    lease_generation = state.get("lease_generation") if isinstance(state, Mapping) else None
    fence_token = state.get("fence_token") if isinstance(state, Mapping) else None
    return WorkModeRecoveryAuthorization(
        authorized=False,
        reason=reason,
        execution_id=execution_id if isinstance(execution_id, str) else None,
        lease_generation=(
            lease_generation
            if isinstance(lease_generation, int) and not isinstance(lease_generation, bool)
            else None
        ),
        fence_token=fence_token if isinstance(fence_token, str) else None,
    )


def authorize_work_mode_recovery(
    observed_state: Mapping[str, Any],
    acquired_state: Mapping[str, Any],
    proof: WorkModeCASProof,
    *,
    now: datetime | str,
    latest_user_input_revision: int | None = None,
    max_acquisition_age_seconds: int = 300,
) -> WorkModeRecoveryAuthorization:
    """Authorize a recovered writer only after exact two-phase CAS readback.

    ``result_commit_sha`` and ``result_blob_sha`` identify the first state-only acquisition commit.
    A second state-only CAS records those values and ``verified_remote_readback=true``. The caller then
    supplies the finalization commit/blob observed from the remote branch. This avoids the impossible
    requirement for a file to contain the SHA of the commit that contains that same file.
    """

    if not isinstance(observed_state, Mapping) or not isinstance(acquired_state, Mapping):
        return _denied("observed and acquired states must be objects")
    if (
        not isinstance(max_acquisition_age_seconds, int)
        or isinstance(max_acquisition_age_seconds, bool)
        or not 1 <= max_acquisition_age_seconds <= 3_600
    ):
        raise WorkModeMonitorError(
            "max_acquisition_age_seconds must be an integer from 1 through 3600"
        )
    proof_values = asdict(proof)
    if not all(isinstance(value, str) and _valid_sha(value) for value in proof_values.values()):
        return _denied("CAS proof contains an invalid Git object SHA", state=acquired_state)
    if proof.finalization_commit_sha != proof.readback_commit_sha:
        return _denied("remote readback commit does not equal the finalization commit", state=acquired_state)
    if proof.acquisition_blob_sha == proof.readback_blob_sha:
        return _denied("finalization did not produce a distinct verified state blob", state=acquired_state)

    observed = evaluate_work_mode_monitor(
        observed_state,
        now=now,
        migration_present=True,
        verified_external_goal=False,
        latest_user_input_revision=latest_user_input_revision,
    )
    if not observed.recovery_eligible:
        return _denied(
            f"observed state is not recovery eligible: {observed.action}", state=acquired_state
        )
    acquired = evaluate_work_mode_monitor(
        acquired_state,
        now=now,
        migration_present=True,
        verified_external_goal=False,
        latest_user_input_revision=latest_user_input_revision,
    )
    if acquired.action not in {
        "suppress_duplicate",
        "suppress_duplicate_surface_user_input",
    }:
        return _denied(
            (
                "acquired state is not a fresh running lease: "
                f"{acquired.action}: {acquired.reason}"
            ),
            state=acquired_state,
        )
    if acquired.heartbeat_age_seconds is None or acquired.heartbeat_age_seconds < 0:
        return _denied("acquired heartbeat must not be in the future", state=acquired_state)
    if acquired.heartbeat_age_seconds > max_acquisition_age_seconds:
        return _denied("acquired heartbeat is too old for authorization", state=acquired_state)

    if latest_user_input_revision is not None:
        observed_acknowledged = observed.acknowledged_user_input_revision
        acquired_acknowledged = acquired.acknowledged_user_input_revision
        if acquired_acknowledged != observed_acknowledged:
            return _denied(
                "recovery acquisition changed the acknowledged user input revision",
                state=acquired_state,
            )

    observed_generation = observed_state.get("lease_generation")
    acquired_generation = acquired_state.get("lease_generation")
    if acquired_generation != observed_generation + 1:
        return _denied("lease_generation must increase by exactly one", state=acquired_state)
    observed_execution = observed_state.get("execution_id")
    acquired_execution = acquired_state.get("execution_id")
    if acquired_execution == observed_execution:
        return _denied("recovery execution_id must be new", state=acquired_state)
    if acquired_state.get("predecessor_execution_id") != observed_execution:
        return _denied("predecessor_execution_id does not bind the observed owner", state=acquired_state)
    if acquired_state.get("owner_kind") != "work_recovery_automation":
        return _denied("owner_kind is not work_recovery_automation", state=acquired_state)
    if acquired_state.get("fence_token") == observed_state.get("fence_token"):
        return _denied("recovery fence_token must be new", state=acquired_state)

    expected_fields = {
        "expected_blob_sha": proof.observed_blob_sha,
        "result_commit_sha": proof.acquisition_commit_sha,
        "result_blob_sha": proof.acquisition_blob_sha,
        "finalize_expected_blob_sha": proof.acquisition_blob_sha,
        "readback_of_commit_sha": proof.acquisition_commit_sha,
        "verified_remote_readback": True,
    }
    for field, expected in expected_fields.items():
        if acquired_state.get(field) != expected:
            return _denied(f"{field} does not match the CAS proof", state=acquired_state)

    return WorkModeRecoveryAuthorization(
        authorized=True,
        reason="exact generation+1 lease and two-phase remote readback were verified",
        execution_id=acquired_execution,
        lease_generation=acquired_generation,
        fence_token=acquired_state.get("fence_token"),
    )
