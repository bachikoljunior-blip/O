from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from agi.regression import RegressionPolicy

from . import candidate_regression as _candidate_regression
from . import openai_client as _openai_client
from .trial_ledger import complete_trial_candidate, reserve_trial_candidate

_DEFAULT_MAX_TRIAL_ATTEMPTS = 3
_MAX_TRIAL_ATTEMPTS = 8
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_INSTALLED = False
_ORIGINAL_RECORD_CANDIDATE_REGRESSION = _candidate_regression.record_candidate_regression
_ORIGINAL_MODEL_CALL = _openai_client.ModelClient.call


class CandidateTrialIntegrationError(ValueError):
    pass


def _candidate_path(root: Path, candidate_id: str) -> Path:
    if not isinstance(candidate_id, str) or not _SAFE_ID.fullmatch(candidate_id):
        raise CandidateTrialIntegrationError("invalid Candidate identity in trial integration")
    return root.resolve() / ".continual" / "candidates" / candidate_id / "candidate.json"


def _candidate_state(root: Path, candidate_id: str) -> dict[str, Any]:
    path = _candidate_path(root, candidate_id)
    if not path.is_file():
        raise CandidateTrialIntegrationError(f"Candidate does not exist: {candidate_id}")
    value = _candidate_regression._read_json(path)
    if not isinstance(value, dict) or value.get("candidate_id") != candidate_id:
        raise CandidateTrialIntegrationError("candidate.json identity mismatch")
    return value


def _max_attempts(candidate: Mapping[str, Any]) -> int:
    raw = candidate.get("max_trial_attempts", _DEFAULT_MAX_TRIAL_ATTEMPTS)
    if not isinstance(raw, int) or isinstance(raw, bool) or not 1 <= raw <= _MAX_TRIAL_ATTEMPTS:
        raise CandidateTrialIntegrationError(
            f"max_trial_attempts must be an integer from 1 through {_MAX_TRIAL_ATTEMPTS}"
        )
    return raw


def _scope(payload: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    execution_unit = payload.get("execution_unit")
    unit_scope = execution_unit.get("scope") if isinstance(execution_unit, Mapping) else None
    raw = result.get("scope") or unit_scope or payload.get("target_component")
    if not isinstance(raw, str) or not raw.strip():
        raise CandidateTrialIntegrationError("TRIAL_CANDIDATE requires an exact non-empty scope")
    return raw


def _selected_candidate_id(result: Mapping[str, Any]) -> str:
    raw = result.get("candidate_id") or result.get("selected_candidate_id")
    if not isinstance(raw, str) or not _SAFE_ID.fullmatch(raw):
        raise CandidateTrialIntegrationError("TRIAL_CANDIDATE requires a safe candidate_id")
    return raw


def _validate_candidate_target(
    candidate: Mapping[str, Any],
    target_component: Any,
) -> None:
    target = candidate.get("target_component")
    if not isinstance(target_component, str) or target not in {target_component, "*"}:
        raise CandidateTrialIntegrationError("Candidate does not target the upcoming component")


def _reservation_payload(reservation: Any, integrity_digest: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "candidate_id": reservation.candidate_id,
        "scope": reservation.scope,
        "trial_key": reservation.trial_key,
        "attempt": reservation.attempt,
        "max_attempts": reservation.max_attempts,
        "replayed": reservation.replayed,
        "candidate_integrity_sha256": integrity_digest,
    }


def apply_runtime_candidate_trial_ledger(
    root: Path,
    *,
    component: str,
    payload: Mapping[str, Any],
    output: Any,
) -> Any:
    """Bind live model-selected Candidate trials to the persistent bounded ledger.

    The model may propose a scoped trial, but it cannot choose an unbounded budget and this function
    never promotes the Candidate. A restart of the same run/unit reuses the same reservation. A new
    run or materially different execution unit consumes a new scope-local attempt.
    """

    if component != "candidate_evaluate" or not isinstance(output, Mapping):
        return output
    result = output.get("result")
    if not isinstance(result, Mapping):
        return output
    mode = payload.get("mode")
    guarded_output = deepcopy(dict(output))
    guarded_result = deepcopy(dict(result))

    if mode == "pre-application":
        decision = guarded_result.get("decision") or guarded_result.get("scope_state")
        if decision != "TRIAL_CANDIDATE":
            return output
        candidate_id = _selected_candidate_id(guarded_result)
        scope = _scope(payload, guarded_result)
        candidate = _candidate_state(root, candidate_id)
        _validate_candidate_target(candidate, payload.get("target_component"))
        integrity_digest = _candidate_regression.candidate_integrity_sha256(root.resolve(), candidate)
        trial_input = {
            "kind": "continual_runtime_candidate_execution",
            "run_id": payload.get("run_id"),
            "target_component": payload.get("target_component"),
            "execution_unit": deepcopy(payload.get("execution_unit", {})),
            "candidate_id": candidate_id,
            "scope": scope,
        }
        reservation = reserve_trial_candidate(
            root,
            candidate_id=candidate_id,
            scope=scope,
            candidate_integrity_sha256=integrity_digest,
            trial_input=trial_input,
            max_attempts=_max_attempts(candidate),
        )
        guarded_result["trial_reservation"] = _reservation_payload(
            reservation,
            integrity_digest,
        )
        guarded_output["result"] = guarded_result
        return guarded_output

    if mode != "post-result":
        return output
    pre_application = payload.get("pre_application")
    if not isinstance(pre_application, Mapping):
        return output
    raw_reservation = pre_application.get("trial_reservation")
    if not isinstance(raw_reservation, Mapping):
        return output
    candidate_id = str(raw_reservation.get("candidate_id", ""))
    scope = str(raw_reservation.get("scope", ""))
    trial_key = str(raw_reservation.get("trial_key", ""))
    bound_integrity = str(raw_reservation.get("candidate_integrity_sha256", ""))
    if not (
        _SAFE_ID.fullmatch(candidate_id)
        and scope.strip()
        and _SHA256.fullmatch(trial_key)
        and _SHA256.fullmatch(bound_integrity)
    ):
        raise CandidateTrialIntegrationError("malformed persisted trial reservation")
    candidate = _candidate_state(root, candidate_id)
    _validate_candidate_target(candidate, payload.get("target_component"))
    current_integrity = _candidate_regression.candidate_integrity_sha256(root.resolve(), candidate)
    if current_integrity != bound_integrity:
        raise CandidateTrialIntegrationError("Candidate changed between reservation and completion")
    clean_result = {
        str(key): deepcopy(value)
        for key, value in guarded_result.items()
        if str(key) != "trial_completion"
    }
    outcome = {
        "kind": "continual_runtime_candidate_result",
        "candidate_id": candidate_id,
        "scope": scope,
        "target_component": payload.get("target_component"),
        "candidate_evaluator_result": clean_result,
        "actual_result": deepcopy(payload.get("actual_result", {})),
    }
    completed = complete_trial_candidate(
        root,
        candidate_id=candidate_id,
        scope=scope,
        trial_key=trial_key,
        outcome=outcome,
    )
    guarded_result["trial_completion"] = {
        "schema_version": 1,
        "candidate_id": candidate_id,
        "scope": scope,
        "trial_key": trial_key,
        "state": completed.get("state"),
        "outcome_sha256": completed.get("outcome_sha256"),
    }
    guarded_output["result"] = guarded_result
    return guarded_output


def bounded_record_candidate_regression(
    root: Path,
    *,
    candidate_id: str,
    scope: str,
    baseline_path: Path,
    candidate_path: Path,
    target_task_ids: Sequence[str],
    policy: RegressionPolicy | None = None,
    max_trial_attempts: int = _DEFAULT_MAX_TRIAL_ATTEMPTS,
) -> dict[str, Any]:
    """Run the deterministic regression gate under one bounded, replay-safe reservation."""

    root = root.resolve()
    state_path = _candidate_path(root, candidate_id)
    original_state = _candidate_state(root, candidate_id)
    integrity_digest = _candidate_regression.candidate_integrity_sha256(root, original_state)
    baseline_measurements = _candidate_regression._measurement_array(Path(baseline_path))
    candidate_measurements = _candidate_regression._measurement_array(Path(candidate_path))
    selected_policy = policy or RegressionPolicy()
    targets = [str(value) for value in target_task_ids if str(value)]
    if not targets:
        raise ValueError("at least one target_task_id is required")
    trial_input = {
        "kind": "deterministic_candidate_regression",
        "target_task_ids": sorted(set(targets)),
        "baseline_measurements": baseline_measurements,
        "candidate_measurements": candidate_measurements,
        "policy": asdict(selected_policy),
    }
    reservation = reserve_trial_candidate(
        root,
        candidate_id=candidate_id,
        scope=scope,
        candidate_integrity_sha256=integrity_digest,
        trial_input=trial_input,
        max_attempts=max_trial_attempts,
    )

    try:
        report = _ORIGINAL_RECORD_CANDIDATE_REGRESSION(
            root,
            candidate_id=candidate_id,
            scope=scope,
            baseline_path=Path(baseline_path),
            candidate_path=Path(candidate_path),
            target_task_ids=targets,
            policy=selected_policy,
        )
        if report.get("candidate_integrity_sha256") != integrity_digest:
            raise CandidateTrialIntegrationError(
                "deterministic regression used a different Candidate integrity identity"
            )
        decision = report.get("decision")
        if not isinstance(decision, Mapping):
            raise CandidateTrialIntegrationError("deterministic regression returned no decision")
        evidence_digest = str(decision.get("regression_evidence_sha256", ""))
        if not _SHA256.fullmatch(evidence_digest):
            raise CandidateTrialIntegrationError("regression evidence digest is malformed")
        outcome = {
            "kind": "deterministic_candidate_regression_result",
            "candidate_id": candidate_id,
            "candidate_integrity_sha256": integrity_digest,
            "scope": scope,
            "decision_ref": report.get("decision_ref"),
            "regression_evidence_sha256": evidence_digest,
            "adopt_candidate": decision.get("adopt_candidate") is True,
        }
        completed = complete_trial_candidate(
            root,
            candidate_id=candidate_id,
            scope=scope,
            trial_key=reservation.trial_key,
            outcome=outcome,
        )
    except Exception:
        _candidate_regression._atomic_json(state_path, original_state)
        raise

    report["trial"] = {
        **_reservation_payload(reservation, integrity_digest),
        "state": completed.get("state"),
        "outcome_sha256": completed.get("outcome_sha256"),
    }
    return report


def guarded_model_call(
    self: Any,
    component: str,
    payload: dict[str, Any],
    prompt_path: str | None = None,
) -> dict[str, Any]:
    output = _ORIGINAL_MODEL_CALL(self, component, payload, prompt_path=prompt_path)
    return apply_runtime_candidate_trial_ledger(
        self.root,
        component=component,
        payload=payload,
        output=output,
    )


def install_trial_integration() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _candidate_regression.record_candidate_regression = bounded_record_candidate_regression
    _openai_client.ModelClient.call = guarded_model_call
    _INSTALLED = True
