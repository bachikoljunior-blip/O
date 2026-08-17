from __future__ import annotations

import json
from pathlib import Path

from agi.partial_route_probe_disambiguation import run_partial_route_probe_disambiguation


def test_generated_object_probe_uses_verified_partial_rejection_as_evidence(
    runtime_repo: Path,
) -> None:
    report = run_partial_route_probe_disambiguation(
        runtime_repo,
        "partial-route-probe-disambiguation-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "partial-route-probe-disambiguation"
    assert report["candidate_ids_supplied_by_caller"] is False
    assert report["probe_pool_supplied_by_caller"] is False
    assert report["generated_probe_count"] > 0
    assert report["initial_matching_route_count"] == 2
    assert isinstance(report["probe_input"], dict)
    assert "flag" not in report["probe_input"]
    assert report["distinct_route_outcome_count"] == 2
    assert report["verified_rejection_count"] == 1
    assert len(report["route_outcome_sha256"]) == 2
    assert report["counterexample_answer"] is False
    assert len(report["refined_selected_candidate_ids"]) == 1
    assert report["refined_selected_depth"] == 1
    assert report["fresh_challenge_count"] == 4
    assert report["fresh_challenge_outputs"] == [False, True, True, False]
    assert report["candidate_trial_state_unchanged"] is True

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "partial-route-probe-disambiguation"
    )
    files = list(evidence_dir.glob("partial-route-probe-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
