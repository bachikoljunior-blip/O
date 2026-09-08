from __future__ import annotations

import json
from pathlib import Path

from agi.acquired_skill_composition import run_acquired_skill_composition


def test_failure_driven_acquired_skill_is_reused_in_new_composition_after_restart(
    runtime_repo: Path,
) -> None:
    report = run_acquired_skill_composition(
        runtime_repo,
        "acquired-skill-composition-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "failure-driven-acquired-skill-composition-v1"
    assert report["caller_supplied_candidate_ids"] is False
    assert report["shortest_type_chain_depth"] == 1
    assert report["selected_chain_depth"] == 2
    assert report["selected_candidate_ids"] == [
        report["newly_acquired_square_candidate_id"],
        report["retained_negation_candidate_id"],
    ]
    assert report["newly_acquired_skill_reused_in_composition"] is True
    assert report["shorter_type_correct_routes_rejected_by_behavior"] is True
    assert report["challenge_generated_after_selection"] is True
    assert report["challenge_case_count"] == 2
    assert report["fresh_engine_runs_unique"] is True
    assert len(set(report["fresh_engine_runs"])) == 2
    assert report["stage_invocation_count"] == 4
    assert report["fresh_state_unchanged"] is True
    assert report["fresh_trials_unchanged"] is True
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
        / "acquired-skill-composition"
    )
    files = list(evidence_dir.glob("composition-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
