from __future__ import annotations

import json
from pathlib import Path

from agi.adaptive_depth_composition import run_adaptive_depth_composition
from agi.cross_domain_seed_gap_acquisition import (
    _precommit_string_gap_schedule,
    run_cross_domain_seed_gap_acquisition,
)


def test_string_gap_schedule_is_seed_committed_and_generic() -> None:
    first = _precommit_string_gap_schedule("cross-domain-string-schedule-test")
    second = _precommit_string_gap_schedule("cross-domain-string-schedule-test")

    assert first == second
    assert first["domain"] == "string"
    assert first["generator_named_fixed_goal_family_used"] is False
    assert len(first["target_schedule"]) >= 4
    assert all(
        '"op":"concat_string"'
        in json.dumps(item["expression"], sort_keys=True, separators=(",", ":"))
        for item in first["target_schedule"]
    )
    assert len({item["behavior_fingerprint"] for item in first["target_schedule"]}) == len(
        first["target_schedule"]
    )


def test_cross_domain_seed_gap_acquisition_retains_numeric_and_learns_string(
    runtime_repo: Path,
) -> None:
    prerequisite = run_adaptive_depth_composition(
        runtime_repo,
        "cross-domain-string-gap-base-library",
    )
    assert prerequisite["passed"] is True

    report = run_cross_domain_seed_gap_acquisition(
        runtime_repo,
        "cross-domain-string-gap-test",
        max_target_attempts=16,
    )

    assert report["passed"] is True
    assert report["source_domains"] == ["numeric", "string"]
    assert report["new_target_domain"] == "string"
    assert report["target_schedule_precommitted_before_support_checks"] is True
    assert report["generator_named_fixed_goal_family_used"] is False
    assert report["acquisition_target_failed_closed_before_learning"] is True
    assert report["control_target_failed_closed_before_learning"] is True
    assert report["numeric_prerequisite_retained_after_string_learning"] is True
    assert report["new_candidate_negative_evidence_retained"] is True
    assert report["transactional_commit"]["atomic_directory_rename"] is True
    assert report["transactional_commit"]["overwrite_allowed"] is False
    assert report["solver_candidate_ids_supplied_by_caller"] is False
    assert report["new_candidate_rediscovered"] is True
    assert report["fresh_engine_runs_unique"] is True
    assert report["control_gap_remained_failed_closed"] is True
    assert report["prior_candidate_state_unchanged"] is True
    assert report["prior_trial_state_unchanged"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_state_unchanged"] is True
    assert report["prior_runs_copied"] is False
    assert report["prior_episodes_copied"] is False
    assert report["prior_evidence_copied"] is False
    assert report["live_model_invocation_required"] is False
    assert "does not establish independent production evaluation or AGI" in report["claim_boundary"]

    evidence_path = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "cross-domain-seed-gap-acquisition"
        / "cross-domain-gap-"
    )
    matches = list(evidence_path.parent.glob("cross-domain-gap-*.json"))
    assert len(matches) == 1
    persisted = json.loads(matches[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["candidate_id"] == report["candidate_id"]
