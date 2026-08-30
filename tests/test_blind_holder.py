from __future__ import annotations

import json

import pytest

from agi.blind_holder import (
    CLAIM_BOUNDARY,
    EVENT_ORDER,
    NEGATIVE_CONTROL_REASONS,
    PROTOCOL_ID,
    canonical_digest,
    negative_controls,
    protocol_spec,
    synthetic_positive_control,
    validate_blind_holder,
    validate_control_suite,
)


def test_synthetic_positive_control_completes_exact_state_machine():
    document = synthetic_positive_control()
    decision = validate_blind_holder(document)
    assert decision["valid"] is True
    assert decision["status"] == "VALID_SYNTHETIC_PROTOCOL_ONLY"
    assert decision["reason_codes"] == []
    assert [item["type"] for item in decision["transition_trace"]] == list(EVENT_ORDER)
    assert decision["claim_boundary"] == CLAIM_BOUNDARY


def test_positive_decision_and_document_are_byte_stable_on_replay():
    first_document = synthetic_positive_control()
    second_document = json.loads(json.dumps(first_document, sort_keys=True))
    first = validate_blind_holder(first_document)
    second = validate_blind_holder(second_document)
    assert first == second
    assert first["decision_digest"] == canonical_digest(
        {key: value for key, value in first.items() if key != "decision_digest"}
    )


def test_holder_receipt_contains_no_outcome_bearing_bytes():
    holder = synthetic_positive_control()["holder_receipt"]
    assert holder["outcome_bytes_exposed"] is False
    assert "outcome_bytes" not in holder
    assert "hidden_outcome_bytes" not in holder
    assert "revealed_bytes" not in holder
    assert len(holder["hidden_outcome_digest"]) == 64


def test_predictor_manifest_denies_source_and_excludes_source_and_outcome_digests():
    document = synthetic_positive_control()
    source = document["source"]
    request = document["prediction_request"]
    isolation = document["isolation_receipt"]
    assert source["immutable_locator"] in request["denied_source_locators"]
    assert source["primary_content_digest"] not in request["allowed_input_digests"]
    assert source["hidden_outcome_digest"] not in request["allowed_input_digests"]
    assert isolation["same_session_with_holder"] is False
    assert isolation["prior_response_loaded"] is False
    assert isolation["context_boundary_kind"] == "independent_process"


@pytest.mark.parametrize("name", sorted(NEGATIVE_CONTROL_REASONS))
def test_each_negative_control_fails_for_one_intended_reason(name: str):
    fixture = negative_controls()[name]
    first = validate_blind_holder(fixture["document"])
    second = validate_blind_holder(json.loads(json.dumps(fixture["document"], sort_keys=True)))
    assert first == second
    assert first["valid"] is False
    assert first["status"] == "INVALID_FAIL_CLOSED"
    assert first["reason_codes"] == [fixture["expected_reason"]]


def test_negative_matrix_covers_observed_contamination_and_exactly_once_failures():
    required = {
        "retrieval_before_commitments",
        "task_rule_after_retrieval",
        "same_session_isolation",
        "reveal_before_response",
        "holder_digest_mismatch",
        "duplicate_prediction",
        "duplicate_reveal",
        "replay_drift",
        "fabricated_baseline",
        "baseline_substitution",
    }
    assert required <= set(negative_controls())


def test_control_suite_reports_every_negative_as_unique_and_fail_closed():
    suite = validate_control_suite()
    assert suite["protocol_id"] == PROTOCOL_ID
    assert suite["positive"]["valid"] is True
    assert suite["all_negative_controls_fail_for_unique_expected_reason"] is True
    assert len(suite["negative_controls"]) == len(NEGATIVE_CONTROL_REASONS)
    for item in suite["negative_controls"].values():
        assert item["valid"] is False
        assert item["observed_reason_codes"] == [item["expected_reason"]]


def test_protocol_spec_is_versioned_and_claim_bounded():
    spec = protocol_spec()
    assert spec["schema_version"] == 1
    assert spec["protocol_id"] == PROTOCOL_ID
    assert spec["states"] == list(EVENT_ORDER)
    assert spec["claim_boundary"] == CLAIM_BOUNDARY
    assert spec["spec_digest"] == canonical_digest(
        {key: value for key, value in spec.items() if key != "spec_digest"}
    )


def test_missing_or_malformed_document_fails_closed_without_exception():
    decision = validate_blind_holder({})
    assert decision["valid"] is False
    assert decision["reason_codes"] == ["UNSUPPORTED_SCHEMA"]
    assert len(decision["decision_digest"]) == 64
