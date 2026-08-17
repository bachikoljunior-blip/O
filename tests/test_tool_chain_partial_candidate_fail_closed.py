from __future__ import annotations

from pathlib import Path

from agi.acquired_programs import ProgramExample
from agi.behavior_guided_tool_chain_discovery import _chain_matches
from agi.heterogeneous_retention_campaign import _promote_task
from agi.materialized_runtime_replay import _candidate_trial_snapshot


def test_partial_verified_candidate_is_nonmatching_instead_of_crashing_planner(
    runtime_repo: Path,
) -> None:
    partial = _promote_task(
        runtime_repo,
        seed="partial-chain-planning-seed",
        spec={
            "name": "partial-object-threshold",
            "input_domain": "object",
            "output_domain": "boolean",
            "examples": (
                ProgramExample({"score": -2, "noise": 0}, False),
                ProgramExample({"score": 4, "noise": 0}, False),
                ProgramExample({"score": 5, "noise": 1}, True),
                ProgramExample({"score": 12, "noise": 1}, True),
            ),
            "probe": {"score": 11, "noise": 999},
            "expected": True,
            "max_nodes": 6,
        },
    )
    candidate_id = str(partial["candidate_id"])
    before = _candidate_trial_snapshot(runtime_repo, candidate_id)
    assert before

    # This Candidate is verified for its exact scope, but the demonstrations permit a simpler
    # learned dependency on `noise`. New task inputs intentionally omit that field. A behavior
    # planner must reject this partial route rather than abort all alternative-chain search.
    matches = _chain_matches(
        runtime_repo,
        (partial,),
        (
            ProgramExample({"score": -11}, False),
            ProgramExample({"score": 1}, True),
            ProgramExample({"score": 14}, False),
        ),
    )

    assert matches is False
    assert _candidate_trial_snapshot(runtime_repo, candidate_id) == before
    assert partial["negative_evidence_retained"] is True
