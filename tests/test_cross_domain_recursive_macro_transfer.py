from __future__ import annotations

import json
from pathlib import Path

from agi.cross_domain_recursive_macro_transfer import run_cross_domain_recursive_macro_transfer


def test_recursive_compiled_macro_transfers_through_new_structured_domain(
    runtime_repo: Path,
) -> None:
    report = run_cross_domain_recursive_macro_transfer(
        runtime_repo,
        "cross-domain-recursive-macro-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "cross-domain-recursive-macro-transfer"
    assert report["shortest_type_chain_depth"] == 1
    assert report["selected_chain_depth"] == 2
    assert len(report["selected_chain_candidate_ids"]) == 2
    assert report["shorter_behavioral_distractor_candidate_id"] in report["component_candidate_ids"]
    assert report["recursive_macro_reused_cross_domain"] is True
    assert report["compiled_input_domain"] == "object"
    assert report["compiled_output_domain"] == "boolean"
    assert report["compiled_program_nodes"] > report["recursive_macro_program_nodes"]
    assert report["compiled_program_depth"] >= 4
    assert report["planning_examples_overlap_component_support"] is False
    assert report["baseline_before_compiled_regression_failed_closed"] is True
    assert report["adverse_compiled_regression_rejected"] is True
    assert report["compiled_candidate_promoted"] is True
    assert report["challenge_generated_after_compiled_promotion"] is True
    assert report["challenge_inputs_overlap_planning_or_support"] is False
    assert report["challenge_case_count"] == 3
    assert report["source_fresh_run_count"] == 9
    assert report["compiled_fresh_run_count"] == 9
    assert report["all_outputs_equal"] is True
    assert report["source_component_trial_state_unchanged"] is True
    assert report["compiled_candidate_trial_state_unchanged"] is True
    assert report["all_source_negative_evidence_retained"] is True
    assert report["live_model_invocation_required"] is False

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "cross-domain-recursive-macro-transfer"
    )
    files = list(evidence_dir.glob("cross-domain-recursive-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
