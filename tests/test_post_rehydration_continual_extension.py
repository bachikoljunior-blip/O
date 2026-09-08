from __future__ import annotations

import json
from pathlib import Path

from agi.post_rehydration_continual_extension import (
    run_post_rehydration_continual_extension,
)


def test_rehydrated_state_can_learn_new_skill_and_survive_second_restart(
    runtime_repo: Path,
) -> None:
    report = run_post_rehydration_continual_extension(
        runtime_repo,
        "post-rehydration-continual-extension-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "post-rehydration-continual-extension-v1"
    assert len(report["heterogeneous_candidate_ids"]) == 4
    assert len(report["recursive_component_candidate_ids"]) == 3
    assert report["extension_candidate_id"] not in report["prior_candidate_ids"]
    assert report["extension_negative_evidence_retained"] is True
    assert report["prior_state_unchanged_after_extension"] is True
    assert report["prior_trials_unchanged_after_extension"] is True
    assert report["first_rehydrated_root_is_distinct"] is True
    assert report["second_rehydrated_root_is_distinct"] is True
    assert report["prior_runs_copied"] is False
    assert report["prior_episodes_copied"] is False
    assert report["prior_evidence_copied"] is False
    assert report["second_copy_byte_identical"] is True
    assert report["second_trials_byte_identical"] is True
    assert report["second_state_unchanged_after_replay"] is True
    assert report["second_trials_unchanged_after_replay"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_ledgers_unchanged"] is True
    assert report["fresh_engine_run_count"] == 12
    assert len(set(report["fresh_engine_runs"])) == 12
    assert report["live_model_invocation_required"] is False

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "post-rehydration-continual-extension"
    )
    files = list(evidence_dir.glob("extension-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
