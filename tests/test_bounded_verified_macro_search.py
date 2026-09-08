from __future__ import annotations

import json
from pathlib import Path

from agi.bounded_verified_macro_search import run_bounded_verified_macro_search


def test_bounded_verified_macro_search_stops_at_unique_minimal_behavior_route(
    runtime_repo: Path,
) -> None:
    report = run_bounded_verified_macro_search(
        runtime_repo,
        "bounded-verified-macro-search-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "bounded-verified-macro-search"
    assert report["generated_candidate_count"] == 14
    assert report["legacy_full_typed_route_count"] > 1000
    assert report["expanded_prefix_count"] < report["legacy_full_typed_route_count"]
    assert report["evaluated_complete_route_count"] < 64
    assert report["selected_depth"] == 2
    assert len(report["selected_candidate_ids"]) == 2
    assert report["candidate_ids_supplied_by_caller"] is False
    assert report["candidate_trial_state_unchanged"] is True
    assert report["fresh_challenge_count"] == 3
    assert report["fresh_challenge_outputs"] == ["23", "8", "31"]

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "bounded-verified-macro-search"
    )
    files = list(evidence_dir.glob("bounded-search-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
