from __future__ import annotations

import json
from pathlib import Path

from agi.interleaved_cross_family_retention import (
    run_interleaved_cross_family_retention,
)


def test_recursive_heterogeneous_recursive_learning_retains_all_families(
    runtime_repo: Path,
) -> None:
    report = run_interleaved_cross_family_retention(
        runtime_repo,
        "interleaved-cross-family-retention-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "interleaved-cross-family-retention-v1"
    assert report["acquisition_sequence"] == [
        "recursive_memory_skill",
        "heterogeneous",
        "recursive_memory_skill",
    ]
    assert report["heterogeneous_capability_count"] == 4
    assert report["first_stage_candidate_count"] == 8
    assert report["second_stage_candidate_count"] == 4
    assert report[
        "first_stage_trials_unchanged_after_second_recursive_learning"
    ] is True
    assert report["first_stage_trials_unchanged_after_interleaved_replay"] is True
    assert report["second_stage_trials_unchanged_after_interleaved_replay"] is True
    assert report["heterogeneous_negative_evidence_retained"] is True
    assert report["fresh_engine_run_count"] == 20
    assert len(set(report["fresh_engine_runs"])) == 20
    assert report["repeat_count"] == 2
    assert report["first_recursive_replay_case_count"] == 3
    assert report["heterogeneous_replay_case_count"] == 4
    assert report["second_recursive_replay_case_count"] == 3
    assert report["all_interleaved_outputs_matched"] is True
    assert report["live_model_invocation_required"] is False

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "interleaved-cross-family-retention"
    )
    files = list(evidence_dir.glob("interleaved-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
