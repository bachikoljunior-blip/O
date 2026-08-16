from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

from .candidate_regression import (
    _atomic_json,
    _read_json,
    candidate_verified_for_scope,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class CandidateScopeControlError(ValueError):
    pass


def _candidate_path(root: Path, candidate_id: str) -> Path:
    if not isinstance(candidate_id, str) or not _SAFE_ID.fullmatch(candidate_id):
        raise CandidateScopeControlError("invalid Candidate identity")
    path = root.resolve() / ".continual" / "candidates" / candidate_id / "candidate.json"
    if not path.is_file():
        raise CandidateScopeControlError(f"Candidate does not exist: {candidate_id}")
    return path


def _load_candidate(root: Path, candidate_id: str) -> tuple[Path, dict[str, Any]]:
    path = _candidate_path(root, candidate_id)
    value = _read_json(path)
    if not isinstance(value, dict) or value.get("candidate_id") != candidate_id:
        raise CandidateScopeControlError("candidate.json identity mismatch")
    return path, value


def _reason_digest(reason: str) -> str:
    if not isinstance(reason, str) or not reason.strip():
        raise CandidateScopeControlError("reason must be a non-empty string")
    return hashlib.sha256(reason.strip().encode("utf-8")).hexdigest()


def _append_unique_evidence(candidate: dict[str, Any], item: Mapping[str, Any]) -> None:
    supporting = list(candidate.get("supporting_evidence", []))
    if dict(item) not in supporting:
        supporting.append(dict(item))
    candidate["supporting_evidence"] = supporting


def _has_other_verified_scope(candidate: Mapping[str, Any], suspended_scope: str) -> bool:
    scope_states = candidate.get("scope_states")
    if not isinstance(scope_states, Mapping):
        return False
    return any(
        scope != suspended_scope and state == "VERIFIED_FOR_SCOPE"
        for scope, state in scope_states.items()
    )


def suspend_verified_candidate_scope(
    root: Path,
    *,
    candidate_id: str,
    scope: str,
    reason: str,
) -> dict[str, Any]:
    """Reversibly hide one verified Candidate scope without changing its semantic identity.

    The exact regression binding remains persisted in ``verified_scope_states``. Only mutable
    availability state is changed, so a later resume can re-verify the original decision record and
    Candidate integrity before making the capability callable again. This operation never consumes a
    Candidate trial and never erases supporting or contradictory evidence.
    """

    if not isinstance(scope, str) or not scope.strip():
        raise CandidateScopeControlError("scope must be a non-empty string")
    path, candidate = _load_candidate(root, candidate_id)
    scope_states = dict(candidate.get("scope_states", {}))
    verified_states = candidate.get("verified_scope_states")
    if not isinstance(verified_states, Mapping) or scope not in verified_states:
        raise CandidateScopeControlError("Candidate has no persisted verified binding for scope")

    if scope_states.get(scope) == "SUSPENDED_FOR_SCOPE":
        probe = dict(candidate)
        probe_states = dict(scope_states)
        probe_states[scope] = "VERIFIED_FOR_SCOPE"
        probe["scope_states"] = probe_states
        probe["status"] = "active-for-scope"
        if not candidate_verified_for_scope(root.resolve(), probe, scope):
            raise CandidateScopeControlError("suspended Candidate no longer has a valid verified binding")
        return {
            "candidate_id": candidate_id,
            "scope": scope,
            "state": "SUSPENDED_FOR_SCOPE",
            "replayed": True,
        }

    if not candidate_verified_for_scope(root.resolve(), candidate, scope):
        raise CandidateScopeControlError("Candidate is not currently verified for scope")

    binding = verified_states[scope]
    decision_ref = binding.get("decision_ref") if isinstance(binding, Mapping) else None
    scope_states[scope] = "SUSPENDED_FOR_SCOPE"
    candidate["scope_states"] = scope_states
    if not _has_other_verified_scope(candidate, scope):
        candidate["status"] = "candidate"
    _append_unique_evidence(
        candidate,
        {
            "type": "reversible_scope_suspension",
            "scope": scope,
            "decision_ref": decision_ref,
            "reason_sha256": _reason_digest(reason),
        },
    )
    _atomic_json(path, candidate)
    return {
        "candidate_id": candidate_id,
        "scope": scope,
        "state": "SUSPENDED_FOR_SCOPE",
        "replayed": False,
    }


def resume_verified_candidate_scope(
    root: Path,
    *,
    candidate_id: str,
    scope: str,
    reason: str,
) -> dict[str, Any]:
    """Resume a suspended scope only if its original regression binding still validates exactly."""

    if not isinstance(scope, str) or not scope.strip():
        raise CandidateScopeControlError("scope must be a non-empty string")
    path, candidate = _load_candidate(root, candidate_id)
    scope_states = dict(candidate.get("scope_states", {}))

    if scope_states.get(scope) == "VERIFIED_FOR_SCOPE":
        if not candidate_verified_for_scope(root.resolve(), candidate, scope):
            raise CandidateScopeControlError("Candidate claims verified state without a valid binding")
        return {
            "candidate_id": candidate_id,
            "scope": scope,
            "state": "VERIFIED_FOR_SCOPE",
            "replayed": True,
        }

    if scope_states.get(scope) != "SUSPENDED_FOR_SCOPE":
        raise CandidateScopeControlError("Candidate scope is not suspended")
    verified_states = candidate.get("verified_scope_states")
    if not isinstance(verified_states, Mapping) or scope not in verified_states:
        raise CandidateScopeControlError("suspended Candidate lost its verified binding")

    probe = dict(candidate)
    probe_states = dict(scope_states)
    probe_states[scope] = "VERIFIED_FOR_SCOPE"
    probe["scope_states"] = probe_states
    probe["status"] = "active-for-scope"
    if not candidate_verified_for_scope(root.resolve(), probe, scope):
        raise CandidateScopeControlError("Candidate regression binding failed re-verification")

    binding = verified_states[scope]
    decision_ref = binding.get("decision_ref") if isinstance(binding, Mapping) else None
    candidate["scope_states"] = probe_states
    candidate["status"] = "active-for-scope"
    _append_unique_evidence(
        candidate,
        {
            "type": "reversible_scope_resume",
            "scope": scope,
            "decision_ref": decision_ref,
            "reason_sha256": _reason_digest(reason),
        },
    )
    _atomic_json(path, candidate)
    return {
        "candidate_id": candidate_id,
        "scope": scope,
        "state": "VERIFIED_FOR_SCOPE",
        "replayed": False,
    }
