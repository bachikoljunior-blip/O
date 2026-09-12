from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from agi.recursive_context_retrieval_ablation import (
    RecursiveContextAblationError,
    load_recursive_context_retrieval_fixtures,
    run_recursive_context_retrieval_ablation,
    validate_recursive_context_retrieval_fixtures,
    validate_recursive_context_retrieval_report,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "agi" / "RECURSIVE_CONTEXT_RETRIEVAL_FIXTURES.json"
REPORT_PATH = ROOT / "agi" / "RECURSIVE_CONTEXT_RETRIEVAL_ABLATION.json"
SOURCE_ARCHIVE = ROOT / "tests" / "fixtures" / "recursive_context_retrieval_sources"


@pytest.fixture
def frozen_root(tmp_path: Path) -> Path:
    """Replay the historical fixture against its original source bytes."""
    provenance = json.loads((SOURCE_ARCHIVE / "SOURCE_PROVENANCE.json").read_text())
    manifest = _raw_fixtures()
    assert manifest["base_commit"] == provenance["base_commit"]
    assert hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest() == provenance["fixture_sha256"]
    assert hashlib.sha256(REPORT_PATH.read_bytes()).hexdigest() == provenance["report_sha256"]
    root = tmp_path / "frozen-repository"
    expected = {
        source["repository_path"]: source["content_sha256"]
        for fixture in manifest["fixtures"]
        for source in fixture["sources"]
    }
    assert len(provenance["sources"]) == len(expected)
    assert {
        source["repository_path"]: source["content_sha256"]
        for source in provenance["sources"]
    } == expected
    for source in provenance["sources"]:
        assert source["archive_file"] == source["content_sha256"] + ".txt"
        raw = (SOURCE_ARCHIVE / source["archive_file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == source["content_sha256"]
        path = (root / source["repository_path"]).resolve()
        assert root.resolve() in path.parents
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    (root / "agi" / FIXTURE_PATH.name).write_bytes(FIXTURE_PATH.read_bytes())
    (root / "agi" / REPORT_PATH.name).write_bytes(REPORT_PATH.read_bytes())
    return root


def _raw_fixtures() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _fixture(report: dict, fixture_id: str) -> dict:
    return next(item for item in report["fixtures"] if item["fixture_id"] == fixture_id)


def _rejection(result: dict, source_id: str) -> dict:
    return next(
        item for item in result["rejected_sources"] if item["source_id"] == source_id
    )


def _run_mutated(tmp_path: Path, value: dict, root: Path) -> dict:
    path = tmp_path / "fixtures.json"
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return run_recursive_context_retrieval_ablation(root, fixture_path=path)


def test_checked_in_three_fixture_ablation_passes_with_narrow_claim(frozen_root: Path) -> None:
    report = run_recursive_context_retrieval_ablation(frozen_root)

    assert report["status"] == "MEASURED"
    assert report["decision"]["verdict"] == "PASS"
    assert report["summary"] == {
        "fixture_count": 3,
        "baseline_mean_required_source_recall": 1 / 3,
        "recursive_mean_required_source_recall": 1.0,
        "mean_recall_delta": 0.6666666666666667,
        "recursive_unsafe_admission_count": 0,
        "stale_source_rejection_count": 1,
        "authority_conflict_rejection_count": 1,
        "deterministic_replay_count": 6,
    }
    assert report["production_activation"] is False
    assert report["claim_boundary"] == {
        "scope": "three frozen repository fixtures at base commit 523f171; non-production ablation only",
        "current_context_kernel_modified": False,
        "production_routing_activated": False,
        "candidate_activated": False,
        "external_independent_evidence": False,
        "agi_claim_supported": False,
        "user_goal_completed": False,
    }


def test_two_hop_variant_recovers_exact_transitive_source_flat_selector_misses(frozen_root: Path) -> None:
    result = _fixture(
        run_recursive_context_retrieval_ablation(frozen_root),
        "missing-transitive-dependency",
    )

    assert [item["source_id"] for item in result["baseline"]["selected_sources"]] == [
        "work-strategy"
    ]
    assert [item["source_id"] for item in result["recursive"]["selected_sources"]] == [
        "work-strategy",
        "context-kernel-architecture",
        "work-source-observation-contract",
    ]
    receipt = next(
        item
        for item in result["recursive"]["selected_sources"]
        if item["source_id"] == "work-source-observation-contract"
    )
    assert receipt["depth"] == 2
    assert receipt["parent_source_id"] == "context-kernel-architecture"
    assert result["baseline"]["required_source_recall"] == 1 / 3
    assert result["recursive"]["required_source_recall"] == 1.0
    assert result["recursive"]["unsafe_admission_count"] == 0


def test_stale_and_authority_competitors_have_exact_fail_closed_reasons(frozen_root: Path) -> None:
    report = run_recursive_context_retrieval_ablation(frozen_root)
    stale = _fixture(report, "stale-competing-source")["recursive"]
    authority = _fixture(report, "authority-conflicting-source")["recursive"]

    assert _rejection(stale, "stale-ci-observation")["reasons"] == [
        "stale_at_decision_time"
    ]
    assert _rejection(authority, "legacy-autonomy-state")["reasons"] == [
        "authority_conflict:work-state->o-work-mode-monitor"
    ]
    assert "stale-ci-observation" not in {
        item["source_id"] for item in stale["selected_sources"]
    }
    assert "legacy-autonomy-state" not in {
        item["source_id"] for item in authority["selected_sources"]
    }


def test_replays_and_report_digest_are_byte_stable(frozen_root: Path) -> None:
    first = run_recursive_context_retrieval_ablation(frozen_root)
    second = run_recursive_context_retrieval_ablation(frozen_root)
    body = deepcopy(first)
    supplied = body.pop("report_digest")
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert first == second
    assert supplied == hashlib.sha256(encoded).hexdigest()
    assert all(item["deterministic_replay_verified"] for item in first["fixtures"])


def test_repository_provenance_digest_and_path_guards_fail_closed(frozen_root: Path) -> None:
    value = _raw_fixtures()
    value["fixtures"][0]["sources"][0]["content_sha256"] = "0" * 64
    with pytest.raises(
        RecursiveContextAblationError,
        match="repository content digest mismatch",
    ):
        validate_recursive_context_retrieval_fixtures(value, root=frozen_root)

    value = _raw_fixtures()
    value["fixtures"][0]["sources"][0]["repository_path"] = "../outside.json"
    with pytest.raises(
        RecursiveContextAblationError,
        match="confined repository-relative",
    ):
        validate_recursive_context_retrieval_fixtures(value, root=frozen_root)


def test_freshness_regression_rejects_required_source_without_unsafe_admission(
    tmp_path: Path,
    frozen_root: Path,
) -> None:
    value = _raw_fixtures()
    source = value["fixtures"][1]["sources"][2]
    assert source["source_id"] == "fresh-ci-observation-contract"
    source["valid_until"] = "2026-08-28T12:30:00Z"

    report = _run_mutated(tmp_path, value, frozen_root)
    result = _fixture(report, "stale-competing-source")["recursive"]

    assert report["decision"]["verdict"] == "FAIL"
    assert _rejection(result, source["source_id"])["reasons"] == [
        "stale_at_decision_time"
    ]
    assert source["source_id"] in result["missing_required_source_ids"]
    assert result["unsafe_admission_count"] == 0


def test_invalidation_regression_rejects_required_transitive_source(
    tmp_path: Path,
    frozen_root: Path,
) -> None:
    value = _raw_fixtures()
    value["fixtures"][0]["active_invalidations"] = ["receipt-contract-change"]

    report = _run_mutated(tmp_path, value, frozen_root)
    result = _fixture(report, "missing-transitive-dependency")["recursive"]

    assert report["decision"]["verdict"] == "FAIL"
    assert _rejection(result, "work-source-observation-contract")["reasons"] == [
        "invalidated:receipt-contract-change"
    ]
    assert result["unsafe_admission_count"] == 0


def test_authority_binding_regression_rejects_formerly_authoritative_source(
    tmp_path: Path,
    frozen_root: Path,
) -> None:
    value = _raw_fixtures()
    value["fixtures"][2]["authority_bindings"]["work-state"] = "new-work-state"

    report = _run_mutated(tmp_path, value, frozen_root)
    result = _fixture(report, "authority-conflicting-source")["recursive"]

    assert report["decision"]["verdict"] == "FAIL"
    assert _rejection(result, "authoritative-monitor-state")["reasons"] == [
        "authority_conflict:work-state->new-work-state"
    ]
    assert "authoritative-monitor-state" in result["missing_required_source_ids"]
    assert result["unsafe_admission_count"] == 0


def test_checked_in_report_equals_fresh_recomputation(frozen_root: Path) -> None:
    fixtures = load_recursive_context_retrieval_fixtures(FIXTURE_PATH, root=frozen_root)
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

    assert fixtures["experiment_id"] == "recursive-context-retrieval-ablation-v1"
    assert validate_recursive_context_retrieval_report(report, root=frozen_root) == report
