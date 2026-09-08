from __future__ import annotations

import json
from pathlib import Path

from agi.adaptive_depth_composition import run_adaptive_depth_composition
from agi.persisted_failure_curriculum import run_persisted_failure_curriculum


def test_persisted_failure_evidence_drives_next_curriculum_step(
    runtime_repo: Path,
) -> None:
    prerequisite = run_adaptive_depth_composition(
        runtime_repo,
        "persisted-failure-curriculum-prerequisite",
    )
    assert prerequisite["passed"] is True

    report = run_persisted_failure_curriculum(
        runtime_repo,
        "persisted-failure-curriculum-test",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "persisted-failure-curriculum-v1"
    assert report["target_selection_source"] == "persisted_fail_closed_control_evidence"
    assert report["fresh_target_enumeration_used_for_next_objective"] is False
    assert report["source_schedule_commitment_reconstructed"] is True
    assert report["persisted_control_gap_still_failed_closed_before_learning"] is True
    assert report["next_objective_precommitted_before_learning"] is True
    assert report["new_candidate_negative_evidence_retained"] is True
    assert report["transactional_commit"]["atomic_directory_rename"] is True
    assert report["transactional_commit"]["overwrite_allowed"] is False
    assert report["prior_candidate_state_unchanged"] is True
    assert report["prior_trial_state_unchanged"] is True
    assert report["solver_candidate_ids_supplied_by_caller"] is False
    assert report["evidence_selected_candidate_rediscovered"] is True
    assert report["fresh_transfer_runs_unique"] is True
    assert report["first_learned_behavior_retained_after_second_learning"] is True
    assert report["untouched_control_gap_remained_failed_closed"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_state_unchanged"] is True
    assert report["live_model_invocation_required"] is False

    transfer = report["fresh_behavior_only_transfer"]
    assert report["new_candidate_id"] in transfer["solver_selected_candidate_ids"]
    assert len(transfer["fresh_engine_run_ids"]) >= 2
    assert len(set(transfer["fresh_engine_run_ids"])) == len(
        transfer["fresh_engine_run_ids"]
    )
    assert all(
        item["expected_sha256"] == item["output_sha256"]
        for item in transfer["heldback_outputs"]
    )

    retained = report["first_learned_behavior_retention"]
    assert report["first_learned_candidate_id"] in retained["solver_selected_candidate_ids"]
    assert retained["solver_candidate_ids_supplied_by_caller"] is False
    assert retained["fresh_engine_runs_unique"] is True
    assert all(
        item["expected_sha256"] == item["output_sha256"]
        for item in retained["heldback_outputs"]
    )

    evidence_path = runtime_repo / report["source_evidence_path"]
    assert evidence_path.is_file()
    source_evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert source_evidence["digest"] == report["source_evidence_digest"]
    assert source_evidence["control_gap_remained_failed_closed"] is True

    outputs = list(
        (
            runtime_repo
            / ".continual"
            / "evidence"
            / "persisted-failure-curriculum"
        ).glob("curriculum-*.json")
    )
    assert len(outputs) == 1
    persisted = json.loads(outputs[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["target_selection_source"] == "persisted_fail_closed_control_evidence"
    assert "not open-domain autonomous task invention" in persisted["claim_boundary"]
