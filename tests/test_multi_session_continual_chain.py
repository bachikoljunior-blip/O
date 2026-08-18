from __future__ import annotations

import json
from pathlib import Path

from agi.multi_session_continual_chain import run_multi_session_continual_chain


def test_two_new_skills_accumulate_across_successive_state_only_restarts(
    runtime_repo: Path,
) -> None:
    report = run_multi_session_continual_chain(
        runtime_repo,
        "multi-session-continual-chain-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "multi-session-continual-chain-v1"
    assert len(report["heterogeneous_candidate_ids"]) == 4
    assert len(report["recursive_component_candidate_ids"]) == 3
    assert report["session1_extension_candidate_id"] not in report["source_candidate_ids"]
    assert report["session2_extension_candidate_id"] not in report["source_candidate_ids"]
    assert report["session2_extension_candidate_id"] != report["session1_extension_candidate_id"]
    assert report["session1_negative_evidence_retained"] is True
    assert report["session2_negative_evidence_retained"] is True
    assert report["source_unchanged_by_first_extension"] is True
    assert report["source_trials_unchanged_by_first_extension"] is True
    assert report["session2_copy_byte_identical"] is True
    assert report["session2_trials_byte_identical"] is True
    assert report["session1_state_unchanged_by_second_extension"] is True
    assert report["session1_trials_unchanged_by_second_extension"] is True
    assert report["session3_copy_byte_identical"] is True
    assert report["session3_trials_byte_identical"] is True
    assert report["session3_state_unchanged_after_replay"] is True
    assert report["session3_trials_unchanged_after_replay"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_ledgers_unchanged"] is True
    assert report["state_only_restart_count"] == 3
    assert report["new_skill_count"] == 2
    assert report["fresh_engine_run_count"] == 14
    assert len(set(report["fresh_engine_runs"])) == 14
    assert report["prior_runs_copied"] is False
    assert report["prior_episodes_copied"] is False
    assert report["prior_evidence_copied"] is False
    assert report["live_model_invocation_required"] is False

    evidence_dir = runtime_repo / ".continual" / "evidence" / "multi-session-continual-chain"
    files = list(evidence_dir.glob("chain-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
