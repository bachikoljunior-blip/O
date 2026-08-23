from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from .context_routing_experiment import (
    build_context_routing_selection_packet,
    context_routing_protocol_digest,
    measure_context_routing_experiment,
    validate_context_routing_experiment,
    validate_context_routing_selection_plan,
)


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_RECEIPT_FIELDS = {
    "schema_version",
    "kind",
    "rendezvous_id",
    "case_id",
    "selector_executor_binding",
    "source_request_digest",
    "protocol_digest",
    "selector_request_digest",
    "sealed_scorer_digest",
    "qualification_digest",
    "plan_digest",
    "measured_artifact_digest",
    "observation",
    "receipt_digest",
}


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _with_digest(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = deepcopy(dict(value))
    result[field] = _canonical_digest(value)
    return result


def _without_digest(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    return {key: deepcopy(item) for key, item in value.items() if key != field}


def _validate_receipt_envelope(receipt: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(receipt, Mapping) or set(receipt) != _RECEIPT_FIELDS:
        raise ValueError("observation receipt has an unexpected schema")
    if receipt.get("schema_version") != 1:
        raise ValueError("observation receipt schema_version must be 1")
    if receipt.get("kind") != "context_routing_observation_receipt":
        raise ValueError("observation receipt kind is invalid")
    _text(receipt.get("rendezvous_id"), "receipt rendezvous_id")
    _text(receipt.get("case_id"), "receipt case_id")
    _text(receipt.get("selector_executor_binding"), "receipt executor binding")
    for field in (
        "source_request_digest",
        "protocol_digest",
        "selector_request_digest",
        "sealed_scorer_digest",
        "qualification_digest",
        "plan_digest",
        "measured_artifact_digest",
        "receipt_digest",
    ):
        _sha256(receipt.get(field), f"receipt {field}")
    if not isinstance(receipt.get("observation"), Mapping):
        raise ValueError("receipt observation must be an object")
    if receipt.get("receipt_digest") != _canonical_digest(
        _without_digest(receipt, "receipt_digest")
    ):
        raise ValueError("observation receipt digest mismatch")
    return deepcopy(dict(receipt))


def build_context_routing_rendezvous(
    experiment: Mapping[str, Any],
    *,
    rendezvous_id: str,
    selector_executor_binding: str,
    source_request_digest: str,
) -> dict[str, Any]:
    """Build physically separable selector and scorer artifacts for one frozen run."""

    validated = validate_context_routing_experiment(experiment)
    if validated["status"] != "HARNESS_READY":
        raise ValueError("only a HARNESS_READY experiment may open a rendezvous")
    rendezvous_id = _text(rendezvous_id, "rendezvous_id")
    executor = _text(selector_executor_binding, "selector_executor_binding")
    source_digest = _sha256(source_request_digest, "source_request_digest")
    protocol_digest = context_routing_protocol_digest(validated)

    public_core = {
        "schema_version": 1,
        "kind": "context_routing_selector_request",
        "rendezvous_id": rendezvous_id,
        "selector_executor_binding": executor,
        "source_request_digest": source_digest,
        "protocol_digest": protocol_digest,
        "selection_packet": build_context_routing_selection_packet(validated),
    }
    public_request = _with_digest(public_core, "selector_request_digest")

    scorer_core = {
        "schema_version": 1,
        "kind": "context_routing_sealed_scorer",
        "rendezvous_id": rendezvous_id,
        "protocol_digest": protocol_digest,
        "selector_request_digest": public_request["selector_request_digest"],
        "labels": [
            {
                "case_id": case["case_id"],
                "required_paths": deepcopy(case["required_paths"]),
                "forbidden_skill_ids": deepcopy(case["forbidden_skill_ids"]),
                "eager_context_chars": case["eager_context_chars"],
            }
            for case in validated["cases"]
        ],
        "thresholds": deepcopy(validated["thresholds"]),
        "claim_boundary": deepcopy(validated["claim_boundary"]),
    }
    sealed_scorer = _with_digest(scorer_core, "sealed_scorer_digest")
    return {"public_request": public_request, "sealed_scorer": sealed_scorer}


def validate_context_routing_rendezvous(
    experiment: Mapping[str, Any], rendezvous: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(rendezvous, Mapping) or set(rendezvous) != {
        "public_request",
        "sealed_scorer",
    }:
        raise ValueError("rendezvous has an unexpected schema")
    public = rendezvous.get("public_request")
    sealed = rendezvous.get("sealed_scorer")
    if not isinstance(public, Mapping) or not isinstance(sealed, Mapping):
        raise ValueError("rendezvous artifacts must be objects")
    expected = build_context_routing_rendezvous(
        experiment,
        rendezvous_id=_text(public.get("rendezvous_id"), "rendezvous_id"),
        selector_executor_binding=_text(
            public.get("selector_executor_binding"), "selector_executor_binding"
        ),
        source_request_digest=_sha256(
            public.get("source_request_digest"), "source_request_digest"
        ),
    )
    if dict(public) != expected["public_request"]:
        raise ValueError("selector request does not equal the frozen public projection")
    if dict(sealed) != expected["sealed_scorer"]:
        raise ValueError("sealed scorer does not equal the frozen scorer projection")
    return expected


def validate_context_routing_selector_registry(
    experiment: Mapping[str, Any], registry: Mapping[str, Any]
) -> dict[str, Any]:
    validated = validate_context_routing_experiment(experiment)
    expected_fields = {
        "schema_version",
        "protocol_digest",
        "revision",
        "issuer",
        "label_exposures",
        "preauthorizations",
    }
    if not isinstance(registry, Mapping) or set(registry) != expected_fields:
        raise ValueError("selector registry has an unexpected schema")
    if registry.get("schema_version") != 1:
        raise ValueError("selector registry schema_version must be 1")
    if registry.get("protocol_digest") != context_routing_protocol_digest(validated):
        raise ValueError("selector registry does not bind the frozen protocol")
    revision = registry.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError("selector registry revision must be a positive integer")
    _text(registry.get("issuer"), "selector registry issuer")

    exposures = registry.get("label_exposures")
    if not isinstance(exposures, list):
        raise ValueError("label_exposures must be an array")
    exposure_ids: set[str] = set()
    for exposure in exposures:
        if not isinstance(exposure, Mapping) or set(exposure) != {
            "executor_binding",
            "reason",
            "evidence_ref",
        }:
            raise ValueError("label exposure has an unexpected schema")
        executor = _text(exposure.get("executor_binding"), "exposed executor")
        _text(exposure.get("reason"), "label exposure reason")
        _text(exposure.get("evidence_ref"), "label exposure evidence_ref")
        if executor in exposure_ids:
            raise ValueError("label-exposed executor bindings must be unique")
        exposure_ids.add(executor)

    authorizations = registry.get("preauthorizations")
    if not isinstance(authorizations, list):
        raise ValueError("preauthorizations must be an array")
    authorization_keys: set[tuple[str, str, str]] = set()
    for authorization in authorizations:
        expected_authorization_fields = {
            "rendezvous_id",
            "selector_executor_binding",
            "source_request_digest",
            "selector_request_digest",
            "registered_before_selection",
            "sealed_scorer_access_allowed",
        }
        if not isinstance(authorization, Mapping) or set(authorization) != expected_authorization_fields:
            raise ValueError("selector preauthorization has an unexpected schema")
        key = (
            _text(authorization.get("rendezvous_id"), "preauthorization rendezvous_id"),
            _text(
                authorization.get("selector_executor_binding"),
                "preauthorization selector_executor_binding",
            ),
            _sha256(
                authorization.get("selector_request_digest"),
                "preauthorization selector_request_digest",
            ),
        )
        _sha256(
            authorization.get("source_request_digest"),
            "preauthorization source_request_digest",
        )
        if authorization.get("registered_before_selection") is not True:
            raise ValueError("selector must be registered before selection")
        if authorization.get("sealed_scorer_access_allowed") is not False:
            raise ValueError("selector preauthorization must deny sealed scorer access")
        if key in authorization_keys:
            raise ValueError("selector preauthorizations must be unique")
        authorization_keys.add(key)
    return deepcopy(dict(registry))


def qualify_context_routing_selector(
    experiment: Mapping[str, Any],
    rendezvous: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> dict[str, Any]:
    validated_rendezvous = validate_context_routing_rendezvous(experiment, rendezvous)
    validated_registry = validate_context_routing_selector_registry(experiment, registry)
    public = validated_rendezvous["public_request"]
    sealed = validated_rendezvous["sealed_scorer"]
    executor = public["selector_executor_binding"]
    exposed = {
        item["executor_binding"] for item in validated_registry["label_exposures"]
    }
    if executor in exposed:
        raise ValueError("selector executor is permanently label-exposed")
    matching = [
        item
        for item in validated_registry["preauthorizations"]
        if item["rendezvous_id"] == public["rendezvous_id"]
        and item["selector_executor_binding"] == executor
        and item["source_request_digest"] == public["source_request_digest"]
        and item["selector_request_digest"] == public["selector_request_digest"]
    ]
    if len(matching) != 1:
        raise ValueError("selector lacks one exact pre-selection authorization")
    core = {
        "schema_version": 1,
        "kind": "context_routing_selector_qualification",
        "rendezvous_id": public["rendezvous_id"],
        "selector_executor_binding": executor,
        "source_request_digest": public["source_request_digest"],
        "protocol_digest": public["protocol_digest"],
        "selector_request_digest": public["selector_request_digest"],
        "sealed_scorer_digest": sealed["sealed_scorer_digest"],
        "registry_revision": validated_registry["revision"],
        "registry_digest": _canonical_digest(validated_registry),
    }
    return _with_digest(core, "qualification_digest")


def _validate_qualification(
    experiment: Mapping[str, Any],
    rendezvous: Mapping[str, Any],
    registry: Mapping[str, Any],
    qualification: Mapping[str, Any],
) -> dict[str, Any]:
    expected = qualify_context_routing_selector(experiment, rendezvous, registry)
    if not isinstance(qualification, Mapping) or dict(qualification) != expected:
        raise ValueError("selector qualification does not match trusted registry state")
    return expected


def create_context_routing_observation_receipts(
    experiment: Mapping[str, Any],
    rendezvous: Mapping[str, Any],
    registry: Mapping[str, Any],
    qualification: Mapping[str, Any],
    submission: Mapping[str, Any],
    *,
    fixture_root: Path,
) -> dict[str, Any]:
    """Validate one qualified plan and create deterministic per-case receipts."""

    validated = validate_context_routing_experiment(experiment)
    rv = validate_context_routing_rendezvous(validated, rendezvous)
    q = _validate_qualification(validated, rv, registry, qualification)
    expected_fields = {
        "schema_version",
        "kind",
        "rendezvous_id",
        "selector_executor_binding",
        "source_request_digest",
        "protocol_digest",
        "selector_request_digest",
        "qualification_digest",
        "plan",
    }
    if not isinstance(submission, Mapping) or set(submission) != expected_fields:
        raise ValueError("selector submission has an unexpected schema")
    public = rv["public_request"]
    expected_bindings = {
        "schema_version": 1,
        "kind": "context_routing_selector_submission",
        "rendezvous_id": public["rendezvous_id"],
        "selector_executor_binding": public["selector_executor_binding"],
        "source_request_digest": public["source_request_digest"],
        "protocol_digest": public["protocol_digest"],
        "selector_request_digest": public["selector_request_digest"],
        "qualification_digest": q["qualification_digest"],
    }
    if any(submission.get(key) != value for key, value in expected_bindings.items()):
        raise ValueError("selector submission cross-binding mismatch")
    plan = submission.get("plan")
    if not isinstance(plan, Mapping):
        raise ValueError("selector submission plan must be an object")
    validate_context_routing_selection_plan(validated, plan)
    measured = measure_context_routing_experiment(
        validated, plan, fixture_root=fixture_root
    )
    plan_digest = _canonical_digest(plan)
    measured_digest = _canonical_digest(measured)
    receipts = []
    for observation in measured["observations"]:
        core = {
            "schema_version": 1,
            "kind": "context_routing_observation_receipt",
            "rendezvous_id": public["rendezvous_id"],
            "case_id": observation["case_id"],
            "selector_executor_binding": public["selector_executor_binding"],
            "source_request_digest": public["source_request_digest"],
            "protocol_digest": public["protocol_digest"],
            "selector_request_digest": public["selector_request_digest"],
            "sealed_scorer_digest": rv["sealed_scorer"]["sealed_scorer_digest"],
            "qualification_digest": q["qualification_digest"],
            "plan_digest": plan_digest,
            "measured_artifact_digest": measured_digest,
            "observation": deepcopy(observation),
        }
        receipts.append(_with_digest(core, "receipt_digest"))
    return {"measured": measured, "receipts": receipts}


def append_context_routing_observation_receipts(
    existing: Sequence[Mapping[str, Any]],
    additions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Pure append-only helper; validation failure leaves caller-owned inputs untouched."""

    combined = [deepcopy(dict(item)) for item in existing]
    seen: set[str] = set()
    seen_cases: set[tuple[str, str]] = set()
    cohort: tuple[str, ...] | None = None
    for receipt in [*existing, *additions]:
        validated = _validate_receipt_envelope(receipt)
        digest = validated["receipt_digest"]
        case_key = (
            validated["rendezvous_id"],
            validated["case_id"],
        )
        if digest in seen or case_key in seen_cases:
            raise ValueError("duplicate observation receipt")
        receipt_cohort = tuple(
            validated[field]
            for field in (
                "rendezvous_id",
                "selector_executor_binding",
                "source_request_digest",
                "protocol_digest",
                "selector_request_digest",
                "sealed_scorer_digest",
                "qualification_digest",
                "plan_digest",
                "measured_artifact_digest",
            )
        )
        if cohort is not None and receipt_cohort != cohort:
            raise ValueError("observation receipt cohort mismatch")
        cohort = receipt_cohort
        seen.add(str(digest))
        seen_cases.add(case_key)
    combined.extend(deepcopy(dict(item)) for item in additions)
    return combined


def aggregate_context_routing_observation_receipts(
    experiment: Mapping[str, Any],
    rendezvous: Mapping[str, Any],
    registry: Mapping[str, Any],
    qualification: Mapping[str, Any],
    measured: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Promote only a complete, cross-bound receipt set; otherwise retain no verdict."""

    validated = validate_context_routing_experiment(experiment)
    rv = validate_context_routing_rendezvous(validated, rendezvous)
    q = _validate_qualification(validated, rv, registry, qualification)
    measured_validated = validate_context_routing_experiment(measured)
    if measured_validated["status"] != "MEASURED":
        raise ValueError("receipt aggregation requires a measured artifact")
    measured_digest = _canonical_digest(measured_validated)
    expected_observations = {
        item["case_id"]: item for item in measured_validated["observations"]
    }
    seen_cases: set[str] = set()
    seen_receipts: set[str] = set()
    plan_digests: set[str] = set()
    for receipt in receipts:
        receipt = _validate_receipt_envelope(receipt)
        bindings = {
            "schema_version": 1,
            "kind": "context_routing_observation_receipt",
            "rendezvous_id": rv["public_request"]["rendezvous_id"],
            "selector_executor_binding": rv["public_request"][
                "selector_executor_binding"
            ],
            "source_request_digest": rv["public_request"]["source_request_digest"],
            "protocol_digest": rv["public_request"]["protocol_digest"],
            "selector_request_digest": rv["public_request"][
                "selector_request_digest"
            ],
            "sealed_scorer_digest": rv["sealed_scorer"]["sealed_scorer_digest"],
            "qualification_digest": q["qualification_digest"],
            "measured_artifact_digest": measured_digest,
        }
        if any(receipt.get(key) != value for key, value in bindings.items()):
            raise ValueError("observation receipt cross-binding mismatch")
        plan_digests.add(_sha256(receipt.get("plan_digest"), "receipt plan_digest"))
        case_id = receipt.get("case_id")
        if case_id not in expected_observations or case_id in seen_cases:
            raise ValueError("receipt case is unknown or duplicated")
        if receipt.get("observation") != expected_observations[case_id]:
            raise ValueError("receipt observation does not match measured artifact")
        seen_cases.add(str(case_id))
        digest = str(receipt["receipt_digest"])
        if digest in seen_receipts:
            raise ValueError("receipt digest is duplicated")
        seen_receipts.add(digest)
    if len(plan_digests) > 1:
        raise ValueError("receipt set mixes multiple selector plans")
    if seen_cases != set(expected_observations):
        retained = deepcopy(validated)
        retained["observations"] = []
        retained["status"] = "HARNESS_READY"
        retained["decision"] = {
            "verdict": "INSUFFICIENT_EVIDENCE",
            "reason": "No complete qualified receipt set exists for every frozen case.",
            "scoped_use_authorized": False,
            "global_activation_authorized": False,
        }
        return validate_context_routing_experiment(retained)
    return measured_validated
