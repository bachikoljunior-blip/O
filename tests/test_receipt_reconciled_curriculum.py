from __future__ import annotations

import json
from pathlib import Path

import pytest

from agi.receipt_reconciled_curriculum import (
    ReceiptReconciliationError,
    SimulatedReceiptInterruption,
    run_receipt_reconciled_curriculum,
)


PHASES = [
    "transactional_base",
    "square_gap",
    "string_gap",
    "final_composition",
]


def test_receipt_reconciles_post_side_effect_crash_without_replaying_learning(
    runtime_repo: Path,
) -> None:
    campaign_id = "receipt-reconciled-curriculum-01"
    seed = "receipt-reconciled-curriculum-seed"

    base = run_receipt_reconciled_curriculum(
        runtime_repo,
        campaign_id,
        seed=seed,
        max_new_phases=1,
    )
    assert base["completed_phases"] == ["transactional_base"]
    assert base["phases"]["transactional_base"]["attempts"] == 1

    with pytest.raises(SimulatedReceiptInterruption):
        run_receipt_reconciled_curriculum(
            runtime_repo,
            campaign_id,
            seed=seed,
            max_new_phases=1,
            interrupt_after_receipt="square_gap",
        )

    state_path = (
        runtime_repo
        / ".continual"
        / "system"
        / "multi-gap-curricula"
        / f"{campaign_id}.json"
    )
    interrupted = json.loads(state_path.read_text(encoding="utf-8"))
    assert interrupted["completed_phases"] == ["transactional_base"]
    assert interrupted["active_phase"] == "square_gap"
    assert interrupted["phases"]["square_gap"]["status"] == "running"
    assert interrupted["phases"]["square_gap"]["attempts"] == 1

    receipt_path = (
        runtime_repo
        / ".continual"
        / "system"
        / "multi-gap-curricula"
        / f"{campaign_id}.receipts"
        / "square_gap.json"
    )
    original_receipt_text = receipt_path.read_text(encoding="utf-8")
    receipt = json.loads(original_receipt_text)
    assert receipt["phase"] == "square_gap"
    assert receipt["attempt"] == 1
    assert receipt["metadata"]["gap_failed_closed_before_learning"] is True
    assert receipt["metadata"]["negative_evidence_retained"] is True

    tampered = dict(receipt)
    tampered["metadata"] = dict(receipt["metadata"])
    tampered["metadata"]["candidate_id"] = "tampered-candidate-id"
    receipt_path.write_text(
        json.dumps(tampered, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ReceiptReconciliationError, match="receipt digest mismatch"):
        run_receipt_reconciled_curriculum(
            runtime_repo,
            campaign_id,
            seed=seed,
            max_new_phases=1,
        )
    still_interrupted = json.loads(state_path.read_text(encoding="utf-8"))
    assert still_interrupted["phases"]["square_gap"]["attempts"] == 1
    assert still_interrupted["phases"]["square_gap"]["status"] == "running"

    receipt_path.write_text(original_receipt_text, encoding="utf-8")
    reconciled = run_receipt_reconciled_curriculum(
        runtime_repo,
        campaign_id,
        seed=seed,
        max_new_phases=1,
    )
    assert reconciled["completed_phases"] == ["transactional_base", "square_gap"]
    assert reconciled["phases"]["square_gap"]["attempts"] == 1
    assert reconciled["last_reconciled_count"] == 1
    assert reconciled["last_advance_count"] == 0
    assert reconciled["receipt_reconciliation_enabled"] is True
    assert reconciled["post_side_effect_crash_window_recoverable"] is True
    assert reconciled["receipt_fail_closed_on_tamper_or_state_mismatch"] is True

    string_phase = run_receipt_reconciled_curriculum(
        runtime_repo,
        campaign_id,
        seed=seed,
        max_new_phases=1,
    )
    assert string_phase["completed_phases"] == PHASES[:3]
    assert string_phase["last_advance_count"] == 1
    assert string_phase["last_reconciled_count"] == 0

    final = run_receipt_reconciled_curriculum(
        runtime_repo,
        campaign_id,
        seed=seed,
        max_new_phases=1,
    )
    assert final["passed"] is True
    assert final["status"] == "completed"
    assert final["completed_phases"] == PHASES
    assert all(final["phases"][phase]["attempts"] == 1 for phase in PHASES)

    final_receipt_path = (
        runtime_repo
        / ".continual"
        / "system"
        / "multi-gap-curricula"
        / f"{campaign_id}.receipts"
        / "final_composition.json"
    )
    final_receipt = json.loads(final_receipt_path.read_text(encoding="utf-8"))
    evidence_path = runtime_repo / final_receipt["metadata"]["evidence_path"]
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["digest"] == final_receipt["result_digest"]
    assert evidence["selected_chain_depth"] == 3
    assert evidence["fresh_engine_runs_unique"] is True
    assert evidence["fresh_state_unchanged"] is True
    assert evidence["fresh_trials_unchanged"] is True
    assert evidence["source_state_unchanged"] is True
    assert evidence["source_trials_unchanged"] is True
    assert evidence["live_model_invocation_required"] is False

    replay = run_receipt_reconciled_curriculum(
        runtime_repo,
        campaign_id,
        seed=seed,
    )
    assert replay["passed"] is True
    assert replay["last_advance_count"] == 0
    assert replay["last_reconciled_count"] == 0
    assert replay["resume_skipped_completed"] == 4
    assert replay["phases"] == final["phases"]
