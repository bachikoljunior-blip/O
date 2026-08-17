from __future__ import annotations

import json
from pathlib import Path

from agi.verified_macro_library_discovery import run_verified_macro_library_discovery


def test_verified_macro_library_is_discovered_without_caller_candidate_ids_and_regated(
    runtime_repo: Path,
) -> None:
    report = run_verified_macro_library_discovery(
        runtime_repo,
        "verified-macro-library-discovery-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "verified-macro-library-discovery"
    assert report["candidate_ids_supplied_by_caller"] is False
    assert report["library_candidate_count"] >= 3
    assert report["shortest_type_chain_depth"] == 1
    assert report["selected_chain_depth"] == 2
    assert report["selected_candidate_ids"] == [
        report["selected_sum_candidate_id"],
        report["selected_to_string_candidate_id"],
    ]
    assert report["shorter_direct_distractor_candidate_id"] not in report[
        "selected_candidate_ids"
    ]
    assert report["planning_examples_overlap_component_support"] is False
    assert report["baseline_before_compiled_regression_failed_closed"] is True
    assert report["adverse_compiled_regression_rejected"] is True
    assert report["compiled_candidate_promoted"] is True
    assert report["challenge_generated_after_compiled_promotion"] is True
    assert report["challenge_inputs_overlap_planning_or_support"] is False
    assert report["challenge_case_count"] == 3
    assert report["source_fresh_run_count"] == 9
    assert report["compiled_fresh_run_count"] == 9
    assert report["all_outputs_equal"] is True
    assert report["generated_component_trial_state_unchanged"] is True
    assert report["all_discovered_source_trial_state_unchanged"] is True
    assert report["compiled_candidate_trial_state_unchanged"] is True
    assert report["live_model_invocation_required"] is False

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "verified-macro-library-discovery"
    )
    files = list(evidence_dir.glob("library-discovery-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
