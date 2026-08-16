from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from agi.regression import RegressionPolicy, compare_snapshots

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_MUTABLE_CANDIDATE_FIELDS = {
    "status",
    "scope_states",
    "verified_scope_states",
    "regression_decision_refs",
    "supporting_evidence",
    "contradictory_evidence",
    "rejected_reasons",
}


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


def candidate_spec_payload(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable semantic Candidate body covered by regression approval.

    Evidence/history/state fields change as a Candidate is evaluated and therefore are excluded. The
    identity, target, expected scope, prompt/tool body, dependencies, applicability, and any other
    semantic fields remain covered. A changed implementation must be measured and promoted again.
    """

    return {
        str(key): value
        for key, value in candidate.items()
        if str(key) not in _MUTABLE_CANDIDATE_FIELDS
    }


def candidate_spec_sha256(candidate: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        candidate_spec_payload(candidate),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def record_candidate_regression(
    root: Path,
    *,
    candidate_id: str,
    scope: str,
    baseline_path: Path,
    candidate_path: Path,
    target_task_ids: Sequence[str],
    policy: RegressionPolicy | None = None,
) -> dict[str, Any]:
    """Recompute protected-baseline evidence and persist a scoped Candidate decision.

    The semantic Candidate Evaluator may recommend that a trial looks promising, but it does not
    get to promote itself. Promotion is performed here from explicit measurement files using the
    deterministic AGI regression gate. Approval is bound to the immutable Candidate semantic body;
    editing a prompt, learned program, scope, dependency, or other behavior invalidates the approval.
    Failed decisions remain in history even if a later trial succeeds, so negative evidence cannot be
    overwritten by a model's later self-report.
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

    baseline_measurements = _measurement_array(baseline_path)
    candidate_measurements = _measurement_array(candidate_path)
    selected_policy = policy or RegressionPolicy()
    decision = compare_snapshots(
        baseline_measurements,
        candidate_measurements,
        target_task_ids=targets,
        scope=scope,
        policy=selected_policy,
        candidate_id=candidate_id,
    )

    digest = str(decision["regression_evidence_sha256"])
    record = {
        "schema_version": 2,
        "candidate_id": candidate_id,
        "candidate_spec_sha256": spec_digest,
        "candidate_spec": candidate_spec_payload(candidate_state),
        "scope": scope,
        "target_task_ids": sorted(set(targets)),
        "baseline_measurements": baseline_measurements,
        "candidate_measurements": candidate_measurements,
        "policy": asdict(selected_policy),
        "decision": decision,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    decision_rel = (
        Path(".continual")
        / "candidates"
        / candidate_id
        / "regression"
        / f"{_scope_key(scope)}-{spec_digest[:16]}-{digest}.json"
    )
    decision_path = root / decision_rel
    if decision_path.exists():
        existing = _read_json(decision_path)
        if (
            not isinstance(existing, Mapping)
            or existing.get("decision") != decision
            or existing.get("candidate_spec_sha256") != spec_digest
        ):
            raise ValueError("existing regression evidence record conflicts with recomputed decision")
    else:
        _atomic_json(decision_path, record)

    scope_states = dict(candidate_state.get("scope_states", {}))
    verified_scope_states = dict(candidate_state.get("verified_scope_states", {}))
    history = list(candidate_state.get("regression_decision_refs", []))
    _append_unique(history, decision_rel.as_posix())

    if decision["adopt_candidate"]:
        scope_states[scope] = "VERIFIED_FOR_SCOPE"
        verified_scope_states[scope] = {
            "state": "VERIFIED_FOR_SCOPE",
            "candidate_spec_sha256": spec_digest,
            "regression_evidence_sha256": digest,
            "decision_ref": decision_rel.as_posix(),
        }
        candidate_state["status"] = "active-for-scope"
        supporting = list(candidate_state.get("supporting_evidence", []))
        _append_unique(
            supporting,
            {
                "type": "deterministic_regression_gate",
                "scope": scope,
                "candidate_spec_sha256": spec_digest,
                "regression_evidence_sha256": digest,
                "decision_ref": decision_rel.as_posix(),
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
                "candidate_spec_sha256": spec_digest,
                "regression_evidence_sha256": digest,
                "decision_ref": decision_rel.as_posix(),
                "negative_evidence": decision.get("negative_evidence", []),
                "reasons": decision.get("reasons", []),
            },
        )
        candidate_state["contradictory_evidence"] = contradictory

    candidate_state["scope_states"] = scope_states
    candidate_state["verified_scope_states"] = verified_scope_states
    candidate_state["regression_decision_refs"] = history
    _atomic_json(candidate_json, candidate_state)
    return {
        "candidate_id": candidate_id,
        "candidate_spec_sha256": spec_digest,
        "scope": scope,
        "decision_ref": decision_rel.as_posix(),
        "decision": decision,
        "candidate_status": candidate_state.get("status"),
        "verified_scope_states": verified_scope_states,
    }
