from __future__ import annotations

import json
from pathlib import Path

from agi.task_conditioned_tool_chain_switching import (
    run_task_conditioned_tool_chain_switching,
)


def test_same_learned_library_switches_between_distinct_behavior_selected_plans(
    runtime_repo: Path,
) -> None:
    report = run_task_conditioned_tool_chain_switching(
        runtime_repo,
        "task-conditioned-switching-seed",
    )

    assert report["passed"] is True
    assert report["candidate_count"] == 4
    assert report["typed_chain_count"] >= 3
    assert report["plans_are_distinct"] is True
    assert len(report["absolute_plan"]) == 3
    assert len(report["negative_plan"]) == 3
    assert report["absolute_plan"][0] != report["negative_plan"][0]
    assert report["absolute_plan"][1:] == report["negative_plan"][1:]
    assert report["shared_tail_stage_count"] == 2
    assert report["planning_example_count_per_goal"] == 3
    assert report["planning_examples_overlap_synthesis_support"] is False
    assert report["challenge_generated_after_both_plan_selections"] is True
    assert report["challenge_inputs_overlap_planning_or_support"] is False
    assert report["challenge_case_count"] == 3
    assert report["goal_count"] == 2
    assert report["fresh_engine_run_count"] == 18
    assert report["fresh_engine_runs_unique"] is True
    assert report["stage_invocation_count"] == 54
    assert report["all_outputs_matched"] is True
    assert report["all_negative_evidence_retained"] is True
    assert report["candidate_trial_state_unchanged"] is True
    assert report["live_model_invocation_required"] is False

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "task-conditioned-tool-chain-switching"
    )
    files = list(evidence_dir.glob("task-conditioned-switching-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
