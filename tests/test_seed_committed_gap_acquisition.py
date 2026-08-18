from __future__ import annotations

import json
from pathlib import Path

from agi.seed_committed_gap_acquisition import run_seed_committed_gap_acquisition
from agi.seed_committed_target_generator import run_seed_committed_target_generator


def test_seed_committed_generic_grammar_gap_is_acquired_and_retained(
    runtime_repo: Path,
) -> None:
    prerequisite = run_seed_committed_target_generator(
        runtime_repo,
        "seed-grammar-gap-prerequisite",
        max_target_attempts=2,
        required_distinct_semantics=2,
    )
    assert prerequisite["passed"] is True

    report = run_seed_committed_gap_acquisition(
        runtime_repo,
        "seed-committed-generic-grammar-gap",
        max_target_attempts=16,
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "seed-committed-generic-grammar-gap-acquisition-v1"
    assert report["generator_named_fixed_goal_family_used"] is False
    assert report["target_schedule_precommitted_before_support_checks"] is True
    assert report["target_schedule_commitment"]
    assert len(report["support_attempts"]) >= 2
    assert all(
        item["candidate_ids_supplied_by_caller"] is False
        for item in report["support_attempts"]
    )
    assert report["acquisition_target_index"] != report["control_target_index"]
    assert (
        report["acquisition_behavior_fingerprint"]
        != report["control_behavior_fingerprint"]
    )
    assert report["acquisition_target_failed_closed_before_learning"] is True
    assert report["control_target_failed_closed_before_learning"] is True
    assert report["new_candidate_negative_evidence_retained"] is True
    assert report["transactional_commit"]["atomic_directory_rename"] is True
    assert report["transactional_commit"]["overwrite_allowed"] is False
    assert report["solver_candidate_ids_supplied_by_caller"] is False
    assert report["candidate_id"] in report["solver_selected_candidate_ids"]
    assert report["new_candidate_rediscovered"] is True
    assert report["fresh_engine_runs_unique"] is True
    assert len(report["fresh_engine_runs"]) == 2
    assert all(
        item["output"] == item["expected"]
        for item in report["heldback_challenge_outputs"]
    )
    assert report["protected_generated_behavior_retained"] is True
    assert report["control_gap_remained_failed_closed"] is True
    assert report["prior_candidate_state_unchanged"] is True
    assert report["prior_trial_state_unchanged"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_state_unchanged"] is True
    assert report["prior_runs_copied"] is False
    assert report["prior_episodes_copied"] is False
    assert report["prior_evidence_copied"] is False
    assert report["live_model_invocation_required"] is False

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "seed-committed-gap-acquisition"
    )
    files = list(evidence_dir.glob("gap-acquisition-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
    assert persisted["generator_named_fixed_goal_family_used"] is False
    assert "do not establish independent production evidence or AGI" in persisted["claim_boundary"]
