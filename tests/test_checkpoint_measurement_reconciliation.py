from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from agi.checkpoint_measurement_reconciliation import (
    checkpoint_measurement_result_digest,
    load_checkpoint_measurement_reconciliation,
    validate_checkpoint_measurement_reconciliation,
)


ROOT = Path(__file__).resolve().parents[1]
RESULT_PATH = ROOT / "agi" / "CHECKPOINT_INHERITANCE_MEASUREMENT_RESULT.json"


def _result() -> dict:
    return json.loads(RESULT_PATH.read_text(encoding="utf-8"))


def test_checked_in_reconciliation_verifies_all_existing_attempts() -> None:
    result = load_checkpoint_measurement_reconciliation(ROOT)
    assert result["status"] == "MEASURED_RECONCILED"
    assert result["ledger"] == {
        "ledger_digest": "d15ff8fc54318a69e229f11b74241542585d5a4c0a17f347e6cd183075764165",
        "receipt_count": 13,
        "measurement_receipt_count": 12,
        "unrelated_receipt_count": 1,
    }
    assert result["decision"]["verdict"] == "REJECT_MECHANISM"
    assert result["execution_policy"]["new_attempts_executed"] == 0
    assert len(result["positive_controls"]["outcome_ids"]) == 3
    assert result["positive_controls"]["duplicate_reproduction_required"] is False


def test_reconciliation_rejects_widened_negative_scope() -> None:
    result = deepcopy(_result())
    result["negative_evidence_scope"]["evidence_against_scientist_agent_family"] = True
    result["result_digest"] = checkpoint_measurement_result_digest(result)
    with pytest.raises(ValueError, match="negative-evidence scope"):
        validate_checkpoint_measurement_reconciliation(ROOT, result)


def test_reconciliation_rejects_claim_or_metric_tampering() -> None:
    result = deepcopy(_result())
    result["metrics"]["medians"]["sibling_checkpoint"]["model_invocations"] = 1.0
    result["result_digest"] = checkpoint_measurement_result_digest(result)
    with pytest.raises(ValueError, match="metrics mismatch"):
        validate_checkpoint_measurement_reconciliation(ROOT, result)


def test_reconciliation_rejects_source_digest_drift() -> None:
    result = deepcopy(_result())
    result["source_records"]["ledger"]["sha256"] = "0" * 64
    result["result_digest"] = checkpoint_measurement_result_digest(result)
    with pytest.raises(ValueError, match="source SHA-256 mismatch"):
        validate_checkpoint_measurement_reconciliation(ROOT, result)
