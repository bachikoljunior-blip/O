from __future__ import annotations

from agi.structured_acquired_program_cegis import run_structured_acquired_program_cegis


def test_structured_cegis_rejects_spurious_key_presence_and_refines_without_final_answer_leakage(
    runtime_repo,
) -> None:
    report = run_structured_acquired_program_cegis(
        runtime_repo,
        "structured-cegis-20260817",
    )

    assert report["passed"] is True
    assert report["commitment_verified"] is True
    assert report["initial_hypothesis"] == {
        "op": "object_has_key",
        "arg": {"op": "input"},
        "key": "approved",
    }
    assert report["initial_hypothesis_was_wrong"] is True
    assert report["counterexample_count"] >= 1
    assert report["final_answers_exposed_to_learner"] is False
    assert report["baseline_score"] == 0.0
    assert report["candidate_score"] == 1.0
    assert set(report["target_repeats"].values()) == {4}
    assert report["protected_prior_score_before"] == 1.0
    assert report["protected_prior_score_during"] == 1.0
    assert report["protected_prior_score_after"] == 1.0
    assert report["promotion_adopted"] is True
    assert report["fresh_engine_runtime_score"] == 1.0
    assert report["candidate_trial_state_unchanged"] is True
    assert report["failed_hypothesis_evidence_retained"] is True
    assert "independently operated production evidence" in report["claim_boundary"]
