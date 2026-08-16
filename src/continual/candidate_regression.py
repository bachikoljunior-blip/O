from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from agi.regression import RegressionPolicy, compare_snapshots

from .trial_ledger import (
    DEFAULT_MAX_TRIAL_ATTEMPTS,
    complete_trial_candidate,
    reserve_trial_candidate,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MUTABLE_CANDIDATE_FIELDS = {
    "status",
    "scope_states",
    "verified_scope_states",
    "regression_decision_refs",
    "supporting_evidence",
    "contradictory_evidence",
    "rejected_reasons",
}


class CandidateIntegrityError(ValueError):
    pass


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _measurement_array(path: Path) -> list[Mapping[str, Any]]:
    value = _read_json(path)
    if isinstance(value, Mapping):
        value = value.get("measurements", value.get("results"))
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise ValueError(f"{path} must contain a measurement array")
    return list(value)


def _append_unique(values: list[Any], item: Any) -> list[Any]:
    encoded = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    existing = {
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        for value in values
    }
    if encoded not in existing:
        values.append(item)
    return values


def _scope_key(scope: str) -> str:
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()[:16]


def _safe_repo_file(root: Path, raw: str, *, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise CandidateIntegrityError(f"{label} must be a non-empty repository-relative path")
    relative = Path(raw)
    if relative.is_absolute():
        raise CandidateIntegrityError(f"{label} must be repository-relative")
    root = root.resolve()
    resolved = (root / relative).resolve()
    if resolved != root and root not in resolved.parents:
        raise CandidateIntegrityError(f"{label} escapes repository")
    if not resolved.is_file():
        raise CandidateIntegrityError(f"{label} does not exist: {raw}")
    return resolved


def candidate_spec_payload(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable semantic Candidate body covered by regression approval."""

    return {
        str(key): value
        for key, value in candidate.items()
        if str(key) not in _MUTABLE_CANDIDATE_FIELDS
    }


def _stable_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def candidate_spec_sha256(candidate: Mapping[str, Any]) -> str:
    return _stable_sha256(candidate_spec_payload(candidate))


def candidate_artifact_manifest(root: Path, candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Hash executable files referenced by a Candidate semantic body.

    Prompt overlays are behavior, not metadata. Binding only their path would allow a model or later
    process to replace the prompt after approval and reuse old regression evidence. Learned tools keep
    their executable program in candidate.json, so they require no external artifact entry.
    """

    manifest: dict[str, Any] = {}
    prompt_path = candidate.get("prompt_path")
    if prompt_path is not None:
        resolved = _safe_repo_file(root, str(prompt_path), label="candidate prompt_path")
        manifest["prompt_path"] = {
            "path": resolved.relative_to(root.resolve()).as_posix(),
            "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        }
    return manifest


def candidate_integrity_sha256(root: Path, candidate: Mapping[str, Any]) -> str:
    return _stable_sha256(
        {
            "candidate_spec": candidate_spec_payload(candidate),
            "artifacts": candidate_artifact_manifest(root, candidate),
        }
    )


def candidate_verified_for_scope(
    root: Path,
    candidate: Mapping[str, Any],
    scope: str,
) -> bool:
    """Verify exact-scope deterministic approval and all semantic bindings.

    Missing old-style bindings simply make a Candidate unavailable. A mismatch after approval is an
    integrity error rather than a new trial result. The underlying decision record is independently
    loaded and checked so a model cannot self-promote by fabricating fields in candidate.json.
    """

    if not isinstance(scope, str) or not scope.strip():
        return False
    candidate_id = str(candidate.get("candidate_id", ""))
    if not _SAFE_ID.fullmatch(candidate_id):
        return False
    scope_states = candidate.get("scope_states")
    verified_states = candidate.get("verified_scope_states")
    if not isinstance(scope_states, Mapping) or not isinstance(verified_states, Mapping):
        return False
    verified = verified_states.get(scope)
    if not (
        candidate.get("status") == "active-for-scope"
        and scope_states.get(scope) == "VERIFIED_FOR_SCOPE"
        and isinstance(verified, Mapping)
        and verified.get("state") == "VERIFIED_FOR_SCOPE"
    ):
        return False

    bound_spec = str(verified.get("candidate_spec_sha256", ""))
    bound_integrity = str(verified.get("candidate_integrity_sha256", ""))
    evidence_digest = str(verified.get("regression_evidence_sha256", ""))
    decision_ref = str(verified.get("decision_ref", ""))
    if not all(_SHA256.fullmatch(value) for value in (bound_spec, bound_integrity, evidence_digest)):
        return False

    current_spec = candidate_spec_sha256(candidate)
    current_integrity = candidate_integrity_sha256(root, candidate)
    if current_spec != bound_spec:
        raise CandidateIntegrityError("Candidate semantic body changed after regression verification")
    if current_integrity != bound_integrity:
        raise CandidateIntegrityError("Candidate executable artifact changed after regression verification")

    expected_prefix = (
        Path(".continual") / "candidates" / candidate_id / "regression"
    ).as_posix() + "/"
    if not decision_ref.startswith(expected_prefix):
        raise CandidateIntegrityError("Candidate decision_ref is outside its regression store")
    decision_path = _safe_repo_file(root, decision_ref, label="Candidate decision_ref")
    record = _read_json(decision_path)
    if not isinstance(record, Mapping):
        raise CandidateIntegrityError("Candidate regression record must be an object")
    decision = record.get("decision")
    if not (
        record.get("candidate_id") == candidate_id
        and record.get("scope") == scope
        and record.get("candidate_spec_sha256") == bound_spec
        and record.get("candidate_integrity_sha256") == bound_integrity
        and isinstance(decision, Mapping)
        and decision.get("adopt_candidate") is True
        and decision.get("regression_evidence_sha256") == evidence_digest
    ):
        raise CandidateIntegrityError("Candidate regression record does not match verified state")
    return True


def record_candidate_regression(
    root: Path,
    *,
    candidate_id: str,
    scope: str,
    baseline_path: Path,
    candidate_path: Path,
    target_task_ids: Sequence[str],
    policy: RegressionPolicy | None = None,
    max_trial_attempts: int = DEFAULT_MAX_TRIAL_ATTEMPTS,
) -> dict[str, Any]:
    """Recompute protected-baseline evidence and persist a bounded scoped Candidate decision.

    Promotion is performed from explicit measurement files using the deterministic AGI regression
    gate. Approval is bound to the immutable Candidate semantic body and its executable artifacts.
    Every distinct exact-scope regression input consumes one persistent trial reservation; exact
    replay reuses the original reservation across process restarts. Editing behavior or supplying
    different measurements therefore requires another bounded trial. Failed decisions remain in
    history even if a later trial succeeds.
    """

    if not isinstance(candidate_id, str) or not _SAFE_ID.fullmatch(candidate_id):
        raise ValueError("candidate_id must contain only letters, digits, '.', '_' or '-'")
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("scope must be a non-empty string")
    targets = [str(value) for value in target_task_ids if str(value)]
    if not targets:
        raise ValueError("at least one target_task_id is required")

    root = root.resolve()
    candidate_json = root / ".continual" / "candidates" / candidate_id / "candidate.json"
    if not candidate_json.is_file():
        raise FileNotFoundError(candidate_json)
    candidate_state = _read_json(candidate_json)
    if not isinstance(candidate_state, dict) or candidate_state.get("candidate_id") != candidate_id:
        raise ValueError("candidate.json identity does not match candidate_id")
    spec_digest = candidate_spec_sha256(candidate_state)
    integrity_digest = candidate_integrity_sha256(root, candidate_state)

    baseline_measurements = _measurement_array(baseline_path)
    candidate_measurements = _measurement_array(candidate_path)
    selected_policy = policy or RegressionPolicy()
    normalized_targets = sorted(set(targets))
    policy_value = asdict(selected_policy)
    trial_input = {
        "kind": "deterministic_candidate_regression",
        "target_task_ids": normalized_targets,
        "baseline_measurements": baseline_measurements,
        "candidate_measurements": candidate_measurements,
        "policy": policy_value,
    }
    reservation = reserve_trial_candidate(
        root,
        candidate_id=candidate_id,
        scope=scope,
        candidate_integrity_sha256=integrity_digest,
        trial_input=trial_input,
        max_attempts=max_trial_attempts,
    )

    decision = compare_snapshots(
        baseline_measurements,
        candidate_measurements,
        target_task_ids=targets,
        scope=scope,
        policy=selected_policy,
        candidate_id=candidate_id,
    )

    digest = str(decision["regression_evidence_sha256"])
    trial_binding = {
        "trial_key": reservation.trial_key,
        "attempt": reservation.attempt,
        "max_attempts": reservation.max_attempts,
    }
    record = {
        "schema_version": 3,
        "candidate_id": candidate_id,
        "candidate_spec_sha256": spec_digest,
        "candidate_integrity_sha256": integrity_digest,
        "candidate_spec": candidate_spec_payload(candidate_state),
        "candidate_artifacts": candidate_artifact_manifest(root, candidate_state),
        "scope": scope,
        "target_task_ids": normalized_targets,
        "baseline_measurements": baseline_measurements,
        "candidate_measurements": candidate_measurements,
        "policy": policy_value,
        "trial": trial_binding,
        "decision": decision,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    decision_rel = (
        Path(".continual")
        / "candidates"
        / candidate_id
        / "regression"
        / f"{_scope_key(scope)}-{integrity_digest[:16]}-{digest}.json"
    )
    decision_path = root / decision_rel
    if decision_path.exists():
        existing = _read_json(decision_path)
        if (
            not isinstance(existing, Mapping)
            or existing.get("decision") != decision
            or existing.get("candidate_spec_sha256") != spec_digest
            or existing.get("candidate_integrity_sha256") != integrity_digest
            or existing.get("trial") != trial_binding
        ):
            raise ValueError("existing regression evidence record conflicts with recomputed decision")
    else:
        _atomic_json(decision_path, record)

    scope_states = dict(candidate_state.get("scope_states", {}))
    verified_scope_states = dict(candidate_state.get("verified_scope_states", {}))
    history = list(candidate_state.get("regression_decision_refs", []))
    _append_unique(history, decision_rel.as_posix())

    evidence_binding = {
        "candidate_spec_sha256": spec_digest,
        "candidate_integrity_sha256": integrity_digest,
        "regression_evidence_sha256": digest,
        "decision_ref": decision_rel.as_posix(),
        **trial_binding,
    }
    if decision["adopt_candidate"]:
        scope_states[scope] = "VERIFIED_FOR_SCOPE"
        verified_scope_states[scope] = {
            "state": "VERIFIED_FOR_SCOPE",
            **evidence_binding,
        }
        candidate_state["status"] = "active-for-scope"
        supporting = list(candidate_state.get("supporting_evidence", []))
        _append_unique(
            supporting,
            {
                "type": "deterministic_regression_gate",
                "scope": scope,
                **evidence_binding,
            },
        )
        candidate_state["supporting_evidence"] = supporting
    else:
        scope_states[scope] = "REMAIN_CANDIDATE"
        verified_scope_states.pop(scope, None)
        if not verified_scope_states:
            candidate_state["status"] = "candidate"
        contradictory = list(candidate_state.get("contradictory_evidence", []))
        _append_unique(
            contradictory,
            {
                "type": "deterministic_regression_gate_failure",
                "scope": scope,
                **evidence_binding,
                "negative_evidence": decision.get("negative_evidence", []),
                "reasons": decision.get("reasons", []),
            },
        )
        candidate_state["contradictory_evidence"] = contradictory

    candidate_state["scope_states"] = scope_states
    candidate_state["verified_scope_states"] = verified_scope_states
    candidate_state["regression_decision_refs"] = history
    _atomic_json(candidate_json, candidate_state)

    completion = complete_trial_candidate(
        root,
        candidate_id=candidate_id,
        scope=scope,
        trial_key=reservation.trial_key,
        outcome={
            "kind": "deterministic_candidate_regression",
            "decision_ref": decision_rel.as_posix(),
            "regression_evidence_sha256": digest,
            "adopt_candidate": bool(decision["adopt_candidate"]),
            "candidate_status": candidate_state.get("status"),
        },
    )
    return {
        "candidate_id": candidate_id,
        "candidate_spec_sha256": spec_digest,
        "candidate_integrity_sha256": integrity_digest,
        "scope": scope,
        "decision_ref": decision_rel.as_posix(),
        "decision": decision,
        "candidate_status": candidate_state.get("status"),
        "verified_scope_states": verified_scope_states,
        "trial": {
            **trial_binding,
            "replayed": reservation.replayed,
            "state": completion.get("state"),
            "outcome_sha256": completion.get("outcome_sha256"),
        },
    }
