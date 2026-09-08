from __future__ import annotations

import json
from pathlib import Path

from agi.adaptive_depth_multiseed_replay import run_adaptive_depth_multiseed_replay


def test_multi_seed_adaptive_routes_survive_another_state_only_session(
    runtime_repo: Path,
) -> None:
    report = run_adaptive_depth_multiseed_replay(
        runtime_repo,
        "adaptive-depth-multiseed-replay-seed",
        max_seed_attempts=4,
        required_distinct_behaviors=2,
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "adaptive-depth-multiseed-later-session-replay-v1"
    assert 2 <= report["seed_attempt_count"] <= report["max_seed_attempts"] == 4
    assert len(report["seed_schedule"]) == 4
    assert report["required_distinct_behaviors"] == 2
    assert report["distinct_behavior_fingerprint_count"] >= 2
    assert report["distinct_route_identity_count"] >= 2
    assert report["multi_seed_behavior_diversity_observed"] is True
    assert report["all_routes_minimal_depth_retained"] is True
    assert report["all_route_identities_retained"] is True
    assert report["all_unsupported_gaps_failed_closed"] is True
    assert report["all_fresh_engine_runs_unique"] is True
    assert report["all_candidate_state_unchanged"] is True
    assert report["all_trial_state_unchanged"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_state_unchanged"] is True
    assert report["live_model_invocation_required"] is False

    assert len(report["seed_records"]) == report["seed_attempt_count"]
    fingerprints = {item["behavior_fingerprint"] for item in report["seed_records"]}
    assert len(fingerprints) == report["distinct_behavior_fingerprint_count"]
    for record in report["seed_records"]:
        assert record["caller_supplied_candidate_ids"] is False
        assert record["selected_candidate_ids"] == record["replay_selected_candidate_ids"]
        assert record["selected_chain_depth"] == record["replay_selected_chain_depth"]
        assert record["minimal_depth_retained"] is True
        assert record["route_identity_retained"] is True
        assert record["unsupported_gap_failed_closed"] is True
        assert record["fresh_engine_runs_unique"] is True
        assert len(set(record["fresh_engine_runs"])) == 3
        assert all(
            item["output"] == item["expected"]
            for item in record["heldback_challenge_outputs"]
        )
        assert record["candidate_state_unchanged"] is True
        assert record["trial_state_unchanged"] is True
        assert record["prior_runs_copied"] is False
        assert record["prior_episodes_copied"] is False
        assert record["prior_evidence_copied"] is False

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "adaptive-depth-multiseed-replay"
    )
    files = list(evidence_dir.glob("multiseed-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
    assert "do not establish independent production evidence" in persisted["claim_boundary"]
