from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from agi.acquired_program_runtime import _atomic_json
from agi.durable_state_rehydration import _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.resumable_multi_gap_curriculum import (
    _PHASE_ORDER,
    _initial_state,
    _load_state,
    _run_phase,
    _safe_campaign_id,
    _state_path,
    _validate_state,
)
from continual.candidate_regression import candidate_verified_for_scope


class ReceiptReconciliationError(RuntimeError):
    pass


class SimulatedReceiptInterruption(RuntimeError):
    """Test-only interruption after durable effects and receipt, before journal completion."""


_RECEIPT_SCHEMA = 1


def _receipt_path(root: Path, campaign_id: str, phase: str) -> Path:
    return (
        root
        / ".continual"
        / "system"
        / "multi-gap-curricula"
        / f"{campaign_id}.receipts"
        / f"{phase}.json"
    )


def _durable_fingerprint(root: Path) -> dict[str, Any]:
    candidate_ids = _all_candidate_ids(root)
    tree = _tree_snapshot(root, candidate_ids)
    trials = _trial_snapshot(root, candidate_ids)
    payload = {
        "candidate_ids": candidate_ids,
        "candidate_tree": tree,
        "trial_ledgers": trials,
    }
    return {
        "candidate_count": len(candidate_ids),
        "candidate_ids_digest": _digest(candidate_ids),
        "candidate_tree_digest": _digest(tree),
        "trial_ledgers_digest": _digest(trials),
        "aggregate_digest": _digest(payload),
    }


def _write_receipt(
    root: Path,
    *,
    campaign_id: str,
    phase: str,
    seed_commitment: str,
    result: Mapping[str, Any],
    attempt: int,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema_version": _RECEIPT_SCHEMA,
        "campaign_id": campaign_id,
        "phase": phase,
        "seed_commitment": seed_commitment,
        "attempt": attempt,
        "result_digest": str(result["result_digest"]),
        "metadata": dict(result.get("metadata") or {}),
        "durable_fingerprint": _durable_fingerprint(root),
    }
    receipt["receipt_digest"] = _digest(receipt)
    path = _receipt_path(root, campaign_id, phase)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != receipt:
            raise ReceiptReconciliationError(
                f"phase receipt already exists with different content: {phase}"
            )
        return receipt
    _atomic_json(path, receipt)
    return receipt


def _read_candidate(root: Path, candidate_id: str) -> dict[str, Any]:
    path = root / ".continual" / "candidates" / candidate_id / "candidate.json"
    if not path.is_file():
        raise ReceiptReconciliationError(
            f"receipt references missing Candidate: {candidate_id}"
        )
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("candidate_id") != candidate_id:
        raise ReceiptReconciliationError(
            f"receipt Candidate identity is invalid: {candidate_id}"
        )
    return value


def _validate_receipt(
    root: Path,
    receipt: Mapping[str, Any],
    *,
    campaign_id: str,
    phase: str,
    seed_commitment: str,
    expected_attempt: int,
) -> dict[str, Any]:
    if receipt.get("schema_version") != _RECEIPT_SCHEMA:
        raise ReceiptReconciliationError("phase receipt schema mismatch")
    if receipt.get("campaign_id") != campaign_id or receipt.get("phase") != phase:
        raise ReceiptReconciliationError("phase receipt identity mismatch")
    if receipt.get("seed_commitment") != seed_commitment:
        raise ReceiptReconciliationError("phase receipt seed commitment mismatch")
    if receipt.get("attempt") != expected_attempt:
        raise ReceiptReconciliationError("phase receipt attempt mismatch")

    supplied_digest = receipt.get("receipt_digest")
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    if not isinstance(supplied_digest, str) or supplied_digest != _digest(unsigned):
        raise ReceiptReconciliationError("phase receipt digest mismatch")

    result_digest = receipt.get("result_digest")
    metadata = receipt.get("metadata")
    if not isinstance(result_digest, str) or not result_digest:
        raise ReceiptReconciliationError("phase receipt result digest is missing")
    if not isinstance(metadata, dict):
        raise ReceiptReconciliationError("phase receipt metadata must be an object")
    if receipt.get("durable_fingerprint") != _durable_fingerprint(root):
        raise ReceiptReconciliationError(
            "durable Candidate/trial state no longer matches phase receipt"
        )

    if phase in {"square_gap", "string_gap"}:
        candidate_id = metadata.get("candidate_id")
        scope = metadata.get("scope")
        if not isinstance(candidate_id, str) or not isinstance(scope, str):
            raise ReceiptReconciliationError(
                "acquisition receipt lacks Candidate identity or scope"
            )
        candidate = _read_candidate(root, candidate_id)
        if not candidate_verified_for_scope(root, candidate, scope):
            raise ReceiptReconciliationError(
                "acquisition receipt Candidate is not verified for its exact scope"
            )

    if phase == "final_composition":
        evidence_ref = metadata.get("evidence_path")
        if not isinstance(evidence_ref, str) or not evidence_ref:
            raise ReceiptReconciliationError(
                "final-composition receipt lacks evidence path"
            )
        evidence_path = (root / evidence_ref).resolve()
        if root != evidence_path and root not in evidence_path.parents:
            raise ReceiptReconciliationError(
                "final-composition receipt evidence path escapes repository"
            )
        if not evidence_path.is_file():
            raise ReceiptReconciliationError(
                "final-composition receipt evidence is missing"
            )
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        if not isinstance(evidence, dict) or evidence.get("digest") != result_digest:
            raise ReceiptReconciliationError(
                "final-composition evidence digest does not match receipt"
            )

    return {
        "result_digest": result_digest,
        "metadata": dict(metadata),
    }


def _load_valid_receipt(
    root: Path,
    *,
    campaign_id: str,
    phase: str,
    seed_commitment: str,
    expected_attempt: int,
) -> dict[str, Any] | None:
    path = _receipt_path(root, campaign_id, phase)
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReceiptReconciliationError("phase receipt must be an object")
    return _validate_receipt(
        root,
        value,
        campaign_id=campaign_id,
        phase=phase,
        seed_commitment=seed_commitment,
        expected_attempt=expected_attempt,
    )


def run_receipt_reconciled_curriculum(
    root: Path,
    campaign_id: str,
    *,
    seed: str,
    max_new_phases: int | None = None,
    interrupt_after_receipt: str | None = None,
) -> dict[str, Any]:
    """Resume a multi-gap curriculum across the post-side-effect/pre-journal crash window.

    The underlying phase journal marks a phase ``running`` before its side effects. This wrapper adds an
    immutable completion receipt immediately after those side effects succeed but before the journal is
    marked ``completed``. On recovery, a valid receipt whose Candidate/trial fingerprint still matches the
    durable repository reconciles that running phase without replaying learning or transactional commits.

    A missing receipt retains the prior fail-closed retry behavior. A present but tampered, rebound, stale,
    or state-mismatched receipt blocks recovery instead of causing replay. ``interrupt_after_receipt`` is a
    deterministic failure-injection hook used only to falsify the crash-window guarantee in tests.

    This remains repository-authored internal development evidence and is not claim-grade AGI evidence.
    """

    if not _safe_campaign_id(campaign_id):
        raise ValueError("campaign_id must be a safe lowercase identifier")
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    if max_new_phases is not None and max_new_phases < 1:
        raise ValueError("max_new_phases must be positive when supplied")
    if interrupt_after_receipt is not None and interrupt_after_receipt not in _PHASE_ORDER:
        raise ValueError("interrupt_after_receipt must name a curriculum phase")

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
    reconciled = 0
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
                reconciled += 1
                processed += 1
                _atomic_json(path, state)
                continue

        entry["status"] = "running"
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        state["active_phase"] = phase
        _atomic_json(path, state)

        result = _run_phase(
            root,
            state,
            phase,
            seed=seed,
            campaign_id=campaign_id,
        )
        if not result.get("passed"):
            raise ReceiptReconciliationError(
                f"receipt-reconciled curriculum phase failed: {phase}"
            )
        _write_receipt(
            root,
            campaign_id=campaign_id,
            phase=phase,
            seed_commitment=seed_commitment,
            result=result,
            attempt=int(entry["attempts"]),
        )
        if interrupt_after_receipt == phase:
            raise SimulatedReceiptInterruption(
                f"simulated process loss after durable receipt for {phase}"
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
            raise ReceiptReconciliationError(
                "completed phase evidence changed during receipt recovery"
            )
        if int(entry["attempts"]) != immutable_attempts[phase]:
            raise ReceiptReconciliationError(
                "completed phase was replayed during receipt recovery"
            )

    complete = tuple(state["completed_phases"]) == _PHASE_ORDER
    state["status"] = "completed" if complete else "in_progress"
    state["passed"] = complete
    state["active_phase"] = None
    state["completed_count"] = len(state["completed_phases"])
    state["resume_skipped_completed"] = len(completed_before)
    state["last_advance_count"] = advanced
    state["last_reconciled_count"] = reconciled
    state["receipt_reconciliation_enabled"] = True
    state["post_side_effect_crash_window_recoverable"] = True
    state["receipt_fail_closed_on_tamper_or_state_mismatch"] = True
    state["digest"] = _digest(
        {key: value for key, value in state.items() if key != "digest"}
    )
    _atomic_json(path, state)
    return state
