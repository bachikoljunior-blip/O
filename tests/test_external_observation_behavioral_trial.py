from __future__ import annotations

import ast
import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from agi.external_observation_behavioral_trial import (
    CASE_IDS,
    ExternalObservationBehavioralTrialError,
    canonical_digest,
    evaluate_publication_case,
    run_external_observation_behavioral_trial,
    validate_trial_spec,
)


ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "agi" / "EXTERNAL_OBSERVATION_BEHAVIORAL_TRIAL_SPEC.json"
RESULT_PATH = ROOT / "agi" / "EXTERNAL_OBSERVATION_BEHAVIORAL_TRIAL_RESULT.json"
MODULE_PATH = ROOT / "src" / "agi" / "external_observation_behavioral_trial.py"

EXPECTED_DECISIONS = {
    "eligible-fresh-exact-head": ("ALLOW", ["eligible_fresh_exact_head_success_receipt"]),
    "absent-observation": ("HOLD", ["observation_missing"]),
    "expired-observation": ("HOLD", ["observation_stale"]),
    "wrong-head-observation": ("HOLD", ["workflow_head_mismatch"]),
    "request-digest-invalid": ("HOLD", ["request_digest_invalid"]),
    "incomplete-required-job": ("HOLD", ["required_job_topology_incomplete"]),
    "authority-conflicting-observation": ("HOLD", ["authority_conflict"]),
}


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _spec() -> dict:
    return _json(SPEC_PATH)


def test_checked_in_result_is_exact_deterministic_trial_output() -> None:
    expected = _json(RESULT_PATH)
    observed = run_external_observation_behavioral_trial(ROOT)
    assert observed == expected
    assert observed["decision"]["verdict"] == "PASS"
    assert observed["status"] == "MEASURED"
    digest_body = deepcopy(observed)
    supplied_digest = digest_body.pop("report_digest")
    assert canonical_digest(digest_body) == supplied_digest
    assert observed["spec_digest"] == canonical_digest(_spec())


def test_only_fresh_exact_head_receipt_changes_publication_decision() -> None:
    result = _json(RESULT_PATH)
    cases = result["case_results"]
    assert tuple(item["case_id"] for item in cases) == CASE_IDS
    assert {
        item["case_id"]: (item["decision"], item["reasons"])
        for item in cases
    } == EXPECTED_DECISIONS
    assert result["paired_outcome"] == {
        "without_admissible_observation": "HOLD",
        "with_eligible_fresh_exact_head_observation": "ALLOW",
        "controlled_difference": "observation_eligibility_only",
    }
    assert result["summary"] == {
        "case_count": 7,
        "allow_count": 1,
        "hold_count": 6,
        "eligible_allow_count": 1,
        "unsafe_allow_count": 0,
        "deterministic_replay_count": 21,
        "all_expectations_matched": True,
    }
    integrity = {item["case_id"]: item["input_integrity"] for item in cases}
    assert integrity["absent-observation"]["receipt_digest_valid"] is None
    assert integrity["request-digest-invalid"] == {
        "request_digest_valid": False,
        "receipt_digest_valid": True,
    }
    for case_id, value in integrity.items():
        if case_id not in {"absent-observation", "request-digest-invalid"}:
            assert value == {
                "request_digest_valid": True,
                "receipt_digest_valid": True,
            }


def test_all_precommitted_replays_are_identical() -> None:
    result = _json(RESULT_PATH)
    replay_total = 0
    for case in result["case_results"]:
        assert case["expectation_matched"] is True
        assert case["replay_count"] == 3
        assert len(case["replay_digests"]) == 3
        assert len(set(case["replay_digests"])) == 1
        replay_total += case["replay_count"]
    assert replay_total == 21


def test_trial_has_no_production_or_repository_effect() -> None:
    result = _json(RESULT_PATH)
    audit = result["mutation_audit"]
    assert audit["protected_path_digests_before"] == audit["protected_path_digests_after"]
    assert audit["changed_paths"] == []
    assert audit["effect_events"] == []
    for field in (
        "merge_performed",
        "dispatch_performed",
        "native_invocation_performed",
        "candidate_activation_performed",
        "provider_call_performed",
        "repository_mutation_performed",
    ):
        assert audit[field] is False
    boundary = result["claim_boundary"]
    assert boundary["agi_claim_supported"] is False
    assert boundary["user_goal_completed"] is False
    assert boundary["production_effect_performed"] is False


def test_frozen_source_and_protected_blob_bindings_are_exact() -> None:
    spec = _spec()
    result = _json(RESULT_PATH)
    assert result["source_commit"] == "c4a126675f2d415e0935f9ad03f0664e37caaea2"
    assert result["source_binding"] == {
        "request_path": spec["source_request"]["path"],
        "request_git_blob_sha": "bd7d4852e7415bd852ba846cdfdd3a4a1b96074f",
        "request_digest": "c773a121e99e01d0e520330c537bef23dbf62b726ca1db4708554096d60e6c28",
        "receipt_path": spec["source_receipt"]["path"],
        "receipt_git_blob_sha": "740a06ea2246de608318c8950ff44358860d72db",
        "receipt_digest": "829bc3771644140c646651ccb19a36fe8e2f337535c226f29a9eab0cab532588",
        "decision_time": "2026-08-30T01:28:10Z",
    }
    assert {item["path"]: item["git_blob_sha"] for item in spec["protected_paths"]} == {
        spec["source_request"]["path"]: "bd7d4852e7415bd852ba846cdfdd3a4a1b96074f",
        spec["source_receipt"]["path"]: "740a06ea2246de608318c8950ff44358860d72db",
        ".continual/work-model/invocations/invoke-7869112b0d411a16c51c8cfc/request.json": "3a78e24f1da67e3e40a7295c33748d81c03741b4",
        ".continual/work-model/invocations/invoke-7869112b0d411a16c51c8cfc/response.json": "68537651c55e9e68db665283074d21e8f08e0a38",
        ".continual/work-model/invocations/invoke-1fcc456eaec7f8f134759b06/request.json": "8f877ca0be349e6736ba803e596549f86cf91727",
    }


def test_valid_shape_with_tampered_receipt_is_fail_closed() -> None:
    raw_spec = _spec()
    spec = validate_trial_spec(raw_spec, root=ROOT)
    request = _json(ROOT / spec["source_request"]["path"])
    receipt = _json(ROOT / spec["source_receipt"]["path"])
    receipt["unknowns"].append("tamper that leaves all semantic bindings unchanged")
    trace = evaluate_publication_case(spec, request, receipt, spec["cases"][0])
    assert trace["decision"] == "HOLD"
    assert trace["reasons"] == ["receipt_digest_invalid"]
    assert trace["admitted_receipt"] is None


def test_spec_validation_rejects_reorder_escape_and_unknown_fields() -> None:
    reordered = deepcopy(_spec())
    reordered["cases"][0], reordered["cases"][1] = (
        reordered["cases"][1],
        reordered["cases"][0],
    )
    with pytest.raises(ExternalObservationBehavioralTrialError, match="case IDs and order"):
        validate_trial_spec(reordered, root=ROOT)

    escaped = deepcopy(_spec())
    escaped["protected_paths"][0]["path"] = "../outside.json"
    with pytest.raises(ExternalObservationBehavioralTrialError, match="confined"):
        validate_trial_spec(escaped, root=ROOT)

    expanded = deepcopy(_spec())
    expanded["unexpected"] = True
    with pytest.raises(ExternalObservationBehavioralTrialError, match="unexpected schema"):
        validate_trial_spec(expanded, root=ROOT)


def test_protected_blob_mismatch_stops_before_trial(tmp_path: Path) -> None:
    spec = _spec()
    for binding in spec["protected_paths"]:
        source = ROOT / binding["path"]
        target = tmp_path / binding["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    local_spec = tmp_path / "agi" / SPEC_PATH.name
    local_spec.parent.mkdir(parents=True, exist_ok=True)
    local_spec.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    corrupted = tmp_path / spec["protected_paths"][-1]["path"]
    corrupted.write_bytes(corrupted.read_bytes() + b"\n")
    with pytest.raises(ExternalObservationBehavioralTrialError, match="protected path Git blob mismatch"):
        run_external_observation_behavioral_trial(tmp_path)


def test_trial_module_has_no_mutating_or_network_execution_surface() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    banned_import_roots = {
        "httpx",
        "requests",
        "socket",
        "subprocess",
        "urllib",
    }
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])
    assert imported_roots.isdisjoint(banned_import_roots)

    banned_calls = {
        "open",
        "mkdir",
        "rename",
        "rmdir",
        "touch",
        "unlink",
        "write_bytes",
        "write_text",
    }
    observed_calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    observed_calls.update(
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    )
    assert observed_calls.isdisjoint(banned_calls)
