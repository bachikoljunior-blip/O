from __future__ import annotations

import json
from pathlib import Path

from agi.autonomous_heterogeneous_goal_acquisition import (
    run_autonomous_heterogeneous_goal_acquisition,
)


def test_selects_one_fail_closed_heterogeneous_goal_learns_and_retains_it(
    runtime_repo: Path,
) -> None:
    report = run_autonomous_heterogeneous_goal_acquisition(
        runtime_repo,
        "autonomous-heterogeneous-goal-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "autonomous-heterogeneous-goal-acquisition-v1"
    assert set(report["goal_domains"]) == {
        "string->string",
        "sequence->numeric",
        "object->boolean",
    }
    assert report["initial_unsupported_count"] >= 2
    assert report["selected_goal"] in report["initial_unsupported_goals"]
    assert report["selected_from_unsupported_only"] is True
    assert report["selected_goal_negative_evidence_retained"] is True
    assert report["caller_supplied_candidate_ids_for_goal_classification"] is False
    assert report["caller_supplied_candidate_ids_for_post_acquisition_resolution"] is False
    assert report["post_acquisition_selected_candidate_ids"] == [
        report["selected_goal_candidate_id"]
    ]
    assert report["post_acquisition_selected_chain_depth"] == 1
    assert report["remaining_unsupported_count"] >= 1
    assert report["selected_goal"] not in report["remaining_unsupported_goals"]
    assert report["one_acquisition_did_not_claim_all_goals"] is True
    assert report["resolver_state_unchanged"] is True
    assert report["resolver_trials_unchanged"] is True
    assert report["source_state_unchanged"] is True
    assert report["source_trials_unchanged"] is True
    assert report["prior_runs_copied"] is False
    assert report["prior_episodes_copied"] is False
    assert report["prior_evidence_copied"] is False
    assert report["live_model_invocation_required"] is False

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "autonomous-heterogeneous-goal-acquisition"
    )
    files = list(evidence_dir.glob("goal-acquisition-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["selected_goal"] == report["selected_goal"]
    assert "do not establish AGI" in persisted["claim_boundary"]
