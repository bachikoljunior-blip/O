from __future__ import annotations

import json
from pathlib import Path

from agi.observed_three_stage_composition import run_observed_three_stage_composition


def test_fresh_resolver_generates_unique_three_stage_target_beyond_shorter_routes(
    runtime_repo: Path,
) -> None:
    report = run_observed_three_stage_composition(
        runtime_repo,
        "observed-three-stage-composition-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "observed-three-stage-composition-v1"
    assert report["caller_supplied_candidate_ids"] is False
    assert report["planner_selected_by_semantic_role_name"] is False
    assert report["typed_chain_count_through_depth_three"] >= 3
    assert report["eligible_unique_three_stage_behavior_count"] >= 1
    assert len(report["hidden_observed_chain"]) == 3
    assert report["selected_candidate_ids"] == report["hidden_observed_chain"]
    assert report["selected_chain_depth"] == 3
    assert report["shortest_type_chain_depth"] < 3
    assert report["shorter_type_correct_route_depth"] < 3
    assert report["shorter_routes_rejected_by_behavior"] is True
    assert report["selected_distinct_stage_signature_count"] >= 3
    assert len({tuple(value) for value in report["selected_stage_signatures"]}) >= 3
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
        / "observed-three-stage-composition"
    )
    files = list(evidence_dir.glob("composition-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
    assert "do not establish independent production" in persisted["claim_boundary"]
