from __future__ import annotations

import json
from pathlib import Path

from agi.observed_capability_composition import run_observed_capability_composition


def test_fresh_planner_generates_composed_target_from_observed_library_without_role_names(
    runtime_repo: Path,
) -> None:
    report = run_observed_capability_composition(
        runtime_repo,
        "observed-capability-composition-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "observed-capability-composition-v2"
    assert report["prerequisite_seed_search_attempts"] >= 2
    assert report["rejected_prerequisite_seed_count"] >= 1
    assert report["rejected_prerequisite_seeds"]
    first_rejected = report["rejected_prerequisite_seeds"][0]
    assert first_rejected["seed"] == "observed-capability-composition-seed:capability-base"
    assert first_rejected["unsupported_parameters"]["offset"] == 6
    assert "outside" in first_rejected["reason"]
    assert report["caller_supplied_candidate_ids"] is False
    assert report["caller_named_capability_roles"] is False
    assert report["planner_used_candidate_names"] is False
    assert report["numeric_library_candidate_count"] >= 3
    assert report["observed_single_behavior_count"] >= 2
    assert report["observed_unique_composed_behavior_count"] >= 1
    assert len(report["hidden_observed_pair"]) == 2
    assert report["selected_candidate_ids"] == report["hidden_observed_pair"]
    assert report["shortest_type_chain_depth"] == 1
    assert report["selected_chain_depth"] == 2
    assert report["one_stage_routes_rejected_by_behavior"] is True
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
        / "observed-capability-composition"
    )
    files = list(evidence_dir.glob("composition-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
    assert "do not establish independent production evidence" in persisted["claim_boundary"]
