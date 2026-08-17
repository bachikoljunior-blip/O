from __future__ import annotations

import json
from pathlib import Path

from agi.persistent_disambiguation_memory import run_persistent_disambiguation_memory_campaign


def test_disambiguation_counterexample_persists_and_resolves_later_fresh_candidates_without_oracle(
    runtime_repo: Path,
) -> None:
    report = run_persistent_disambiguation_memory_campaign(
        runtime_repo,
        "persistent-disambiguation-memory-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "persistent-verified-macro-disambiguation-memory"
    assert report["fresh_candidate_identities"] is True
    assert report["first_initial_matching_route_count"] == 2
    assert report["later_initial_matching_route_count"] == 2
    assert report["first_oracle_query_count"] == 1
    assert report["later_oracle_query_count"] == 0
    assert report["memory_reused_without_new_oracle_query"] is True
    assert report["memory_file_unchanged_after_reuse"] is True
    assert report["remembered_probe_distinct_route_outcome_count"] == 2
    assert report["remembered_probe_verified_rejection_count"] == 1
    assert len(report["later_selected_candidate_ids"]) == 1
    assert report["later_selected_depth"] == 1
    assert report["first_challenge_outputs"] == [False, True, True, False]
    assert report["later_challenge_outputs"] == [False, True, True, False]
    assert report["first_candidate_trial_state_unchanged"] is True
    assert report["later_candidate_trial_state_unchanged"] is True
    assert report["candidate_trial_state_unchanged"] is True

    memories = list(
        (
            runtime_repo
            / ".continual"
            / "evidence"
            / "verified-macro-disambiguation-memory"
        ).glob("memory-*.json")
    )
    assert len(memories) == 1
    memory = json.loads(memories[0].read_text(encoding="utf-8"))
    assert memory["kind"] == "verified-macro-counterexample-memory-v1"
    assert memory["task_key"] == "object-has-flag-presence-v1"
    assert memory["counterexample"]["output"] is False
    assert memory["answer_received_after_probe_commitment"] is True
    assert memory["candidate_trial_state_mutated"] is False
    assert memory["memory_sha256"] == report["memory_sha256"]

    evidence = list(
        (
            runtime_repo
            / ".continual"
            / "evidence"
            / "persistent-disambiguation-memory"
        ).glob("persistent-disambiguation-*.json")
    )
    assert len(evidence) == 1
    persisted = json.loads(evidence[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
