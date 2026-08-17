from __future__ import annotations

import json
from pathlib import Path

from agi.verified_macro_active_disambiguation import (
    run_verified_macro_active_disambiguation,
)


def test_ambiguous_verified_macros_fail_closed_then_resolve_after_committed_probe(
    runtime_repo: Path,
) -> None:
    report = run_verified_macro_active_disambiguation(
        runtime_repo,
        "verified-macro-active-disambiguation-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "verified-macro-active-disambiguation"
    assert report["candidate_ids_supplied_by_caller"] is False
    assert report["initial_matching_route_count"] == 2
    assert report["unique_search_failed_closed_on_ambiguity"] is True
    assert report["probe_input"] < 0
    assert report["distinct_route_output_count"] == 2
    assert len(report["route_output_sha256"]) == 2
    assert report["counterexample_answer_source"].startswith("frozen-campaign-abs-oracle")
    assert report["counterexample_answer"] == abs(report["probe_input"])
    assert len(report["refined_selected_candidate_ids"]) == 1
    assert report["refined_selected_depth"] == 1
    assert report["fresh_challenge_count"] == 3
    assert report["fresh_challenge_outputs"] == [13, 6, 21]
    assert report["candidate_trial_state_unchanged"] is True

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "verified-macro-active-disambiguation"
    )
    files = list(evidence_dir.glob("active-disambiguation-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
