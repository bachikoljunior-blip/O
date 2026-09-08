from __future__ import annotations

import json
from pathlib import Path

from agi.recursive_skill_postlearning_retention import (
    run_recursive_skill_postlearning_retention,
)


def test_recursive_memory_skill_survives_later_heterogeneous_learning(
    runtime_repo: Path,
) -> None:
    report = run_recursive_skill_postlearning_retention(
        runtime_repo,
        "recursive-skill-postlearning-retention-seed",
    )

    assert report["passed"] is True
    assert report["recursive_candidate_count"] == 4
    assert report["later_heterogeneous_capability_count"] == 4
    assert report["later_heterogeneous_input_domains"] == [
        "numeric",
        "string",
        "sequence",
        "object",
    ]
    assert report["later_heterogeneous_output_domains"] == [
        "numeric",
        "string",
        "numeric",
        "boolean",
    ]
    assert report["recursive_trials_unchanged_after_later_learning"] is True
    assert report["recursive_trials_unchanged_after_postlearning_replay"] is True
    assert report["later_trials_unchanged_after_recursive_replay"] is True
    assert report["later_negative_evidence_retained"] is True
    assert report["fresh_engine_run_count"] == 8
    assert len(set(report["fresh_engine_runs"])) == 8
    assert report["heldout_case_count"] == 4
    assert report["repeat_count"] == 2
    assert report["all_postlearning_outputs_matched"] is True
    assert report["live_model_invocation_required"] is False

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "recursive-skill-postlearning-retention"
    )
    files = list(evidence_dir.glob("retention-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
