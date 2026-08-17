from __future__ import annotations

import json
from pathlib import Path

from agi.learned_tool_chain_compilation import run_learned_tool_chain_compilation


def test_behavior_selected_chain_compiles_to_one_regression_gated_reusable_tool(
    runtime_repo: Path,
) -> None:
    report = run_learned_tool_chain_compilation(
        runtime_repo,
        "compiled-learned-tool-chain-seed",
    )

    assert report["passed"] is True
    assert report["component_candidate_count"] == 4
    assert len(report["selected_chain_candidate_ids"]) == 3
    assert report["source_stage_count"] == 3
    assert report["compiled_stage_count"] == 1
    assert report["compiled_program_nodes"] >= 5
    assert report["compiled_program_depth"] >= 3
    assert len(report["compiled_support_sha256"]) == 64
    assert report["planning_examples_overlap_component_support"] is False
    assert report["baseline_before_compiled_regression_failed_closed"] is True
    assert report["adverse_compiled_regression_rejected"] is True
    assert report["compiled_candidate_promoted"] is True
    assert report["challenge_generated_after_compiled_promotion"] is True
    assert report["challenge_inputs_overlap_planning_or_support"] is False
    assert report["challenge_case_count"] == 3
    assert report["chain_fresh_run_count"] == 9
    assert report["compiled_fresh_run_count"] == 9
    assert report["all_outputs_equal"] is True
    assert report["all_component_negative_evidence_retained"] is True
    assert report["component_candidate_trial_state_unchanged"] is True
    assert report["compiled_candidate_trial_state_unchanged"] is True
    assert report["live_model_invocation_required"] is False

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "learned-tool-chain-compilation"
    )
    files = list(evidence_dir.glob("compiled-plan-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
