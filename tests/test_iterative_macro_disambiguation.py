from __future__ import annotations

import json
from pathlib import Path

from agi.iterative_macro_disambiguation import run_iterative_macro_disambiguation


def test_generated_counterexamples_require_two_rounds_to_isolate_verified_target(
    runtime_repo: Path,
) -> None:
    report = run_iterative_macro_disambiguation(
        runtime_repo,
        "iterative-macro-disambiguation-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "iterative-macro-disambiguation"
    assert report["candidate_ids_supplied_by_caller"] is False
    assert report["probe_pools_supplied_by_caller"] is False
    assert report["initial_matching_route_count"] == 3
    assert report["matching_route_counts"] == [3, 2, 1]
    assert report["counterexample_round_count"] == 2
    assert report["all_probes_committed_before_answers"] is True
    assert len(report["transcript"]) == 2
    assert all(item["answer_known_at_probe_commitment"] is False for item in report["transcript"])
    assert len(report["selected_candidate_ids"]) == 1
    assert report["selected_depth"] == 1
    assert report["fresh_challenge_count"] == 4
    assert report["fresh_challenge_outputs"] == [9, -3, -1, 8]
    assert report["candidate_trial_state_unchanged"] is True

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "iterative-macro-disambiguation"
    )
    files = list(evidence_dir.glob("iterative-disambiguation-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
