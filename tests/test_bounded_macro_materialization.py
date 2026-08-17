from __future__ import annotations

import json
from pathlib import Path

from agi.bounded_macro_materialization import run_bounded_macro_materialization


def test_bounded_search_plan_is_regression_materialized_and_replays_on_fresh_engines(
    runtime_repo: Path,
) -> None:
    report = run_bounded_macro_materialization(
        runtime_repo,
        "bounded-macro-materialization-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "bounded-macro-materialization"
    assert report["source_candidate_count"] == 6
    assert report["selected_depth"] == 2
    assert len(report["selected_candidate_ids"]) == 2
    assert report["baseline_before_compiled_regression_failed_closed"] is True
    assert report["adverse_compiled_regression_rejected"] is True
    assert report["compiled_candidate_promoted"] is True
    assert report["challenge_generated_after_compiled_promotion"] is True
    assert report["challenge_case_count"] == 3
    assert report["source_fresh_run_count"] == 9
    assert report["compiled_fresh_run_count"] == 9
    assert report["all_outputs_equal"] is True
    assert report["source_candidate_trial_state_unchanged"] is True
    assert report["compiled_candidate_trial_state_unchanged"] is True

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "bounded-macro-materialization"
    )
    files = list(evidence_dir.glob("materialization-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
