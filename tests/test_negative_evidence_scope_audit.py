from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from agi.negative_evidence_scope_audit import (
    NegativeEvidenceScopeAuditError,
    load_negative_evidence_scope_ledger,
    validate_negative_evidence_scope_ledger,
)


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "agi" / "NEGATIVE_EVIDENCE_SCOPE_LEDGER.json"


def _value() -> dict:
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def test_checked_in_scope_ledger_is_source_bound_and_complete() -> None:
    value = load_negative_evidence_scope_ledger(LEDGER, root=ROOT)

    assert value["policy_revision"] == 20
    assert value["summary"] == {
        "entry_count": 4,
        "classification_counts": {
            "adaptation_or_ablation_loss": 0,
            "original_baseline_reproduction_failure": 0,
            "tested_variant_failure": 2,
            "untested_mechanism": 2,
        },
        "repair_required_count": 1,
        "family_wide_negative_count": 0,
        "untested_mechanism_negative_count": 0,
    }
    assert all(entry["family_failure_supported"] is False for entry in value["entries"])
    assert all(
        entry["untested_mechanism_failure_supported"] is False
        for entry in value["entries"]
    )


def test_family_wide_inference_fails_closed() -> None:
    value = deepcopy(_value())
    value["entries"][0]["family_failure_supported"] = True

    with pytest.raises(NegativeEvidenceScopeAuditError, match="family inference"):
        validate_negative_evidence_scope_ledger(value, root=ROOT)


def test_untested_mechanism_inference_fails_closed() -> None:
    value = deepcopy(_value())
    value["entries"][1]["untested_mechanism_failure_supported"] = True

    with pytest.raises(NegativeEvidenceScopeAuditError, match="untested-mechanism"):
        validate_negative_evidence_scope_ledger(value, root=ROOT)


def test_source_value_tamper_fails_closed() -> None:
    value = deepcopy(_value())
    value["entries"][2]["source"]["content_digest"] = "0" * 64

    with pytest.raises(NegativeEvidenceScopeAuditError, match="source digest mismatch"):
        validate_negative_evidence_scope_ledger(value, root=ROOT)


def test_control_cannot_be_reused_without_provenance_equivalence() -> None:
    value = deepcopy(_value())
    control = value["entries"][1]["positive_control"]
    control["decision"] = "reuse_provenance_equivalent"

    with pytest.raises(NegativeEvidenceScopeAuditError, match="lacks established"):
        validate_negative_evidence_scope_ledger(value, root=ROOT)


def test_equivalent_reported_control_is_reused_without_duplicate_reproduction() -> None:
    value = load_negative_evidence_scope_ledger(LEDGER, root=ROOT)
    entry = next(
        item
        for item in value["entries"]
        if item["entry_id"] == "scientist-baseline-reported-mixed-outcomes"
    )

    assert entry["positive_control"]["decision"] == "reuse_provenance_equivalent"
    assert entry["positive_control"]["equivalence"]["status"] == "established"
    assert entry["positive_control"]["provenance_refs"]
