from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from agi.context_routing_experiment import context_routing_protocol_digest
from agi.context_routing_rendezvous import (
    aggregate_context_routing_observation_receipts,
    append_context_routing_observation_receipts,
    build_context_routing_rendezvous,
    create_context_routing_observation_receipts,
    qualify_context_routing_selector,
    validate_context_routing_rendezvous,
    validate_context_routing_selector_registry,
)


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_PATH = ROOT / "agi" / "CONTEXT_ROUTING_EXPERIMENT.json"
REGISTRY_PATH = ROOT / "agi" / "CONTEXT_ROUTING_SELECTOR_REGISTRY.json"
SOURCE_REQUEST_DIGEST = "1" * 64


def _experiment() -> dict:
    return json.loads(EXPERIMENT_PATH.read_text(encoding="utf-8"))


def _rendezvous(value: dict, executor: str = "fresh-selector-v1") -> dict:
    return build_context_routing_rendezvous(
        value,
        rendezvous_id="routing-heldout-rendezvous-v1",
        selector_executor_binding=executor,
        source_request_digest=SOURCE_REQUEST_DIGEST,
    )


def _registry(value: dict, rendezvous: dict, *, exposed: tuple[str, ...] = ()) -> dict:
    public = rendezvous["public_request"]
    return {
        "schema_version": 1,
        "protocol_digest": context_routing_protocol_digest(value),
        "revision": 1,
        "issuer": "test-native-o-coordinator",
        "label_exposures": [
            {
                "executor_binding": executor,
                "reason": "test exposure",
                "evidence_ref": "test:label-exposure",
            }
            for executor in exposed
        ],
        "preauthorizations": [
            {
                "rendezvous_id": public["rendezvous_id"],
                "selector_executor_binding": public["selector_executor_binding"],
                "source_request_digest": public["source_request_digest"],
                "selector_request_digest": public["selector_request_digest"],
                "registered_before_selection": True,
                "sealed_scorer_access_allowed": False,
            }
        ],
    }


def _plan(value: dict) -> dict:
    cases = []
    for case in value["cases"]:
        selected: dict[str, list[str]] = {}
        for path in case["required_paths"]:
            for parent, child in zip(path, path[1:]):
                selected.setdefault(parent, [])
                if child not in selected[parent]:
                    selected[parent].append(child)
        cases.append(
            {
                "case_id": case["case_id"],
                "selections": [
                    {"skill_id": parent, "selected_child_ids": children}
                    for parent, children in selected.items()
                ],
            }
        )
    return {
        "schema_version": 1,
        "protocol_digest": context_routing_protocol_digest(value),
        "cases": cases,
    }


def _submission(rendezvous: dict, qualification: dict, plan: dict) -> dict:
    public = rendezvous["public_request"]
    return {
        "schema_version": 1,
        "kind": "context_routing_selector_submission",
        "rendezvous_id": public["rendezvous_id"],
        "selector_executor_binding": public["selector_executor_binding"],
        "source_request_digest": public["source_request_digest"],
        "protocol_digest": public["protocol_digest"],
        "selector_request_digest": public["selector_request_digest"],
        "qualification_digest": qualification["qualification_digest"],
        "plan": plan,
    }


def _contains_key(value: object, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        return any(key in forbidden for key in value) or any(
            _contains_key(item, forbidden) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_key(item, forbidden) for item in value)
    return False


def test_public_and_sealed_artifacts_are_exactly_separated() -> None:
    value = _experiment()
    rendezvous = validate_context_routing_rendezvous(value, _rendezvous(value))
    public = rendezvous["public_request"]
    sealed = rendezvous["sealed_scorer"]

    assert not _contains_key(
        public,
        {
            "required_paths",
            "forbidden_skill_ids",
            "eager_context_chars",
            "thresholds",
            "decision",
            "labels",
        },
    )
    assert sealed["labels"]
    assert sealed["thresholds"] == value["thresholds"]
    assert sealed["selector_request_digest"] == public["selector_request_digest"]


def test_public_or_sealed_mutation_fails_closed() -> None:
    value = _experiment()
    rendezvous = _rendezvous(value)
    mutated = deepcopy(rendezvous)
    mutated["public_request"]["selection_packet"]["cases"][0]["situation"][
        "tampered"
    ] = True
    with pytest.raises(ValueError, match="public projection"):
        validate_context_routing_rendezvous(value, mutated)

    mutated = deepcopy(rendezvous)
    mutated["sealed_scorer"]["labels"][0]["required_paths"] = []
    with pytest.raises(ValueError, match="scorer projection"):
        validate_context_routing_rendezvous(value, mutated)


def test_checked_in_registry_permanently_excludes_current_context() -> None:
    value = _experiment()
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    validate_context_routing_selector_registry(value, registry)
    rendezvous = _rendezvous(value, executor="current_chatgpt_work_session")
    registry["preauthorizations"] = _registry(value, rendezvous)[
        "preauthorizations"
    ]

    with pytest.raises(ValueError, match="permanently label-exposed"):
        qualify_context_routing_selector(value, rendezvous, registry)


def test_qualification_requires_exact_precommitted_no_scorer_access() -> None:
    value = _experiment()
    rendezvous = _rendezvous(value)
    registry = _registry(value, rendezvous)
    qualification = qualify_context_routing_selector(value, rendezvous, registry)
    assert qualification["selector_request_digest"] == rendezvous[
        "public_request"
    ]["selector_request_digest"]

    for field, replacement, match in (
        ("registered_before_selection", False, "before selection"),
        ("sealed_scorer_access_allowed", True, "deny sealed scorer"),
        ("selector_request_digest", "0" * 64, "lacks one exact"),
    ):
        changed = deepcopy(registry)
        changed["preauthorizations"][0][field] = replacement
        with pytest.raises(ValueError, match=match):
            qualify_context_routing_selector(value, rendezvous, changed)


def test_qualified_submission_creates_complete_cross_bound_receipts(
    tmp_path: Path,
) -> None:
    value = _experiment()
    rendezvous = _rendezvous(value)
    registry = _registry(value, rendezvous)
    qualification = qualify_context_routing_selector(value, rendezvous, registry)
    created = create_context_routing_observation_receipts(
        value,
        rendezvous,
        registry,
        qualification,
        _submission(rendezvous, qualification, _plan(value)),
        fixture_root=tmp_path / "fixture",
    )

    assert len(created["receipts"]) == len(value["cases"])
    assert {item["case_id"] for item in created["receipts"]} == {
        item["case_id"] for item in value["cases"]
    }
    assert all(item["qualification_digest"] == qualification["qualification_digest"] for item in created["receipts"])
    aggregated = aggregate_context_routing_observation_receipts(
        value,
        rendezvous,
        registry,
        qualification,
        created["measured"],
        created["receipts"],
    )
    assert aggregated == created["measured"]
    assert aggregated["decision"]["verdict"] == "ADOPT_FOR_SCOPED_WORK"
    assert aggregated["user_level_verdict"] == "FAIL"


def test_submission_binding_mismatch_is_atomic(tmp_path: Path) -> None:
    value = _experiment()
    before = deepcopy(value)
    rendezvous = _rendezvous(value)
    registry = _registry(value, rendezvous)
    qualification = qualify_context_routing_selector(value, rendezvous, registry)
    submission = _submission(rendezvous, qualification, _plan(value))
    submission["source_request_digest"] = "2" * 64

    with pytest.raises(ValueError, match="cross-binding mismatch"):
        create_context_routing_observation_receipts(
            value,
            rendezvous,
            registry,
            qualification,
            submission,
            fixture_root=tmp_path / "fixture",
        )
    assert value == before
    assert not (tmp_path / "fixture").exists()


def test_partial_receipts_retain_insufficient_evidence(tmp_path: Path) -> None:
    value = _experiment()
    rendezvous = _rendezvous(value)
    registry = _registry(value, rendezvous)
    qualification = qualify_context_routing_selector(value, rendezvous, registry)
    created = create_context_routing_observation_receipts(
        value,
        rendezvous,
        registry,
        qualification,
        _submission(rendezvous, qualification, _plan(value)),
        fixture_root=tmp_path / "fixture",
    )
    retained = aggregate_context_routing_observation_receipts(
        value,
        rendezvous,
        registry,
        qualification,
        created["measured"],
        created["receipts"][:-1],
    )

    assert retained["status"] == "HARNESS_READY"
    assert retained["observations"] == []
    assert retained["decision"]["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert retained["decision"]["scoped_use_authorized"] is False


def test_receipt_tamper_duplicate_and_append_atomicity_fail_closed(
    tmp_path: Path,
) -> None:
    value = _experiment()
    rendezvous = _rendezvous(value)
    registry = _registry(value, rendezvous)
    qualification = qualify_context_routing_selector(value, rendezvous, registry)
    created = create_context_routing_observation_receipts(
        value,
        rendezvous,
        registry,
        qualification,
        _submission(rendezvous, qualification, _plan(value)),
        fixture_root=tmp_path / "fixture",
    )
    receipts = created["receipts"]

    tampered = deepcopy(receipts)
    tampered[0]["observation"]["required_branch_recall"] = 0.0
    with pytest.raises(ValueError, match="digest mismatch"):
        aggregate_context_routing_observation_receipts(
            value,
            rendezvous,
            registry,
            qualification,
            created["measured"],
            tampered,
        )

    existing = [receipts[0]]
    before = deepcopy(existing)
    with pytest.raises(ValueError, match="duplicate"):
        append_context_routing_observation_receipts(existing, [receipts[0]])
    assert existing == before

    malformed = deepcopy(receipts[1])
    malformed["unexpected"] = True
    with pytest.raises(ValueError, match="unexpected schema"):
        append_context_routing_observation_receipts(existing, [malformed])
    assert existing == before

    mixed = deepcopy(receipts[1])
    mixed["source_request_digest"] = "3" * 64
    mixed_without_digest = {
        key: value for key, value in mixed.items() if key != "receipt_digest"
    }
    import hashlib

    mixed["receipt_digest"] = hashlib.sha256(
        json.dumps(
            mixed_without_digest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    with pytest.raises(ValueError, match="cohort mismatch"):
        append_context_routing_observation_receipts(existing, [mixed])
    assert existing == before


def test_post_relabeling_invalidates_rendezvous_and_receipts(tmp_path: Path) -> None:
    value = _experiment()
    rendezvous = _rendezvous(value)
    registry = _registry(value, rendezvous)
    qualification = qualify_context_routing_selector(value, rendezvous, registry)
    created = create_context_routing_observation_receipts(
        value,
        rendezvous,
        registry,
        qualification,
        _submission(rendezvous, qualification, _plan(value)),
        fixture_root=tmp_path / "fixture",
    )
    relabeled = deepcopy(value)
    relabeled["cases"][0]["required_paths"][0][-1] = "controls"

    with pytest.raises(ValueError):
        aggregate_context_routing_observation_receipts(
            relabeled,
            rendezvous,
            registry,
            qualification,
            created["measured"],
            created["receipts"],
        )
