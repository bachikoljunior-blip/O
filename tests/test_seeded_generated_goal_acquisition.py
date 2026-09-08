from __future__ import annotations

import json
from pathlib import Path

from agi.seeded_generated_goal_acquisition import (
    _generated_goal_specs,
    run_seeded_generated_goal_acquisition,
)


def test_generated_goal_instances_are_seed_bound_and_one_gap_is_retained(
    runtime_repo: Path,
) -> None:
    seed = "seeded-generated-goal-acquisition-seed"
    specs = _generated_goal_specs(seed)
    other_specs = _generated_goal_specs(seed + "-other")
    assert [spec["name"] for spec in specs] != [spec["name"] for spec in other_specs]

    report = run_seeded_generated_goal_acquisition(runtime_repo, seed)

    assert report["passed"] is True
    assert report["campaign_kind"] == "seeded-generated-goal-acquisition-v1"
    assert report["generated_goal_count"] == 5
    assert len(set(report["generated_goal_names"])) == 5
    assert len(set(report["generated_goal_domains"])) >= 3
    assert report["initial_unsupported_count"] >= 2
    assert report["selected_goal"] in report["initial_unsupported_goals"]
    assert report["selected_from_unsupported_only"] is True
    assert report["selected_negative_evidence_retained"] is True
    assert report["caller_supplied_candidate_ids"] is False
    assert report["selected_candidate_id"] in report[
        "post_acquisition_selected_candidate_ids"
    ]
    assert report["remaining_unsupported_count"] >= 1
    assert report["selected_goal"] not in report["remaining_unsupported_goals"]
    assert report["fresh_engine_run"]
    assert report["prior_state_unchanged"] is True
    assert report["prior_trials_unchanged"] is True
    assert report["all_state_unchanged_after_fresh_replay"] is True
    assert report["all_trials_unchanged_after_fresh_replay"] is True
    assert report["live_model_invocation_required"] is False

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "seeded-generated-goal-acquisition"
    )
    files = list(evidence_dir.glob("generated-goal-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["generated_spec_digest"] == report["generated_spec_digest"]
    assert "does not establish AGI" in persisted["claim_boundary"]
