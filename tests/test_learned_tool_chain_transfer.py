from __future__ import annotations

import json
from pathlib import Path

from agi.learned_tool_chain_transfer import run_learned_tool_chain_transfer


def test_three_stage_learned_tool_chain_transfers_after_promotion_without_trial_mutation(
    runtime_repo: Path,
) -> None:
    report = run_learned_tool_chain_transfer(
        runtime_repo,
        "learned-tool-chain-seed",
    )

    assert report["passed"] is True
    assert len(set(report["candidate_ids"])) == 3
    assert len(set(report["scopes"])) == 3
    assert report["chain_domains"] == [
        "numeric->numeric",
        "numeric->object",
        "object->boolean",
    ]
    assert report["challenge_generated_after_promotion"] is True
    assert report["challenge_inputs_overlap_synthesis_support"] is False
    assert report["challenge_case_count"] == 2
    assert report["fresh_engine_chain_count"] == 6
    assert len(set(report["fresh_engine_runs"])) == 6
    assert report["stage_invocation_count"] == 18
    assert report["all_chain_outputs_matched"] is True
    assert report["all_negative_evidence_retained"] is True
    assert report["candidate_trial_state_unchanged"] is True
    assert report["live_model_invocation_required"] is False

    evidence_dir = runtime_repo / ".continual" / "evidence" / "learned-tool-chain-transfer"
    files = list(evidence_dir.glob("learned-tool-chain-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
