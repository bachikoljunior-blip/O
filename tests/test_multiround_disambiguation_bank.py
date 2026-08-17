from __future__ import annotations

import json
from pathlib import Path

from agi.multiround_disambiguation_bank import run_multiround_disambiguation_bank_campaign


def test_two_round_counterexamples_are_accumulated_and_reused_without_later_oracle(
    runtime_repo: Path,
) -> None:
    report = run_multiround_disambiguation_bank_campaign(
        runtime_repo,
        "multiround-disambiguation-bank-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "multi-round-persistent-active-disambiguation"
    assert report["fresh_candidate_identities"] is True
    assert report["initial_matching_route_count"] == 3
    assert report["round_route_counts"] == [3, 2, 1]
    assert report["first_oracle_query_count"] == 2
    assert report["probe_count"] == 2
    assert len(report["probe_commitments"]) == 2
    assert report["bank_entry_count"] == 2
    assert report["later_initial_matching_route_count"] == 3
    assert report["later_oracle_query_count"] == 0
    assert report["later_reused_counterexample_count"] == 2
    assert report["first_selected_depth"] == 1
    assert report["later_selected_depth"] == 1
    assert report["first_challenge_outputs"] == [False, True, False, True, False]
    assert report["later_challenge_outputs"] == [False, True, False, True, False]
    assert report["counterexample_bank_unchanged_after_reuse"] is True
    assert report["first_candidate_trial_state_unchanged"] is True
    assert report["later_candidate_trial_state_unchanged"] is True
    assert report["candidate_trial_state_unchanged"] is True

    banks = list(
        (
            runtime_repo
            / ".continual"
            / "evidence"
            / "verified-macro-counterexample-bank"
        ).glob("bank-*.json")
    )
    assert len(banks) == 1
    bank = json.loads(banks[0].read_text(encoding="utf-8"))
    assert bank["kind"] == "verified-macro-counterexample-bank-v1"
    assert bank["task_key"] == "object-has-key-a-multiround-v1"
    assert len(bank["entries"]) == 2
    assert bank["candidate_trial_state_mutated"] is False
    assert bank["bank_sha256"] == report["bank_sha256"]
    assert all(entry["answer_received_after_probe_commitment"] for entry in bank["entries"])

    evidence = list(
        (
            runtime_repo
            / ".continual"
            / "evidence"
            / "multiround-disambiguation-bank"
        ).glob("multiround-disambiguation-*.json")
    )
    assert len(evidence) == 1
    persisted = json.loads(evidence[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
