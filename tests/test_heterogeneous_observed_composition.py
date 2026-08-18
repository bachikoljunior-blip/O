from __future__ import annotations

import json
from pathlib import Path

from agi.heterogeneous_observed_composition import (
    run_heterogeneous_observed_composition,
)


def test_fresh_planner_selects_heterogeneous_composition_and_rejects_incompatible_chain(
    runtime_repo: Path,
) -> None:
    report = run_heterogeneous_observed_composition(
        runtime_repo,
        "heterogeneous-observed-composition-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "heterogeneous-observed-composition-v1"
    assert report["promoted_signature_count"] == 4
    assert len(set(report["promoted_candidate_ids"])) == 4
    assert report["caller_supplied_candidate_ids"] is False
    assert report["planner_selected_by_semantic_role_name"] is False
    assert report["observed_unique_target_count"] >= 1
    assert len(report["selected_candidate_ids"]) == 2
    assert report["selected_chain_depth"] == 2
    assert report["selected_distinct_stage_signature_count"] == 2
    assert len({tuple(value) for value in report["selected_stage_signatures"]}) == 2
    assert len(report["incompatible_pair_candidate_ids"]) == 2
    assert report["incompatible_pair_failed_closed"] is True
    assert report["challenge_generated_after_selection"] is True
    assert report["challenge_case_count"] == 2
    assert report["fresh_engine_runs_unique"] is True
    assert len(set(report["fresh_engine_runs"])) == 2
    assert all(item["output"] == item["expected"] for item in report["challenge_outputs"])
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
        / "heterogeneous-observed-composition"
    )
    files = list(evidence_dir.glob("composition-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
    assert "do not establish independent production evidence" in persisted["claim_boundary"]
