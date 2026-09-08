from __future__ import annotations

import json
from pathlib import Path

from agi.sequence_construction_transfer import run_sequence_construction_transfer


def test_sequence_construction_is_promoted_and_transfers_without_trial_mutation(
    runtime_repo: Path,
) -> None:
    report = run_sequence_construction_transfer(
        runtime_repo,
        "sequence-construction-seed",
    )

    assert report["passed"] is True
    assert report["expression"] == {
        "op": "concat_sequence",
        "left": {"op": "input"},
        "right": {"op": "input"},
    }
    assert report["insufficient_two_node_search_failed"] is True
    assert report["negative_evidence_retained"] is True
    assert report["challenge_generated_after_promotion"] is True
    assert report["challenge_inputs_overlap_synthesis_support"] is False
    assert report["challenge_case_count"] == 3
    assert report["fresh_engine_run_count"] == 9
    assert len(set(report["fresh_engine_runs"])) == 9
    assert report["invocation_count"] == 9
    assert report["all_outputs_matched"] is True
    assert report["candidate_trial_state_unchanged"] is True
    assert report["live_model_invocation_required"] is False

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "sequence-construction-transfer"
    )
    files = list(evidence_dir.glob("sequence-construction-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
