from __future__ import annotations

import json
from pathlib import Path

from agi.resumable_multi_gap_curriculum import run_resumable_multi_gap_curriculum


PHASES = [
    "transactional_base",
    "square_gap",
    "string_gap",
    "final_composition",
]


def test_multi_gap_curriculum_resumes_across_phase_checkpoints_without_replay(
    runtime_repo: Path,
) -> None:
    campaign_id = "resumable-multigap-curriculum-01"
    seed = "resumable-multigap-curriculum-seed"

    snapshots: list[dict] = []
    for expected_count in range(1, 5):
        state = run_resumable_multi_gap_curriculum(
            runtime_repo,
            campaign_id,
            seed=seed,
            max_new_phases=1,
        )
        snapshots.append(state)
        assert state["completed_count"] == expected_count
        assert state["completed_phases"] == PHASES[:expected_count]
        assert state["last_advance_count"] == 1
        assert state["phase_level_resumability"] is True
        assert state["running_phase_retries_fail_closed"] is True
        if expected_count < 4:
            assert state["passed"] is False
            assert state["status"] == "in_progress"
        else:
            assert state["passed"] is True
            assert state["status"] == "completed"

    first_phase_digest = snapshots[0]["phases"]["transactional_base"]["result_digest"]
    first_phase_attempts = snapshots[0]["phases"]["transactional_base"]["attempts"]
    assert first_phase_attempts == 1
    for state in snapshots[1:]:
        assert state["phases"]["transactional_base"]["result_digest"] == first_phase_digest
        assert state["phases"]["transactional_base"]["attempts"] == first_phase_attempts

    final = snapshots[-1]
    square = final["phases"]["square_gap"]
    string = final["phases"]["string_gap"]
    composition = final["phases"]["final_composition"]
    assert square["metadata"]["gap_failed_closed_before_learning"] is True
    assert square["metadata"]["negative_evidence_retained"] is True
    assert string["metadata"]["gap_failed_closed_before_learning"] is True
    assert string["metadata"]["negative_evidence_retained"] is True
    assert square["metadata"]["candidate_id"] != string["metadata"]["candidate_id"]
    assert composition["metadata"]["challenge_case_count"] == 2
    assert len(composition["metadata"]["selected_candidate_ids"]) == 3

    state_path = (
        runtime_repo
        / ".continual"
        / "system"
        / "multi-gap-curricula"
        / f"{campaign_id}.json"
    )
    state_text = state_path.read_text(encoding="utf-8")
    persisted = json.loads(state_text)
    assert persisted["digest"] == final["digest"]
    assert seed not in state_text
    assert "Internal deterministic long-horizon continual-learning evidence only" in persisted[
        "claim_boundary"
    ]

    evidence_path = runtime_repo / composition["metadata"]["evidence_path"]
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["passed"] is True
    assert evidence["campaign_kind"] == "resumable-multi-gap-autonomous-curriculum-v1"
    assert evidence["caller_supplied_candidate_ids"] is False
    assert evidence["shortest_type_chain_depth"] == 1
    assert evidence["selected_chain_depth"] == 3
    assert evidence["fresh_engine_runs_unique"] is True
    assert evidence["stage_invocation_count"] == 6
    assert evidence["fresh_state_unchanged"] is True
    assert evidence["fresh_trials_unchanged"] is True
    assert evidence["source_state_unchanged"] is True
    assert evidence["source_trials_unchanged"] is True
    assert evidence["prior_runs_copied"] is False
    assert evidence["prior_episodes_copied"] is False
    assert evidence["prior_evidence_copied"] is False
    assert evidence["live_model_invocation_required"] is False

    replay = run_resumable_multi_gap_curriculum(
        runtime_repo,
        campaign_id,
        seed=seed,
    )
    assert replay["passed"] is True
    assert replay["last_advance_count"] == 0
    assert replay["resume_skipped_completed"] == 4
    assert replay["completed_phases"] == PHASES
    assert replay["phases"] == final["phases"]
