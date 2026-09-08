from __future__ import annotations

import json
from pathlib import Path

from agi.verified_macro_synthesis import run_generic_verified_macro_synthesis


def test_generic_synthesizer_reuses_recursive_verified_macro_and_regates_result(
    runtime_repo: Path,
) -> None:
    report = run_generic_verified_macro_synthesis(
        runtime_repo,
        "generic-verified-macro-synthesis-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "generic-verified-macro-synthesis"
    assert report["shortest_type_chain_depth"] == 1
    assert report["selected_chain_depth"] == 2
    assert len(report["selected_candidate_ids"]) == 2
    assert report["selected_candidate_ids"][1] == report["recursive_macro_candidate_id"]
    assert report["recursive_macro_candidate_id"] in report["source_candidate_ids"]
    assert report["generic_synthesis_trial_state_unchanged"] is True
    assert report["generic_synthesis_negative_evidence_retained"] is True
    assert report["compiled_program_nodes"] >= 5
    assert report["compiled_program_depth"] >= 4
    assert report["planning_examples_overlap_new_component_support"] is False
    assert report["baseline_before_compiled_regression_failed_closed"] is True
    assert report["adverse_compiled_regression_rejected"] is True
    assert report["compiled_candidate_promoted"] is True
    assert report["challenge_generated_after_compiled_promotion"] is True
    assert report["challenge_inputs_overlap_planning_or_support"] is False
    assert report["challenge_case_count"] == 3
    assert report["source_fresh_run_count"] == 9
    assert report["compiled_fresh_run_count"] == 9
    assert report["all_outputs_equal"] is True
    assert report["source_candidate_trial_state_unchanged"] is True
    assert report["compiled_candidate_trial_state_unchanged"] is True
    assert report["live_model_invocation_required"] is False

    evidence_dir = runtime_repo / ".continual" / "evidence" / "generic-verified-macro-synthesis"
    files = list(evidence_dir.glob("generic-macro-synthesis-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
