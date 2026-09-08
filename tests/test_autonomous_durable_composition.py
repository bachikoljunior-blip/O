from __future__ import annotations

import json
from pathlib import Path

from agi.autonomous_durable_composition import run_autonomous_durable_composition


def test_fresh_restart_composes_two_durable_skills_without_candidate_ids(
    runtime_repo: Path,
) -> None:
    report = run_autonomous_durable_composition(
        runtime_repo,
        "autonomous-durable-composition-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "autonomous-durable-library-composition-v1"
    assert report["caller_supplied_candidate_ids"] is False
    assert report["shortest_type_chain_depth"] == 1
    assert report["selected_chain_depth"] == 2
    assert len(report["selected_candidate_ids"]) == 2
    assert report["selected_candidate_ids"][0] == report["selected_absolute_candidate_id"]
    assert report["selected_candidate_ids"][1] == report["selected_newly_committed_negation_id"]
    assert report["shorter_type_correct_routes_rejected_by_behavior"] is True
    assert report["challenge_generated_after_selection"] is True
    assert report["challenge_case_count"] == 2
    assert report["fresh_engine_runs_unique"] is True
    assert len(set(report["fresh_engine_runs"])) == 2
    assert report["stage_invocation_count"] == 4
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
        / "autonomous-durable-composition"
    )
    files = list(evidence_dir.glob("composition-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
