from __future__ import annotations

import json
from pathlib import Path

from agi.recursive_skill_chunking import run_recursive_skill_chunking


def test_verified_compiled_skill_can_be_reused_and_chunked_again(
    runtime_repo: Path,
) -> None:
    report = run_recursive_skill_chunking(
        runtime_repo,
        "recursive-compiled-skill-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "recursive-verified-skill-chunking"
    assert report["shortest_type_chain_depth"] == 1
    assert report["selected_chain_depth"] == 2
    assert len(report["selected_chain_candidate_ids"]) == 2
    assert report["selected_chain_candidate_ids"][0] == report["generation_one_candidate_id"]
    assert report["first_generation_macro_reused_as_stage"] is True
    assert report["generation_one_program_nodes"] >= 5
    assert report["second_generation_program_nodes"] > report["generation_one_program_nodes"]
    assert report["second_generation_program_depth"] >= 4
    assert report["planning_examples_overlap_generation_one_support"] is False
    assert report["baseline_before_second_regression_failed_closed"] is True
    assert report["adverse_second_regression_rejected"] is True
    assert report["second_generation_candidate_promoted"] is True
    assert report["challenge_generated_after_second_promotion"] is True
    assert report["challenge_inputs_overlap_planning_or_earlier_support"] is False
    assert report["challenge_case_count"] == 3
    assert report["source_fresh_run_count"] == 9
    assert report["compiled_fresh_run_count"] == 9
    assert report["all_outputs_equal"] is True
    assert report["source_component_trial_state_unchanged"] is True
    assert report["second_generation_trial_state_unchanged"] is True
    assert report["source_negative_evidence_retained"] is True
    assert report["live_model_invocation_required"] is False

    evidence_dir = runtime_repo / ".continual" / "evidence" / "recursive-skill-chunking"
    files = list(evidence_dir.glob("recursive-skill-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
