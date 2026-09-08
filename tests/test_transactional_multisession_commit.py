from __future__ import annotations

import json
from pathlib import Path

from agi.transactional_multisession_commit import run_transactional_multisession_commit
from continual.candidate_regression import candidate_verified_for_scope


def test_verified_session_skills_commit_transactionally_and_survive_restart(
    runtime_repo: Path,
) -> None:
    report = run_transactional_multisession_commit(
        runtime_repo,
        "transactional-multisession-commit-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "transactional-multisession-candidate-commit-v1"
    assert report["transactional_commit_count"] == 2
    assert report["tampered_candidate_commit_failed_closed"] is True
    assert report["duplicate_commit_failed_closed"] is True
    assert report["source_state_unchanged"] is True
    assert report["source_trials_unchanged"] is True
    assert report["first_committed_candidate_preserved_during_second_learning"] is True
    assert report["durable_root_contains_both_new_verified_candidates"] is True
    assert report["durable_state_unchanged_by_replay"] is True
    assert report["fresh_state_only_restart_replayed_full_set"] is True
    assert report["fresh_engine_run_count"] == 14
    assert len(set(report["fresh_engine_runs"])) == 14
    assert report["fresh_engine_runs_unique"] is True
    assert report["prior_runs_copied"] is False
    assert report["prior_episodes_copied"] is False
    assert report["prior_evidence_copied"] is False
    assert report["live_model_invocation_required"] is False

    for id_key, scope_key in (
        ("first_committed_candidate_id", "first_committed_scope"),
        ("second_committed_candidate_id", "second_committed_scope"),
    ):
        candidate_id = str(report[id_key])
        candidate_path = (
            runtime_repo
            / ".continual"
            / "candidates"
            / candidate_id
            / "candidate.json"
        )
        assert candidate_path.is_file()
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        assert candidate_verified_for_scope(
            runtime_repo,
            candidate,
            str(report[scope_key]),
        )
        assert list(
            (runtime_repo / ".continual" / "candidates" / candidate_id / "trials").glob(
                "*.json"
            )
        )

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "transactional-multisession-commit"
    )
    files = list(evidence_dir.glob("commit-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
