from __future__ import annotations

import json
from pathlib import Path

from agi.generated_parameter_composition_transfer import (
    run_generated_parameter_composition_transfer,
)


def test_fresh_restart_composes_seed_generated_scale_and_offset_without_candidate_ids(
    runtime_repo: Path,
) -> None:
    report = run_generated_parameter_composition_transfer(
        runtime_repo,
        "generated-parameter-composition-transfer-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "generated-parameter-composition-transfer-v1"
    assert report["caller_supplied_candidate_ids"] is False
    assert report["scale"] >= 2
    assert report["offset"] >= 2
    assert report["distractor_scale"] != report["scale"]
    assert report["shortest_type_chain_depth"] == 1
    assert report["selected_chain_depth"] == 2
    assert report["selected_candidate_ids"] == [
        report["scale_candidate_id"],
        report["offset_candidate_id"],
    ]
    assert report["distractor_candidate_id"] not in report["selected_candidate_ids"]
    assert report["one_stage_type_correct_routes_rejected_by_behavior"] is True
    assert report["challenge_generated_after_selection"] is True
    assert report["challenge_case_count"] == 2
    assert report["fresh_engine_runs_unique"] is True
    assert len(set(report["fresh_engine_runs"])) == 2
    assert report["all_outputs_matched"] is True
    assert report["fresh_candidate_state_unchanged"] is True
    assert report["fresh_trial_state_unchanged"] is True
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
        / "generated-parameter-composition-transfer"
    )
    files = list(evidence_dir.glob("composition-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
    assert "do not establish" in persisted["claim_boundary"]
