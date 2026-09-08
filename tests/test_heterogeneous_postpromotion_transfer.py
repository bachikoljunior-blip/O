from __future__ import annotations

import json
from pathlib import Path

from agi.heterogeneous_postpromotion_transfer import (
    run_heterogeneous_postpromotion_transfer,
)


def test_postpromotion_challenges_transfer_without_support_overlap_or_trial_mutation(
    runtime_repo: Path,
) -> None:
    report = run_heterogeneous_postpromotion_transfer(
        runtime_repo,
        "heterogeneous-postpromotion-seed",
    )

    assert report["passed"] is True
    assert report["challenge_generated_after_promotion"] is True
    assert report["challenge_inputs_overlap_synthesis_support"] is False
    assert report["capability_count"] == 4
    assert report["challenge_case_count"] == 8
    assert report["fresh_engine_count"] == 3
    assert len(set(report["fresh_engine_runs"])) == 3
    assert report["replay_invocation_count"] == 24
    assert report["input_domains"] == ["numeric", "string", "sequence", "object"]
    assert report["candidate_trial_state_unchanged"] is True
    assert report["all_fresh_engine_replays_matched"] is True
    assert report["live_model_invocation_required"] is False
    assert len(report["challenge_refs"]) == 4
    assert all(len(item["probe_sha256"]) == 2 for item in report["challenge_refs"])

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "heterogeneous-postpromotion-transfer"
    )
    files = list(evidence_dir.glob("heterogeneous-postpromotion-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
