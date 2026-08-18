from __future__ import annotations

import json
from pathlib import Path

from agi.cross_family_order_retention import run_cross_family_order_retention


def test_heterogeneous_skills_survive_later_recursive_memory_learning(
    runtime_repo: Path,
) -> None:
    report = run_cross_family_order_retention(
        runtime_repo,
        "cross-family-order-retention-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "cross-family-order-retention-v1"
    assert report["acquisition_order"] == [
        "heterogeneous",
        "recursive_memory_skill",
    ]
    assert report["heterogeneous_candidate_count"] == 4
    assert report["heterogeneous_input_domains"] == [
        "numeric",
        "string",
        "sequence",
        "object",
    ]
    assert report["heterogeneous_output_domains"] == [
        "numeric",
        "string",
        "numeric",
        "boolean",
    ]
    assert report["heterogeneous_negative_evidence_retained"] is True
    assert report["recursive_candidate_count"] == 4
    assert report["heterogeneous_trials_unchanged_after_recursive_learning"] is True
    assert report["heterogeneous_trials_unchanged_after_cross_replay"] is True
    assert report["recursive_trials_unchanged_after_cross_replay"] is True
    assert report["fresh_engine_run_count"] == 16
    assert len(set(report["fresh_engine_runs"])) == 16
    assert report["repeat_count"] == 2
    assert report["heterogeneous_replay_case_count"] == 4
    assert report["recursive_replay_case_count"] == 4
    assert report["all_cross_family_outputs_matched"] is True
    assert report["live_model_invocation_required"] is False

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "cross-family-order-retention"
    )
    files = list(evidence_dir.glob("order-retention-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
