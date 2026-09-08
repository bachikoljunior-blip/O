from __future__ import annotations

import json
from pathlib import Path

from agi.failure_driven_durable_acquisition import (
    run_failure_driven_durable_acquisition,
)
from continual.candidate_regression import candidate_verified_for_scope


def test_unsupported_behavior_drives_verified_durable_acquisition_and_retrieval(
    runtime_repo: Path,
) -> None:
    report = run_failure_driven_durable_acquisition(
        runtime_repo,
        "failure-driven-durable-acquisition-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "failure-driven-durable-acquisition-v1"
    assert report["initial_gap_failed_closed"] is True
    assert report["new_candidate_negative_evidence_retained"] is True
    assert report["older_candidate_state_unchanged_by_learning_and_commit"] is True
    assert report["older_candidate_trials_unchanged_by_learning_and_commit"] is True
    assert report["caller_supplied_candidate_ids_for_retrieval"] is False
    assert report["retrieved_new_candidate"] is True
    assert report["retrieved_candidate_ids"] == [report["new_candidate_id"]]
    assert report["retrieved_chain_depth"] == 1
    assert report["challenge_generated_after_retrieval"] is True
    assert report["fresh_engine_runs_unique"] is True
    assert len(set(report["fresh_engine_runs"])) == 2
    assert report["resolver_state_unchanged"] is True
    assert report["resolver_trials_unchanged"] is True
    assert report["source_state_unchanged_after_retrieval"] is True
    assert report["source_trials_unchanged_after_retrieval"] is True
    assert report["prior_runs_copied"] is False
    assert report["prior_episodes_copied"] is False
    assert report["prior_evidence_copied"] is False
    assert report["live_model_invocation_required"] is False

    candidate_path = (
        runtime_repo
        / ".continual"
        / "candidates"
        / str(report["new_candidate_id"])
        / "candidate.json"
    )
    assert candidate_path.is_file()
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert candidate_verified_for_scope(
        runtime_repo,
        candidate,
        str(report["new_candidate_scope"]),
    )

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "failure-driven-durable-acquisition"
    )
    files = list(evidence_dir.glob("acquisition-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
