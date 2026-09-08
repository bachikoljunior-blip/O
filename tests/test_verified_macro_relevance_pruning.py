from __future__ import annotations

import json
from pathlib import Path

from agi.verified_macro_relevance_pruning import run_verified_macro_relevance_pruning


def test_typed_relevance_pruning_avoids_whole_library_bound_without_trial_mutation(
    runtime_repo: Path,
) -> None:
    report = run_verified_macro_relevance_pruning(
        runtime_repo,
        "verified-macro-relevance-pruning-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "verified-macro-relevance-pruning"
    assert report["old_unpruned_path_failed_on_four_candidate_bound"] is True
    assert report["library_candidate_count"] == 8
    assert report["relevant_candidate_count"] == 3
    assert report["pruned_candidate_count"] == 5
    assert set(report["numeric_distractor_ids"]).issubset(
        set(report["pruned_candidate_ids"])
    )
    assert report["candidate_ids_supplied_by_caller"] is False
    assert report["typed_relevance_pruning_applied"] is True
    assert report["shortest_type_chain_depth"] == 1
    assert report["selected_chain_depth"] == 2
    assert len(report["selected_candidate_ids"]) == 2
    assert report["candidate_trial_state_unchanged"] is True

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "verified-macro-relevance-pruning"
    )
    files = list(evidence_dir.glob("relevance-pruning-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
