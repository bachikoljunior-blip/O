from __future__ import annotations

import json
from pathlib import Path

from agi.autonomous_durable_library_retrieval import (
    run_autonomous_durable_library_retrieval,
)


def test_fresh_restart_discovers_and_selects_new_durable_skills_without_ids(
    runtime_repo: Path,
) -> None:
    report = run_autonomous_durable_library_retrieval(
        runtime_repo,
        "autonomous-durable-library-retrieval-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "autonomous-durable-library-retrieval-v1"
    assert report["caller_supplied_candidate_ids"] is False
    assert report["library_contains_committed_lower"] is True
    assert report["library_contains_committed_negation"] is True
    assert report["lower_selected_chain_depth"] == 1
    assert report["negation_selected_chain_depth"] == 1
    assert report["unsupported_behavior_failed_closed"] is True
    assert report["fresh_engine_runs_unique"] is True
    assert len(set(report["fresh_engine_runs"])) == 2
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
        / "autonomous-durable-library-retrieval"
    )
    files = list(evidence_dir.glob("retrieval-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
