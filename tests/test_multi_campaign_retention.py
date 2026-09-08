from __future__ import annotations

import json
from pathlib import Path

from agi.multi_campaign_retention import run_multi_campaign_retention


def test_sequential_structured_campaigns_retain_anchor_trial_state(
    runtime_repo: Path,
) -> None:
    report = run_multi_campaign_retention(
        runtime_repo,
        "multi-campaign-retention-seed",
        campaign_count=4,
    )

    assert report["passed"] is True
    assert report["campaign_count"] == 4
    assert report["candidate_count"] == 8
    assert report["tool_count"] == 8
    assert report["fresh_engine_count"] == 12
    assert len(set(report["candidate_ids"])) == 8
    assert len(set(report["tool_ids"])) == 8
    assert len(set(report["fresh_engine_runs"])) == 12
    assert report["anchor_trial_state_unchanged_after_later_learning"] is True
    assert report["all_negative_evidence_retained"] is True
    assert report["all_fresh_engine_composition_matched"] is True
    assert report["all_runner_attestations_checked"] is True
    assert report["all_runners_internal_test_doubles"] is True
    assert report["live_model_invocation_required"] is False

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "multi-campaign-retention"
    )
    files = list(evidence_dir.glob("multi-campaign-retention-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
