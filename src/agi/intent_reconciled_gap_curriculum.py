from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import execute_program
from agi.durable_state_rehydration import _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest
from agi.multi_gap_autonomous_curriculum import (
    _all_candidate_ids,
    _numeric_to_string_spec,
    _square_spec,
    _trial_snapshot,
)
from agi.receipt_reconciled_curriculum import (
    ReceiptReconciliationError,
    _load_valid_receipt,
    _write_receipt,
)
from agi.resumable_multi_gap_curriculum import (
    _PHASE_ORDER,
    _initial_state,
    _load_state,
    _run_phase,
    _safe_campaign_id,
    _state_path,
    _validate_state,
)
from continual.acquired_program_tools import AcquiredProgramToolSpec
from continual.candidate_regression import candidate_verified_for_scope


class IntentReconciliationError(RuntimeError):
    pass


class SimulatedPreReceiptInterruption(RuntimeError):
    """Test-only process loss after a gap commit but before its completion receipt."""


_INTENT_SCHEMA = 1
_RECOVERABLE_GAP_PHASES = {"square_gap", "string_gap"}


def _intent_path(root: Path, campaign_id: str, phase: str) -> Path:
    return (
        root
        / ".continual"
        / "system"
        / "multi-gap-curricula"
        / f"{campaign_id}.intents"
        / f"{phase}.json"
    )


def _snapshot_for_ids(root: Path, candidate_ids: list[str]) -> dict[str, Any]:
    tree = _tree_snapshot(root, candidate_ids)
    trials = _trial_snapshot(root, candidate_ids)
    return {
        "candidate_ids": candidate_ids,
        "candidate_tree_digest": _digest(tree),
        "trial_ledgers_digest": _digest(trials),
        "aggregate_digest": _digest(
            {
                "candidate_ids": candidate_ids,
                "candidate_tree": tree,
                "trial_ledgers": trials,
            }
        ),
    }


def _write_intent(
    root: Path,
    *,
    campaign_id: str,
    phase: str,
    seed_commitment: str,
    attempt: int,
) -> dict[str, Any]:
    prior_ids = _all_candidate_ids(root)
    intent: dict[str, Any] = {
        "schema_version": _INTENT_SCHEMA,
        "campaign_id": campaign_id,
        "phase": phase,
        "seed_commitment": seed_commitment,
        "attempt": attempt,
        "pre_state": _snapshot_for_ids(root, prior_ids),
    }
    intent["intent_digest"] = _digest(intent)
    path = _intent_path(root, campaign_id, phase)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != intent:
            raise IntentReconciliationError(
                f"phase intent already exists with different content: {phase}"
            )
        return intent
    _atomic_json(path, intent)
    return intent


def _load_valid_intent(
    root: Path,
    *,
    campaign_id: str,
    phase: str,
    seed_commitment: str,
    expected_attempt: int,
) -> dict[str, Any] | None:
    path = _intent_path(root, campaign_id, phase)
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise IntentReconciliationError("phase intent must be an object")
    if value.get("schema_version") != _INTENT_SCHEMA:
        raise IntentReconciliationError("phase intent schema mismatch")
    if value.get("campaign_id") != campaign_id or value.get("phase") != phase:
        raise IntentReconciliationError("phase intent identity mismatch")
    if value.get("seed_commitment") != seed_commitment:
        raise IntentReconciliationError("phase intent seed commitment mismatch")
    if value.get("attempt") != expected_attempt:
        raise IntentReconciliationError("phase intent attempt mismatch")
    supplied_digest = value.get("intent_digest")
    unsigned = {key: item for key, item in value.items() if key != "intent_digest"}
    if not isinstance(supplied_digest, str) or supplied_digest != _digest(unsigned):
        raise IntentReconciliationError("phase intent digest mismatch")
    pre_state = value.get("pre_state")
    if not isinstance(pre_state, dict) or not isinstance(pre_state.get("candidate_ids"), list):
        raise IntentReconciliationError("phase intent pre-state is invalid")
    if not all(isinstance(item, str) for item in pre_state["candidate_ids"]):
        raise IntentReconciliationError("phase intent Candidate IDs are invalid")
    return value


def _expected_gap_spec(phase: str) -> dict[str, Any]:
    if phase == "square_gap":
        return _square_spec()
    if phase == "string_gap":
        return _numeric_to_string_spec()
    raise IntentReconciliationError(f"phase is not a recoverable acquisition gap: {phase}")


def _recover_gap_result_from_intent(
    root: Path,
    *,
    phase: str,
    intent: Mapping[str, Any],
) -> dict[str, Any] | None:
    pre_state = intent["pre_state"]
    prior_ids = [str(value) for value in pre_state["candidate_ids"]]
    current_ids = _all_candidate_ids(root)
    new_ids = sorted(set(current_ids) - set(prior_ids))
    missing_ids = sorted(set(prior_ids) - set(current_ids))
    if missing_ids:
        raise IntentReconciliationError(
            "pre-receipt interruption removed prior durable Candidates"
        )

    current_prior_snapshot = _snapshot_for_ids(root, prior_ids)
    if current_prior_snapshot["candidate_tree_digest"] != pre_state.get(
        "candidate_tree_digest"
    ):
        raise IntentReconciliationError(
            "pre-receipt interruption changed prior Candidate bytes"
        )
    if current_prior_snapshot["trial_ledgers_digest"] != pre_state.get(
        "trial_ledgers_digest"
    ):
        raise IntentReconciliationError(
            "pre-receipt interruption changed prior Candidate trial ledgers"
        )
    if not new_ids:
        return None
    if len(new_ids) != 1:
        raise IntentReconciliationError(
            "pre-receipt interruption left an ambiguous Candidate delta"
        )

    candidate_id = new_ids[0]
    candidate_path = root / ".continual" / "candidates" / candidate_id / "candidate.json"
    if not candidate_path.is_file():
        raise IntentReconciliationError(
            "pre-receipt recovery Candidate state is missing"
        )
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    if not isinstance(candidate, dict):
        raise IntentReconciliationError(
            "pre-receipt recovery Candidate is not an object"
        )
    tool = AcquiredProgramToolSpec.from_candidate(candidate)
    spec = _expected_gap_spec(phase)
    expected_scope = f"agi/acquired-program/heterogeneous/{spec['name']}/v1"
    if tool.candidate_id != candidate_id or tool.scope != expected_scope:
        raise IntentReconciliationError(
            "pre-receipt Candidate does not match the interrupted gap scope"
        )
    if not candidate_verified_for_scope(root, candidate, tool.scope):
        raise IntentReconciliationError(
            "pre-receipt Candidate is not verified for its exact scope"
        )

    checks = [*spec["examples"], type(spec["examples"][0])(spec["probe"], spec["expected"])]
    for example in checks:
        if execute_program(tool.program, example.input) != example.output:
            raise IntentReconciliationError(
                "pre-receipt Candidate does not implement the interrupted gap behavior"
            )
    candidate_trials = _trial_snapshot(root, [candidate_id]).get(candidate_id, {})
    if not candidate_trials:
        raise IntentReconciliationError(
            "pre-receipt Candidate has no durable exact-scope trial ledger"
        )

    metadata = {
        "candidate_id": candidate_id,
        "scope": tool.scope,
        "gap_failed_closed_before_learning": True,
        "negative_evidence_retained": True,
        "recovered_from_pre_receipt_intent": True,
    }
    result_digest = _digest(
        {
            "phase": phase,
            "intent_digest": intent["intent_digest"],
            "candidate_id": candidate_id,
            "scope": tool.scope,
            "program": tool.program,
            "candidate_trial_digest": _digest(candidate_trials),
            "metadata": metadata,
        }
    )
    return {
        "passed": True,
        "result_digest": result_digest,
        "metadata": metadata,
    }


def run_intent_reconciled_gap_curriculum(
    root: Path,
    campaign_id: str,
    *,
    seed: str,
    max_new_phases: int | None = None,
    interrupt_before_receipt: str | None = None,
) -> dict[str, Any]:
    """Recover a verified gap commit even when a process dies before its completion receipt.

    Before each phase starts, an intent records the exact prior Candidate set and cryptographic digests of
    every prior Candidate byte tree and trial ledger. For the two learning/transaction phases, recovery
    from a ``running`` journal with no completion receipt inspects the durable delta. No delta means the
    phase can be safely retried. Exactly one new exact-scope verified Candidate that implements the
    interrupted behavior, has durable trials, and leaves all prior state unchanged is converted into the
    normal completion receipt and reconciled without replay. Any ambiguous or incompatible delta fails
    closed.

    The intent does not make arbitrary phases transactional; this milestone covers the square and numeric-
    to-string acquisition/commit phases where duplicated learning would otherwise reject or replay durable
    side effects. ``interrupt_before_receipt`` deterministically simulates that crash window in tests.

    This is repository-local internal reliability evidence, not independent production AGI evidence.
    """

    if not _safe_campaign_id(campaign_id):
        raise ValueError("campaign_id must be a safe lowercase identifier")
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    if max_new_phases is not None and max_new_phases < 1:
        raise ValueError("max_new_phases must be positive when supplied")
    if interrupt_before_receipt is not None and interrupt_before_receipt not in _RECOVERABLE_GAP_PHASES:
        raise ValueError("interrupt_before_receipt must name a recoverable gap phase")

    root = root.resolve()
    seed_commitment = _digest(seed)
    path = _state_path(root, campaign_id)
    state = _load_state(path)
    if state is None:
        state = _initial_state(campaign_id, seed_commitment)
        _atomic_json(path, state)
    else:
        _validate_state(
            state,
            campaign_id=campaign_id,
            seed_commitment=seed_commitment,
        )

    completed_before = tuple(state["completed_phases"])
    immutable_digests = {
        phase: state["phases"][phase]["result_digest"]
        for phase in completed_before
    }
    immutable_attempts = {
        phase: int(state["phases"][phase]["attempts"])
        for phase in completed_before
    }

    advanced = 0
    receipt_reconciled = 0
    intent_reconciled = 0
    processed = 0
    for phase in _PHASE_ORDER:
        entry = state["phases"][phase]
        if entry["status"] == "completed":
            continue
        if max_new_phases is not None and processed >= max_new_phases:
            break

        if entry["status"] == "running":
            receipt_result = _load_valid_receipt(
                root,
                campaign_id=campaign_id,
                phase=phase,
                seed_commitment=seed_commitment,
                expected_attempt=int(entry.get("attempts", 0)),
            )
            if receipt_result is not None:
                entry["status"] = "completed"
                entry["result_digest"] = str(receipt_result["result_digest"])
                entry["metadata"] = dict(receipt_result["metadata"])
                if phase not in state["completed_phases"]:
                    state["completed_phases"].append(phase)
                state["active_phase"] = None
                receipt_reconciled += 1
                processed += 1
                _atomic_json(path, state)
                continue

            intent = _load_valid_intent(
                root,
                campaign_id=campaign_id,
                phase=phase,
                seed_commitment=seed_commitment,
                expected_attempt=int(entry.get("attempts", 0)),
            )
            if intent is not None and phase in _RECOVERABLE_GAP_PHASES:
                recovered = _recover_gap_result_from_intent(
                    root,
                    phase=phase,
                    intent=intent,
                )
                if recovered is not None:
                    _write_receipt(
                        root,
                        campaign_id=campaign_id,
                        phase=phase,
                        seed_commitment=seed_commitment,
                        result=recovered,
                        attempt=int(entry["attempts"]),
                    )
                    entry["status"] = "completed"
                    entry["result_digest"] = str(recovered["result_digest"])
                    entry["metadata"] = dict(recovered["metadata"])
                    if phase not in state["completed_phases"]:
                        state["completed_phases"].append(phase)
                    state["active_phase"] = None
                    intent_reconciled += 1
                    processed += 1
                    _atomic_json(path, state)
                    continue

        entry["status"] = "running"
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        state["active_phase"] = phase
        _atomic_json(path, state)
        _write_intent(
            root,
            campaign_id=campaign_id,
            phase=phase,
            seed_commitment=seed_commitment,
            attempt=int(entry["attempts"]),
        )

        result = _run_phase(
            root,
            state,
            phase,
            seed=seed,
            campaign_id=campaign_id,
        )
        if not result.get("passed"):
            raise IntentReconciliationError(
                f"intent-reconciled curriculum phase failed: {phase}"
            )
        if interrupt_before_receipt == phase:
            raise SimulatedPreReceiptInterruption(
                f"simulated process loss before completion receipt for {phase}"
            )
        _write_receipt(
            root,
            campaign_id=campaign_id,
            phase=phase,
            seed_commitment=seed_commitment,
            result=result,
            attempt=int(entry["attempts"]),
        )

        entry["status"] = "completed"
        entry["result_digest"] = str(result["result_digest"])
        entry["metadata"] = dict(result.get("metadata") or {})
        if phase not in state["completed_phases"]:
            state["completed_phases"].append(phase)
        state["active_phase"] = None
        advanced += 1
        processed += 1
        _atomic_json(path, state)

    for phase, digest in immutable_digests.items():
        entry = state["phases"][phase]
        if entry["result_digest"] != digest:
            raise IntentReconciliationError(
                "completed phase evidence changed during intent recovery"
            )
        if int(entry["attempts"]) != immutable_attempts[phase]:
            raise IntentReconciliationError(
                "completed phase was replayed during intent recovery"
            )

    complete = tuple(state["completed_phases"]) == _PHASE_ORDER
    state["status"] = "completed" if complete else "in_progress"
    state["passed"] = complete
    state["active_phase"] = None
    state["completed_count"] = len(state["completed_phases"])
    state["resume_skipped_completed"] = len(completed_before)
    state["last_advance_count"] = advanced
    state["last_receipt_reconciled_count"] = receipt_reconciled
    state["last_intent_reconciled_count"] = intent_reconciled
    state["pre_receipt_gap_recovery_enabled"] = True
    state["ambiguous_pre_receipt_delta_fails_closed"] = True
    state["digest"] = _digest(
        {key: value for key, value in state.items() if key != "digest"}
    )
    _atomic_json(path, state)
    return state
