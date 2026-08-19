from __future__ import annotations

import json
from pathlib import Path

from agi.adaptive_depth_composition import run_adaptive_depth_composition
from agi.evidence_derived_target_synthesis import (
    _derived_target,
    _precommit_evidence_derived_schedule,
    _source_semantic_commitment,
    run_evidence_derived_target_synthesis,
)


def test_target_schedule_ignores_audit_only_storage_digest() -> None:
    source_a = {
        "target_schedule_commitment": "1" * 64,
        "acquisition_behavior_fingerprint": "2" * 64,
        "control_behavior_fingerprint": "3" * 64,
        "digest": "a" * 64,
        "transactional_commit": {"tree_digest": "b" * 64},
    }
    source_b = {
        **source_a,
        "digest": "c" * 64,
        "transactional_commit": {"tree_digest": "d" * 64},
    }
    semantic_a = _source_semantic_commitment(source_a)
    semantic_b = _source_semantic_commitment(source_b)
    assert semantic_a == semantic_b

    support_inputs = (-13, -3, 2, 11, 18)
    source_target = _derived_target(
        {
            "op": "add",
            "left": {"op": "input"},
            "right": {"op": "const", "domain": "numeric", "value": 1},
        },
        support_inputs,
    )
    schedule_a = _precommit_evidence_derived_schedule(
        source_target=source_target,
        source_semantic_commitment=semantic_a,
        source_schedule_signatures=set(),
        support_inputs=support_inputs,
    )
    schedule_b = _precommit_evidence_derived_schedule(
        source_target=source_target,
        source_semantic_commitment=semantic_b,
        source_schedule_signatures=set(),
        support_inputs=support_inputs,
    )
    assert schedule_a["schedule_commitment"] == schedule_b["schedule_commitment"]
    assert schedule_a["target_schedule"] == schedule_b["target_schedule"]


def test_persisted_failure_structure_generates_a_new_learning_target(runtime_repo: Path) -> None:
    prerequisite = run_adaptive_depth_composition(
        runtime_repo,
        "evidence-derived-target-synthesis-prerequisite",
    )
    assert prerequisite["passed"] is True

    report = run_evidence_derived_target_synthesis(
        runtime_repo,
        "evidence-derived-target-synthesis-test",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "evidence-derived-target-synthesis-v1"
    assert report["target_generation_source"] == "persisted_failure_structure"
    assert report["target_selection_uses_audit_storage_digest"] is False
    assert report["fresh_global_grammar_enumeration_used_for_derived_targets"] is False
    assert report["finite_source_schedule_target_replayed_as_new_objective"] is False
    assert report["derived_schedule_precommitted_before_support_checks"] is True
    assert report["derived_target_absent_from_source_schedule"] is True
    assert report["derived_target_failed_closed_before_learning"] is True
    assert report["derived_control_failed_closed_before_learning"] is True
    assert report["new_candidate_negative_evidence_retained"] is True
    assert report["transactional_commit"]["atomic_directory_rename"] is True
    assert report["transactional_commit"]["overwrite_allowed"] is False
    assert report["prior_candidate_state_unchanged"] is True
    assert report["prior_trial_state_unchanged"] is True
    assert report["new_candidate_rediscovered_without_caller_ids"] is True
    assert report["previous_evidence_selected_behavior_retained"] is True
    assert report["derived_control_gap_remained_failed_closed"] is True
    assert report["source_persisted_control_gap_remained_failed_closed"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_state_unchanged"] is True
    assert report["live_model_invocation_required"] is False

    assert len(report["support_attempts"]) >= 2
    assert report["support_attempts"][0]["candidate_ids_supplied_by_caller"] is False
    transfer = report["fresh_behavior_only_transfer"]
    assert transfer["solver_candidate_ids_supplied_by_caller"] is False
    assert report["new_candidate_id"] in transfer["solver_selected_candidate_ids"]
    assert transfer["fresh_engine_runs_unique"] is True
    assert all(
        item["expected_sha256"] == item["output_sha256"]
        for item in transfer["heldback_outputs"]
    )

    retained = report["previous_evidence_selected_behavior_retention"]
    assert retained["solver_candidate_ids_supplied_by_caller"] is False
    assert report["previous_evidence_selected_candidate_id"] in retained[
        "solver_selected_candidate_ids"
    ]
    assert retained["fresh_engine_runs_unique"] is True

    outputs = list(
        (
            runtime_repo
            / ".continual"
            / "evidence"
            / "evidence-derived-target-synthesis"
        ).glob("evidence-derived-*.json")
    )
    assert len(outputs) == 1
    persisted = json.loads(outputs[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["derived_target_absent_from_source_schedule"] is True
    assert "not open-domain autonomous objective invention" in persisted["claim_boundary"]
