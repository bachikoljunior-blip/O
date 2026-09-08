from __future__ import annotations

import json
from pathlib import Path

from agi.behavior_guided_tool_chain_discovery import (
    run_behavior_guided_tool_chain_discovery,
)


def test_behavior_guided_discovery_rejects_shorter_wrong_type_path_and_transfers(
    runtime_repo: Path,
) -> None:
    report = run_behavior_guided_tool_chain_discovery(
        runtime_repo,
        "behavior-guided-tool-chain-seed",
    )

    assert report["passed"] is True
    assert report["candidate_count"] == 4
    assert report["typed_chain_count"] >= 3
    assert report["shortest_type_chain_depth"] == 2
    assert report["selected_chain_depth"] == 3
    assert report["selected_chain_depth"] > report["shortest_type_chain_depth"]
    assert report["matching_chain_count"] >= 1
    assert report["same_depth_nonmatching_chain_count"] >= 1
    assert report["planning_example_count"] == 3
    assert report["planning_examples_overlap_synthesis_support"] is False
    assert report["challenge_generated_after_selection"] is True
    assert report["challenge_inputs_overlap_planning_or_support"] is False
    assert report["challenge_case_count"] == 3
    assert report["fresh_engine_chain_count"] == 9
    assert len(set(report["fresh_engine_runs"])) == 9
    assert report["stage_invocation_count"] == 27
    assert report["all_challenge_outputs_matched"] is True
    assert report["all_negative_evidence_retained"] is True
    assert report["candidate_trial_state_unchanged"] is True
    assert report["live_model_invocation_required"] is False

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "behavior-guided-tool-chain-discovery"
    )
    files = list(evidence_dir.glob("tool-chain-discovery-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
