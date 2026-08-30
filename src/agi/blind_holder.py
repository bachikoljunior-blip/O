from __future__ import annotations

import argparse
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = 1
PROTOCOL_ID = "agi/heldout-protocol/blind-holder-gate-v1"
CLAIM_BOUNDARY = (
    "Repository-local synthetic validation of blind-holder ordering, digest binding, "
    "predictor isolation, reveal authorization, and deterministic replay only. It is "
    "not a live forecast, external capability result, production claim, or AGI evidence."
)
EVENT_ORDER = (
    "commitments_frozen",
    "holder_committed",
    "isolation_bound",
    "primary_retrieval",
    "prediction",
    "response_readback",
    "reveal",
    "score",
)
REQUIRED_COMMITMENTS = (
    "source_selection_rule",
    "task_schema",
    "label_rule",
    "scoring_rule",
    "predictor_context",
)
NEGATIVE_CONTROL_REASONS = {
    "retrieval_before_commitments": "RETRIEVAL_BEFORE_COMMITMENTS",
    "task_rule_after_retrieval": "TASK_RULE_AFTER_RETRIEVAL",
    "missing_isolation_receipt": "MISSING_ISOLATION_RECEIPT",
    "same_session_isolation": "SAME_SESSION_ISOLATION",
    "unverified_model_context": "UNVERIFIED_MODEL_CONTEXT",
    "reveal_before_response": "REVEAL_BEFORE_RESPONSE_READBACK",
    "holder_digest_mismatch": "HOLDER_OUTCOME_DIGEST_MISMATCH",
    "source_identity_mismatch": "REVEAL_SOURCE_IDENTITY_MISMATCH",
    "duplicate_prediction": "DUPLICATE_PREDICTION",
    "duplicate_reveal": "DUPLICATE_REVEAL",
    "replay_drift": "REPLAY_DRIFT",
    "fabricated_baseline": "FABRICATED_BASELINE",
    "baseline_substitution": "BASELINE_INPUT_SUBSTITUTION",
    "duplicate_effect_key": "DUPLICATE_IDEMPOTENCY_KEY",
}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ProtocolViolation(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fail(condition: bool, code: str, detail: str) -> None:
    if condition:
        raise ProtocolViolation(code, detail)


def _mapping(value: Any, code: str, label: str) -> dict[str, Any]:
    _fail(not isinstance(value, Mapping), code, f"{label} must be an object")
    return dict(value)


def _digest(value: Any, code: str, label: str) -> str:
    _fail(not isinstance(value, str) or _HEX64.fullmatch(value) is None, code, f"{label} must be sha256")
    return value


def _text(value: Any, code: str, label: str) -> str:
    _fail(not isinstance(value, str) or not value.strip(), code, f"{label} must be non-empty")
    return value


def _sequence(value: Any, code: str, label: str) -> int:
    _fail(isinstance(value, bool) or not isinstance(value, int) or value < 1, code, f"{label} must be positive")
    return value


def _seal(value: Mapping[str, Any], field: str = "receipt_digest") -> dict[str, Any]:
    result = deepcopy(dict(value))
    result.pop(field, None)
    result[field] = canonical_digest(result)
    return result


def _verified(value: Any, *, field: str, code: str, label: str) -> dict[str, Any]:
    result = _mapping(value, code, label)
    supplied = _digest(result.get(field), code, f"{label}.{field}")
    body = deepcopy(result)
    body.pop(field, None)
    _fail(supplied != canonical_digest(body), code, f"{label} canonical digest mismatch")
    return result


def _decision(value: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(value)
    result["decision_digest"] = canonical_digest(result)
    return result


def source_identity_digest(source: Mapping[str, Any]) -> str:
    value = _mapping(source, "MALFORMED_SOURCE", "source")
    identity = {
        "immutable_locator": _text(value.get("immutable_locator"), "MALFORMED_SOURCE", "immutable_locator"),
        "immutable_version": _text(value.get("immutable_version"), "MALFORMED_SOURCE", "immutable_version"),
        "primary_content_digest": _digest(
            value.get("primary_content_digest"), "MALFORMED_SOURCE", "primary_content_digest"
        ),
    }
    return canonical_digest(identity)


def _validate(document: Mapping[str, Any]) -> dict[str, Any]:
    value = _mapping(document, "MALFORMED_DOCUMENT", "document")
    _fail(value.get("schema_version") != SCHEMA_VERSION, "UNSUPPORTED_SCHEMA", "schema_version must be 1")
    _fail(value.get("protocol_id") != PROTOCOL_ID, "PROTOCOL_ID_MISMATCH", "unexpected protocol")
    trial_id = _text(value.get("trial_id"), "MALFORMED_DOCUMENT", "trial_id")

    raw_events = value.get("events")
    _fail(not isinstance(raw_events, list), "MALFORMED_EVENT_LOG", "events must be an array")
    events = [_mapping(item, "MALFORMED_EVENT_LOG", f"events[{index}]") for index, item in enumerate(raw_events)]
    by_type: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        kind = _text(event.get("type"), "MALFORMED_EVENT_LOG", "event.type")
        by_type.setdefault(kind, []).append(event)
    _fail(len(by_type.get("prediction", [])) > 1, "DUPLICATE_PREDICTION", "prediction is exactly once")
    _fail(len(by_type.get("reveal", [])) > 1, "DUPLICATE_REVEAL", "reveal is exactly once")
    missing = [kind for kind in EVENT_ORDER if len(by_type.get(kind, [])) != 1]
    _fail(bool(missing), "MISSING_PROTOCOL_EVENT", f"missing or non-unique events: {missing!r}")
    unknown = sorted(set(by_type) - set(EVENT_ORDER))
    _fail(bool(unknown), "UNKNOWN_PROTOCOL_EVENT", f"unknown events: {unknown!r}")
    sequence = {
        kind: _sequence(by_type[kind][0].get("sequence"), "MALFORMED_EVENT_LOG", f"{kind}.sequence")
        for kind in EVENT_ORDER
    }
    _fail(len(set(sequence.values())) != len(sequence), "EVENT_SEQUENCE_COLLISION", "event sequences collide")
    _fail(
        sequence["primary_retrieval"] <= sequence["commitments_frozen"],
        "RETRIEVAL_BEFORE_COMMITMENTS",
        "primary retrieval precedes the frozen rules",
    )
    _fail(
        sequence["reveal"] <= sequence["response_readback"],
        "REVEAL_BEFORE_RESPONSE_READBACK",
        "reveal precedes exact response readback",
    )

    commitments = _verified(
        value.get("commitments"),
        field="receipt_digest",
        code="COMMITMENT_RECEIPT_DIGEST_MISMATCH",
        label="commitments",
    )
    for name in REQUIRED_COMMITMENTS:
        record = _mapping(commitments.get(name), "MISSING_COMMITMENTS", name)
        _digest(record.get("digest"), "MALFORMED_COMMITMENT", f"{name}.digest")
        committed = _sequence(record.get("committed_sequence"), "MALFORMED_COMMITMENT", f"{name}.sequence")
        if committed >= sequence["primary_retrieval"]:
            code = (
                "TASK_RULE_AFTER_RETRIEVAL"
                if name in {"task_schema", "label_rule", "scoring_rule"}
                else "PREDICTOR_CONTEXT_AFTER_RETRIEVAL"
                if name == "predictor_context"
                else "SOURCE_SELECTION_AFTER_RETRIEVAL"
            )
            raise ProtocolViolation(code, f"{name} was committed after retrieval")
    _fail(
        by_type["commitments_frozen"][0].get("receipt_digest") != commitments["receipt_digest"],
        "COMMITMENT_EVENT_BINDING_MISMATCH",
        "commitment event binds another receipt",
    )
    observed_order = [kind for kind, _ in sorted(sequence.items(), key=lambda pair: pair[1])]
    _fail(observed_order != list(EVENT_ORDER), "INVALID_TRANSITION_ORDER", f"order is {observed_order!r}")

    source = _mapping(value.get("source"), "MALFORMED_SOURCE", "source")
    source_digest = source_identity_digest(source)
    content_digest = _digest(source.get("primary_content_digest"), "MALFORMED_SOURCE", "primary content")
    outcome_digest = _digest(source.get("hidden_outcome_digest"), "MALFORMED_SOURCE", "hidden outcome")

    holder = _verified(
        value.get("holder_receipt"),
        field="receipt_digest",
        code="HOLDER_RECEIPT_DIGEST_MISMATCH",
        label="holder_receipt",
    )
    _fail(
        any(key in holder for key in ("outcome_bytes", "hidden_outcome_bytes", "revealed_bytes"))
        or holder.get("outcome_bytes_exposed") is not False,
        "HOLDER_LEAKED_OUTCOME_BYTES",
        "holder receipt contains outcome-bearing bytes",
    )
    _fail(
        holder.get("source_identity_digest") != source_digest,
        "HOLDER_SOURCE_IDENTITY_MISMATCH",
        "holder source identity differs",
    )
    _fail(
        holder.get("hidden_outcome_digest") != outcome_digest,
        "HOLDER_OUTCOME_DIGEST_MISMATCH",
        "holder outcome digest differs",
    )
    _fail(
        _sequence(holder.get("created_sequence"), "MALFORMED_HOLDER_RECEIPT", "holder sequence")
        >= sequence["primary_retrieval"],
        "HOLDER_COMMIT_AFTER_RETRIEVAL",
        "holder was created after retrieval",
    )
    _fail(
        by_type["holder_committed"][0].get("receipt_digest") != holder["receipt_digest"],
        "HOLDER_EVENT_BINDING_MISMATCH",
        "holder event binds another receipt",
    )

    if value.get("isolation_receipt") is None:
        raise ProtocolViolation("MISSING_ISOLATION_RECEIPT", "predictor isolation receipt is required")
    isolation = _verified(
        value["isolation_receipt"],
        field="receipt_digest",
        code="ISOLATION_RECEIPT_DIGEST_MISMATCH",
        label="isolation_receipt",
    )
    _fail(isolation.get("fresh_invocation") is not True, "NONFRESH_PREDICTOR_INVOCATION", "invocation is not fresh")
    _fail(isolation.get("same_session_with_holder") is not False, "SAME_SESSION_ISOLATION", "same session is inadmissible")
    _fail(isolation.get("prior_response_loaded") is not False, "REUSED_RESPONSE_STATE", "prior response crossed boundary")
    _fail(isolation.get("model_verified") is not True, "UNVERIFIED_MODEL_CONTEXT", "model context is unverified")
    _fail(
        isolation.get("context_boundary_kind") not in {"independent_process", "independent_runtime"},
        "MISSING_ISOLATION_BOUNDARY",
        "independent boundary is missing",
    )
    _digest(isolation.get("boundary_evidence_digest"), "MISSING_ISOLATION_BOUNDARY", "boundary evidence")
    _fail(
        _sequence(isolation.get("created_sequence"), "MALFORMED_ISOLATION_RECEIPT", "isolation sequence")
        >= sequence["primary_retrieval"],
        "ISOLATION_AFTER_RETRIEVAL",
        "isolation was bound after retrieval",
    )
    context_body = {
        key: isolation.get(key)
        for key in (
            "invocation_id",
            "executor_binding",
            "model_identity",
            "model_verified",
            "context_id",
            "context_boundary_kind",
            "allowed_input_digests",
            "denied_source_locators",
            "boundary_evidence_digest",
        )
    }
    context_digest = commitments["predictor_context"]["digest"]
    _fail(canonical_digest(context_body) != context_digest, "CONTEXT_COMMITMENT_MISMATCH", "context differs")
    _fail(isolation.get("context_commitment_digest") != context_digest, "CONTEXT_COMMITMENT_MISMATCH", "context digest differs")
    allowed = isolation.get("allowed_input_digests")
    _fail(not isinstance(allowed, list) or not allowed, "MALFORMED_ISOLATION_RECEIPT", "allowlist missing")
    for index, item in enumerate(allowed):
        _digest(item, "MALFORMED_ISOLATION_RECEIPT", f"allowed[{index}]")
    _fail(allowed != sorted(set(allowed)), "MALFORMED_ISOLATION_RECEIPT", "allowlist is not sorted unique")
    denied = isolation.get("denied_source_locators")
    _fail(
        not isinstance(denied, list) or source.get("immutable_locator") not in denied,
        "SOURCE_NOT_DENIED_TO_PREDICTOR",
        "source locator is not denied",
    )
    _fail(
        outcome_digest in allowed or content_digest in allowed,
        "OUTCOME_BEARING_INPUT_ALLOWED",
        "predictor allowlist contains source or outcome bytes",
    )
    _fail(
        by_type["isolation_bound"][0].get("receipt_digest") != isolation["receipt_digest"],
        "ISOLATION_EVENT_BINDING_MISMATCH",
        "isolation event binds another receipt",
    )

    request = _verified(
        value.get("prediction_request"),
        field="request_digest",
        code="PREDICTION_REQUEST_DIGEST_MISMATCH",
        label="prediction_request",
    )
    _fail(request.get("invocation_id") != isolation.get("invocation_id"), "PREDICTOR_INVOCATION_MISMATCH", "request invocation differs")
    _fail(request.get("context_commitment_digest") != context_digest, "PREDICTOR_CONTEXT_MISMATCH", "request context differs")
    _fail(request.get("allowed_input_digests") != allowed, "PREDICTOR_INPUT_ALLOWLIST_MISMATCH", "request allowlist differs")
    _fail(request.get("denied_source_locators") != denied, "PREDICTOR_DENYLIST_MISMATCH", "request denylist differs")
    serialized_request = json.dumps(request, sort_keys=True)
    _fail(
        outcome_digest in serialized_request or content_digest in serialized_request,
        "PREDICTION_REQUEST_CONTAINS_OUTCOME",
        "prediction request contains outcome-bearing digest",
    )
    _fail(
        by_type["prediction"][0].get("request_digest") != request["request_digest"],
        "PREDICTION_EVENT_BINDING_MISMATCH",
        "prediction event binds another request",
    )

    response = _verified(
        value.get("prediction_response"),
        field="response_digest",
        code="PREDICTION_RESPONSE_DIGEST_MISMATCH",
        label="prediction_response",
    )
    _fail(response.get("request_digest") != request["request_digest"], "RESPONSE_REQUEST_MISMATCH", "response request differs")
    _digest(response.get("response_payload_digest"), "MALFORMED_PREDICTION_RESPONSE", "response payload")
    readback = by_type["response_readback"][0]
    _fail(readback.get("exact_readback") is not True, "RESPONSE_READBACK_NOT_EXACT", "readback is not exact")
    _fail(
        readback.get("request_digest") != request["request_digest"]
        or readback.get("response_digest") != response["response_digest"],
        "RESPONSE_READBACK_BINDING_MISMATCH",
        "readback binding differs",
    )

    reveal = _verified(
        value.get("reveal_receipt"),
        field="receipt_digest",
        code="REVEAL_RECEIPT_DIGEST_MISMATCH",
        label="reveal_receipt",
    )
    _fail(reveal.get("exact_response_readback") is not True, "REVEAL_WITHOUT_EXACT_READBACK", "exact readback absent")
    _fail(reveal.get("holder_receipt_digest") != holder["receipt_digest"], "REVEAL_HOLDER_RECEIPT_MISMATCH", "holder differs")
    _fail(reveal.get("revealed_bytes_digest") != outcome_digest, "HOLDER_OUTCOME_DIGEST_MISMATCH", "revealed digest differs")
    _fail(reveal.get("source_identity_digest") != source_digest, "REVEAL_SOURCE_IDENTITY_MISMATCH", "source differs")
    _fail(
        reveal.get("prediction_request_digest") != request["request_digest"]
        or reveal.get("prediction_response_digest") != response["response_digest"],
        "REVEAL_PREDICTION_BINDING_MISMATCH",
        "prediction binding differs",
    )
    _fail(
        by_type["reveal"][0].get("receipt_digest") != reveal["receipt_digest"],
        "REVEAL_EVENT_BINDING_MISMATCH",
        "reveal event binds another receipt",
    )

    replay = _verified(
        value.get("replay_receipt"),
        field="receipt_digest",
        code="REPLAY_RECEIPT_DIGEST_MISMATCH",
        label="replay_receipt",
    )
    first = _digest(replay.get("first_decision_digest"), "MALFORMED_REPLAY", "first replay")
    second = _digest(replay.get("second_decision_digest"), "MALFORMED_REPLAY", "second replay")
    _fail(first != second, "REPLAY_DRIFT", "replay decisions differ")

    baseline = _verified(
        value.get("baseline_receipt"),
        field="receipt_digest",
        code="BASELINE_RECEIPT_DIGEST_MISMATCH",
        label="baseline_receipt",
    )
    _fail(baseline.get("fabricated") is not False, "FABRICATED_BASELINE", "baseline is fabricated")
    _fail(baseline.get("kind") != "same_input", "BASELINE_INPUT_SUBSTITUTION", "baseline kind differs")
    _fail(
        baseline.get("exposed_input_digest") != canonical_digest(allowed),
        "BASELINE_INPUT_SUBSTITUTION",
        "baseline inputs differ",
    )
    _digest(baseline.get("comparator_response_digest"), "MALFORMED_BASELINE_RECEIPT", "baseline response")

    keys = [
        _text(by_type[kind][0].get("idempotency_key"), "MISSING_IDEMPOTENCY_KEY", kind)
        for kind in EVENT_ORDER
    ]
    _fail(len(keys) != len(set(keys)), "DUPLICATE_IDEMPOTENCY_KEY", "durable transition key reused")
    score = by_type["score"][0]
    _fail(score.get("scoring_rule_digest") != commitments["scoring_rule"]["digest"], "SCORING_RULE_MISMATCH", "score rule differs")
    _fail(score.get("baseline_receipt_digest") != baseline["receipt_digest"], "SCORE_BASELINE_BINDING_MISMATCH", "baseline differs")
    _fail(score.get("replay_receipt_digest") != replay["receipt_digest"], "SCORE_REPLAY_BINDING_MISMATCH", "replay differs")

    trace = [{"type": kind, "sequence": sequence[kind]} for kind in EVENT_ORDER]
    evidence = {
        "trial_id": trial_id,
        "commitments": commitments["receipt_digest"],
        "holder": holder["receipt_digest"],
        "isolation": isolation["receipt_digest"],
        "request": request["request_digest"],
        "response": response["response_digest"],
        "reveal": reveal["receipt_digest"],
        "replay": replay["receipt_digest"],
        "baseline": baseline["receipt_digest"],
        "transition_trace": trace,
    }
    return {"evidence_digest": canonical_digest(evidence), "transition_trace": trace}


def validate_blind_holder(document: Mapping[str, Any]) -> dict[str, Any]:
    try:
        success = _validate(document)
    except ProtocolViolation as exc:
        return _decision(
            {
                "schema_version": SCHEMA_VERSION,
                "protocol_id": PROTOCOL_ID,
                "valid": False,
                "status": "INVALID_FAIL_CLOSED",
                "reason_codes": [exc.code],
                "detail": exc.detail,
                "claim_boundary": CLAIM_BOUNDARY,
            }
        )
    except Exception as exc:  # pragma: no cover
        return _decision(
            {
                "schema_version": SCHEMA_VERSION,
                "protocol_id": PROTOCOL_ID,
                "valid": False,
                "status": "INVALID_FAIL_CLOSED",
                "reason_codes": ["MALFORMED_DOCUMENT"],
                "detail": f"{type(exc).__name__}: {exc}",
                "claim_boundary": CLAIM_BOUNDARY,
            }
        )
    return _decision(
        {
            "schema_version": SCHEMA_VERSION,
            "protocol_id": PROTOCOL_ID,
            "valid": True,
            "status": "VALID_SYNTHETIC_PROTOCOL_ONLY",
            "reason_codes": [],
            "evidence_digest": success["evidence_digest"],
            "transition_trace": success["transition_trace"],
            "claim_boundary": CLAIM_BOUNDARY,
        }
    )


def protocol_spec() -> dict[str, Any]:
    value = {
        "schema_version": SCHEMA_VERSION,
        "protocol_id": PROTOCOL_ID,
        "states": list(EVENT_ORDER),
        "required_commitments": list(REQUIRED_COMMITMENTS),
        "negative_control_reasons": deepcopy(NEGATIVE_CONTROL_REASONS),
        "holder_policy": "immutable source identity and hidden outcome digest only; no outcome-bearing bytes",
        "isolation_policy": "fresh verified independent process/runtime receipt; same-session and reused state fail closed",
        "reveal_policy": "exact prediction response readback must precede holder- and source-bound reveal",
        "idempotency_policy": "every transition has a unique key; prediction and reveal are exactly once",
        "claim_boundary": CLAIM_BOUNDARY,
    }
    value["spec_digest"] = canonical_digest(value)
    return value


def synthetic_positive_control() -> dict[str, Any]:
    source = {
        "immutable_locator": "synthetic://blind-holder/canary-v1",
        "immutable_version": "synthetic-commit:positive-v1",
        "primary_content_digest": _sha_text("synthetic primary content; no external claim"),
        "hidden_outcome_digest": _sha_text("synthetic canary outcome: holder-valid-v1"),
    }
    source_digest = source_identity_digest(source)
    task_digest = _sha_text("synthetic task schema v1")
    label_digest = _sha_text("synthetic label rule v1")
    score_digest = _sha_text("synthetic score rule v1")
    allowed = sorted([task_digest, label_digest, score_digest, source_digest])
    context_body = {
        "invocation_id": "synthetic-predictor-invocation-v1",
        "executor_binding": "synthetic-independent-executor",
        "model_identity": "synthetic-validator-model-v1",
        "model_verified": True,
        "context_id": "synthetic-context-boundary-v1",
        "context_boundary_kind": "independent_process",
        "allowed_input_digests": allowed,
        "denied_source_locators": [source["immutable_locator"]],
        "boundary_evidence_digest": _sha_text("synthetic process boundary evidence v1"),
    }
    context_digest = canonical_digest(context_body)
    commitments = _seal(
        {
            "source_selection_rule": {
                "digest": _sha_text("select one immutable synthetic canary source"),
                "committed_sequence": 1,
            },
            "task_schema": {"digest": task_digest, "committed_sequence": 1},
            "label_rule": {"digest": label_digest, "committed_sequence": 1},
            "scoring_rule": {"digest": score_digest, "committed_sequence": 1},
            "predictor_context": {"digest": context_digest, "committed_sequence": 1},
        }
    )
    holder = _seal(
        {
            "source_identity_digest": source_digest,
            "hidden_outcome_digest": source["hidden_outcome_digest"],
            "outcome_bytes_exposed": False,
            "created_sequence": 2,
            "idempotency_key": "blind-holder:positive:holder:v1",
        }
    )
    isolation = _seal(
        {
            **context_body,
            "context_commitment_digest": context_digest,
            "fresh_invocation": True,
            "same_session_with_holder": False,
            "prior_response_loaded": False,
            "created_sequence": 3,
            "idempotency_key": "blind-holder:positive:isolation:v1",
        }
    )
    request = _seal(
        {
            "invocation_id": context_body["invocation_id"],
            "context_commitment_digest": context_digest,
            "allowed_input_digests": allowed,
            "denied_source_locators": context_body["denied_source_locators"],
            "task_payload_digest": _sha_text("synthetic visible task payload v1"),
            "idempotency_key": "blind-holder:positive:prediction:v1",
        },
        "request_digest",
    )
    response = _seal(
        {
            "request_digest": request["request_digest"],
            "response_payload_digest": _sha_text("synthetic prediction response v1"),
            "idempotency_key": "blind-holder:positive:response:v1",
        },
        "response_digest",
    )
    reveal = _seal(
        {
            "holder_receipt_digest": holder["receipt_digest"],
            "source_identity_digest": source_digest,
            "revealed_bytes_digest": source["hidden_outcome_digest"],
            "prediction_request_digest": request["request_digest"],
            "prediction_response_digest": response["response_digest"],
            "exact_response_readback": True,
            "idempotency_key": "blind-holder:positive:reveal:v1",
        }
    )
    replay_seed = canonical_digest(
        {
            "commitments": commitments["receipt_digest"],
            "holder": holder["receipt_digest"],
            "isolation": isolation["receipt_digest"],
            "request": request["request_digest"],
            "response": response["response_digest"],
            "reveal": reveal["receipt_digest"],
        }
    )
    replay_decision = canonical_digest({"input": replay_seed, "decision": "VALID"})
    replay = _seal(
        {
            "replay_input_digest": replay_seed,
            "first_decision_digest": replay_decision,
            "second_decision_digest": replay_decision,
            "idempotency_key": "blind-holder:positive:replay:v1",
        }
    )
    baseline = _seal(
        {
            "kind": "same_input",
            "exposed_input_digest": canonical_digest(allowed),
            "comparator_response_digest": _sha_text("synthetic same-input comparator response v1"),
            "fabricated": False,
            "idempotency_key": "blind-holder:positive:baseline:v1",
        }
    )
    events = [
        {
            "type": "commitments_frozen",
            "sequence": 1,
            "receipt_digest": commitments["receipt_digest"],
            "idempotency_key": "blind-holder:positive:event:commitments:v1",
        },
        {
            "type": "holder_committed",
            "sequence": 2,
            "receipt_digest": holder["receipt_digest"],
            "idempotency_key": "blind-holder:positive:event:holder:v1",
        },
        {
            "type": "isolation_bound",
            "sequence": 3,
            "receipt_digest": isolation["receipt_digest"],
            "idempotency_key": "blind-holder:positive:event:isolation:v1",
        },
        {
            "type": "primary_retrieval",
            "sequence": 4,
            "source_identity_digest": source_digest,
            "idempotency_key": "blind-holder:positive:event:retrieval:v1",
        },
        {
            "type": "prediction",
            "sequence": 5,
            "request_digest": request["request_digest"],
            "idempotency_key": "blind-holder:positive:event:prediction:v1",
        },
        {
            "type": "response_readback",
            "sequence": 6,
            "request_digest": request["request_digest"],
            "response_digest": response["response_digest"],
            "exact_readback": True,
            "idempotency_key": "blind-holder:positive:event:readback:v1",
        },
        {
            "type": "reveal",
            "sequence": 7,
            "receipt_digest": reveal["receipt_digest"],
            "idempotency_key": "blind-holder:positive:event:reveal:v1",
        },
        {
            "type": "score",
            "sequence": 8,
            "scoring_rule_digest": score_digest,
            "baseline_receipt_digest": baseline["receipt_digest"],
            "replay_receipt_digest": replay["receipt_digest"],
            "idempotency_key": "blind-holder:positive:event:score:v1",
        },
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_id": PROTOCOL_ID,
        "trial_id": "synthetic-blind-holder-positive-v1",
        "source": source,
        "commitments": commitments,
        "holder_receipt": holder,
        "isolation_receipt": isolation,
        "prediction_request": request,
        "prediction_response": response,
        "reveal_receipt": reveal,
        "replay_receipt": replay,
        "baseline_receipt": baseline,
        "events": events,
        "claim_boundary": CLAIM_BOUNDARY,
    }


def _event(document: dict[str, Any], event_type: str) -> dict[str, Any]:
    return next(event for event in document["events"] if event["type"] == event_type)


def negative_controls() -> dict[str, dict[str, Any]]:
    controls: dict[str, dict[str, Any]] = {}

    def add(name: str, mutate: Any) -> None:
        document = deepcopy(synthetic_positive_control())
        mutate(document)
        controls[name] = {"expected_reason": NEGATIVE_CONTROL_REASONS[name], "document": document}

    def reseal_isolation(value: dict[str, Any]) -> None:
        value["isolation_receipt"] = _seal(value["isolation_receipt"])
        _event(value, "isolation_bound")["receipt_digest"] = value["isolation_receipt"]["receipt_digest"]

    def reseal_reveal(value: dict[str, Any]) -> None:
        value["reveal_receipt"] = _seal(value["reveal_receipt"])
        _event(value, "reveal")["receipt_digest"] = value["reveal_receipt"]["receipt_digest"]

    def reseal_replay(value: dict[str, Any]) -> None:
        value["replay_receipt"] = _seal(value["replay_receipt"])
        _event(value, "score")["replay_receipt_digest"] = value["replay_receipt"]["receipt_digest"]

    def reseal_baseline(value: dict[str, Any]) -> None:
        value["baseline_receipt"] = _seal(value["baseline_receipt"])
        _event(value, "score")["baseline_receipt_digest"] = value["baseline_receipt"]["receipt_digest"]

    def retrieval_before(value: dict[str, Any]) -> None:
        _event(value, "commitments_frozen")["sequence"] = 4
        _event(value, "primary_retrieval")["sequence"] = 1

    def rule_after(value: dict[str, Any]) -> None:
        value["commitments"]["task_schema"]["committed_sequence"] = 5
        value["commitments"] = _seal(value["commitments"])
        _event(value, "commitments_frozen")["receipt_digest"] = value["commitments"]["receipt_digest"]

    def missing_isolation(value: dict[str, Any]) -> None:
        value["isolation_receipt"] = None

    def same_session(value: dict[str, Any]) -> None:
        value["isolation_receipt"]["same_session_with_holder"] = True
        reseal_isolation(value)

    def unverified(value: dict[str, Any]) -> None:
        value["isolation_receipt"]["model_verified"] = False
        reseal_isolation(value)

    def early_reveal(value: dict[str, Any]) -> None:
        _event(value, "response_readback")["sequence"] = 7
        _event(value, "reveal")["sequence"] = 6

    def holder_mismatch(value: dict[str, Any]) -> None:
        value["reveal_receipt"]["revealed_bytes_digest"] = _sha_text("wrong outcome")
        reseal_reveal(value)

    def source_mismatch(value: dict[str, Any]) -> None:
        value["reveal_receipt"]["source_identity_digest"] = _sha_text("wrong source")
        reseal_reveal(value)

    def duplicate_prediction(value: dict[str, Any]) -> None:
        duplicate = deepcopy(_event(value, "prediction"))
        duplicate["sequence"] = 9
        duplicate["idempotency_key"] += ":duplicate"
        value["events"].append(duplicate)

    def duplicate_reveal(value: dict[str, Any]) -> None:
        duplicate = deepcopy(_event(value, "reveal"))
        duplicate["sequence"] = 9
        duplicate["idempotency_key"] += ":duplicate"
        value["events"].append(duplicate)

    def replay_drift(value: dict[str, Any]) -> None:
        value["replay_receipt"]["second_decision_digest"] = _sha_text("drift")
        reseal_replay(value)

    def fabricated(value: dict[str, Any]) -> None:
        value["baseline_receipt"]["fabricated"] = True
        reseal_baseline(value)

    def substitution(value: dict[str, Any]) -> None:
        value["baseline_receipt"]["exposed_input_digest"] = _sha_text("substituted inputs")
        reseal_baseline(value)

    def duplicate_key(value: dict[str, Any]) -> None:
        _event(value, "score")["idempotency_key"] = _event(value, "reveal")["idempotency_key"]

    add("retrieval_before_commitments", retrieval_before)
    add("task_rule_after_retrieval", rule_after)
    add("missing_isolation_receipt", missing_isolation)
    add("same_session_isolation", same_session)
    add("unverified_model_context", unverified)
    add("reveal_before_response", early_reveal)
    add("holder_digest_mismatch", holder_mismatch)
    add("source_identity_mismatch", source_mismatch)
    add("duplicate_prediction", duplicate_prediction)
    add("duplicate_reveal", duplicate_reveal)
    add("replay_drift", replay_drift)
    add("fabricated_baseline", fabricated)
    add("baseline_substitution", substitution)
    add("duplicate_effect_key", duplicate_key)
    return controls


def validate_control_suite() -> dict[str, Any]:
    controls: dict[str, Any] = {}
    for name, fixture in negative_controls().items():
        decision = validate_blind_holder(fixture["document"])
        controls[name] = {
            "expected_reason": fixture["expected_reason"],
            "observed_reason_codes": decision["reason_codes"],
            "valid": decision["valid"],
            "decision_digest": decision["decision_digest"],
        }
    value = {
        "schema_version": SCHEMA_VERSION,
        "protocol_id": PROTOCOL_ID,
        "positive": validate_blind_holder(synthetic_positive_control()),
        "negative_controls": controls,
        "all_negative_controls_fail_for_unique_expected_reason": all(
            item["valid"] is False and item["observed_reason_codes"] == [item["expected_reason"]]
            for item in controls.values()
        ),
        "claim_boundary": CLAIM_BOUNDARY,
    }
    value["suite_digest"] = canonical_digest(value)
    return value


def _main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed blind-holder protocol validator")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("document", type=Path)
    commands.add_parser("positive")
    commands.add_parser("suite")
    args = parser.parse_args()
    if args.command == "validate":
        result = validate_blind_holder(json.loads(args.document.read_text(encoding="utf-8")))
    elif args.command == "positive":
        result = synthetic_positive_control()
    elif args.command == "suite":
        result = validate_control_suite()
    else:  # pragma: no cover
        raise AssertionError("unreachable")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
