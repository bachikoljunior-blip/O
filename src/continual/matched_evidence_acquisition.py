from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,191}$")
_SAFE_UNIT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_CAUSE = "MISSING_SAME_CAPABILITY_EVIDENCE"


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def matched_evidence_source_clock(root: Path) -> dict[str, Any]:
    """Return the narrow policy clock used by the one-shot guard.

    The clock deliberately excludes free-form model text and timestamps.  A
    producer can bind the typed gap to these exact durable policy sources, and
    any later source change makes the guard fail closed.
    """

    inbox_path = root / "agi" / "USER_INPUT_INBOX.json"
    inbox: Any = {}
    if inbox_path.is_file():
        try:
            inbox = json.loads(inbox_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            inbox = {}
    revision = inbox.get("revision") if isinstance(inbox, Mapping) else None
    return {
        "schema_version": 1,
        "user_input_revision": revision,
        "user_input_sha256": _file_sha256(inbox_path),
        "effective_directives_sha256": _file_sha256(
            root / "agi" / "USER_DIRECTIVE_EVENTS.json"
        ),
        "work_strategy_sha256": _file_sha256(root / "agi" / "WORK_STRATEGY.json"),
    }


def matched_evidence_authority(root: Path) -> dict[str, Any]:
    state_path = root / "agi" / "WORK_EXECUTION_STATE.json"
    if not state_path.is_file():
        return {"status": "missing"}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"status": "malformed"}
    if not isinstance(state, Mapping):
        return {"status": "malformed"}
    fence = state.get("fence_token")
    return {
        "status": state.get("status"),
        "resume_required": state.get("resume_required"),
        "execution_id": state.get("execution_id"),
        "lease_generation": state.get("lease_generation"),
        "fence_token_digest": (
            hashlib.sha256(fence.encode("utf-8")).hexdigest()
            if isinstance(fence, str) and fence
            else None
        ),
    }


def _no(reason: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": 1,
        "decision": "NO_SCHEDULE",
        "reason": reason,
        "selected_unit": None,
    }
    if idempotency_key is not None:
        result["idempotency_key"] = idempotency_key
    return result


def decide_matched_evidence_acquisition(
    *,
    learn_result: Any,
    proposed_root_unit: Any,
    current_authority: Mapping[str, Any],
    current_source_clock: Mapping[str, Any],
    seen_idempotency_keys: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Select at most one typed same-capability acquisition unit.

    Invalid, ambiguous, mixed, stale, replayed, or prose-only inputs are normal
    fail-closed outcomes.  The function never infers a trigger from prose.
    """

    if not isinstance(learn_result, Mapping) or learn_result.get("decision") != "NO_CHANGE":
        return _no("learn_result_not_typed_no_change")
    gap = learn_result.get("matched_evidence_gap")
    if not isinstance(gap, Mapping) or gap.get("schema_version") != 1:
        return _no("typed_gap_absent_or_unsupported")

    capability_id = gap.get("capability_id")
    if not isinstance(capability_id, str) or _SAFE_ID.fullmatch(capability_id) is None:
        return _no("invalid_capability_identity")
    causes = gap.get("causes")
    if not isinstance(causes, list) or len(causes) != 1 or not isinstance(causes[0], Mapping):
        return _no("rejection_cause_not_sole_and_typed")
    cause = causes[0]
    if cause.get("code") != _CAUSE or cause.get("capability_id") != capability_id:
        return _no("rejection_cause_not_same_capability_missing_evidence")
    requirement = cause.get("evidence_requirement")
    if not isinstance(requirement, Mapping):
        return _no("evidence_requirement_not_typed")
    requirement_id = requirement.get("requirement_id")
    if not isinstance(requirement_id, str) or _SAFE_ID.fullmatch(requirement_id) is None:
        return _no("invalid_evidence_requirement_identity")
    if gap.get("matched_evidence_present") is not False:
        return _no("matched_evidence_not_proven_absent")

    if not isinstance(proposed_root_unit, Mapping):
        return _no("root_unit_not_typed")
    next_capability = proposed_root_unit.get("capability_id")
    if not isinstance(next_capability, str) or _SAFE_ID.fullmatch(next_capability) is None:
        return _no("root_switch_capability_not_typed")
    if next_capability == capability_id:
        return _no("root_not_switching_capability")

    expected_authority = gap.get("expected_authority")
    if not isinstance(expected_authority, Mapping) or dict(expected_authority) != dict(current_authority):
        return _no("authority_changed_or_unbound")
    if current_authority.get("status") != "running" or current_authority.get("resume_required") is not True:
        return _no("authority_not_live_and_resumable")
    if not all(
        current_authority.get(key)
        for key in ("execution_id", "lease_generation", "fence_token_digest")
    ):
        return _no("authority_identity_incomplete")

    expected_clock = gap.get("expected_source_clock")
    if not isinstance(expected_clock, Mapping) or dict(expected_clock) != dict(current_source_clock):
        return _no("source_clock_changed_or_unbound")
    if (
        current_source_clock.get("schema_version") != 1
        or not isinstance(current_source_clock.get("user_input_revision"), int)
        or isinstance(current_source_clock.get("user_input_revision"), bool)
        or not all(
            isinstance(current_source_clock.get(key), str) and current_source_clock.get(key)
            for key in (
                "user_input_sha256",
                "effective_directives_sha256",
                "work_strategy_sha256",
            )
        )
    ):
        return _no("source_clock_identity_incomplete")

    unit = gap.get("acquisition_unit")
    if not isinstance(unit, Mapping):
        return _no("acquisition_unit_not_typed")
    required_unit_fields = ("unit_id", "goal", "scope", "capability_id")
    if any(
        not isinstance(unit.get(key), str) or not unit.get(key).strip()
        for key in required_unit_fields
    ):
        return _no("acquisition_unit_identity_incomplete")
    if _SAFE_UNIT_ID.fullmatch(str(unit.get("unit_id"))) is None:
        return _no("acquisition_unit_id_not_safe")
    if unit.get("component") != "execute" or unit.get("capability_id") != capability_id:
        return _no("acquisition_unit_not_same_capability_execute")
    marker = unit.get("evidence_acquisition")
    if not isinstance(marker, Mapping) or marker.get("schema_version") != 1:
        return _no("acquisition_marker_not_typed")
    if marker.get("requirement_id") != requirement_id or marker.get("attempt") != 1:
        return _no("acquisition_marker_not_first_exact_requirement_attempt")

    key_material = {
        "schema_version": 1,
        "capability_id": capability_id,
        "requirement": requirement,
        "acquisition_unit": unit,
        "source_clock": dict(current_source_clock),
        "authority": dict(current_authority),
    }
    idempotency_key = "matched-evidence-acquisition:" + _digest(key_material)
    if idempotency_key in seen_idempotency_keys:
        return _no("idempotency_replay_suppressed", idempotency_key=idempotency_key)

    selected = deepcopy(dict(unit))
    selected["matched_evidence_idempotency_key"] = idempotency_key
    return {
        "schema_version": 1,
        "decision": "SCHEDULE_ONCE",
        "reason": "typed_sole_missing_same_capability_evidence_before_domain_switch",
        "idempotency_key": idempotency_key,
        "selected_unit": selected,
        "receipt": {
            "schema_version": 1,
            "record_type": "matched_evidence_acquisition_decision",
            "decision": "SCHEDULE_ONCE",
            "idempotency_key": idempotency_key,
            "capability_id": capability_id,
            "next_capability_id": next_capability,
            "evidence_requirement": deepcopy(dict(requirement)),
            "source_clock": deepcopy(dict(current_source_clock)),
            "authority": deepcopy(dict(current_authority)),
            "selected_unit": selected,
            "candidate_activation": False,
            "upper_objective_achieved": False,
            "agi_achieved": False,
        },
    }
