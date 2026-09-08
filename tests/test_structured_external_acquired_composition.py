from __future__ import annotations

from agi.structured_external_acquired_composition import (
    run_structured_external_acquired_composition,
)


def test_structured_external_output_composes_into_newly_acquired_object_program(runtime_repo) -> None:
    report = run_structured_external_acquired_composition(
        runtime_repo,
        "structured-external-composition-20260817",
    )

    assert report["passed"] is True
    assert report["domain_chain"] == ["numeric", "object", "boolean"]
    assert len(set(report["fresh_engine_runs"])) == 2
    assert report["fresh_engine_composition_matched"] is True
    assert report["candidate_trial_state_unchanged"] is True
    assert report["external_negative_evidence_retained"] is True
    assert report["learned_negative_evidence_retained"] is True
    assert set(report["learned_target_repeats"].values()) == {2}
    assert report["decisions"] == [False, True]
    assert report["live_model_invocation_required"] is False
    assert "do not prove" in report["claim_boundary"]
