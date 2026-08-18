from __future__ import annotations

import json
from pathlib import Path

from agi.multi_gap_autonomous_curriculum import run_multi_gap_autonomous_curriculum


def test_two_fail_closed_gaps_are_learned_retained_and_composed_after_restart(
    runtime_repo: Path,
) -> None:
    report = run_multi_gap_autonomous_curriculum(
        runtime_repo,
        "multi-gap-autonomous-curriculum-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "multi-gap-autonomous-curriculum-v1"
    assert report["sequential_gap_count"] == 2
    assert report["first_gap_failed_closed_before_learning"] is True
    assert report["second_gap_failed_closed_before_learning"] is True
    assert report["first_acquired_candidate_id"] != report["second_acquired_candidate_id"]
    assert report["first_acquisition_preserved_during_second"] is True
    assert report["caller_supplied_candidate_ids_for_final_composition"] is False
    assert report["final_shortest_type_chain_depth"] == 1
    assert report["final_selected_chain_depth"] == 3
    assert report["both_new_skills_reused_in_final_composition"] is True
    assert report["older_retained_skill_reused_in_final_composition"] is True
    assert report["challenge_generated_after_final_selection"] is True
    assert report["fresh_engine_runs_unique"] is True
    assert len(set(report["fresh_engine_runs"])) == 2
    assert report["stage_invocation_count"] == 6
    assert report["fresh_state_unchanged"] is True
    assert report["fresh_trials_unchanged"] is True
    assert report["source_state_unchanged_after_final"] is True
    assert report["source_trials_unchanged_after_final"] is True
    assert report["base_state_still_present"] is True
    assert report["prior_runs_copied"] is False
    assert report["prior_episodes_copied"] is False
    assert report["prior_evidence_copied"] is False
    assert report["live_model_invocation_required"] is False

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "multi-gap-autonomous-curriculum"
    )
    files = list(evidence_dir.glob("curriculum-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
