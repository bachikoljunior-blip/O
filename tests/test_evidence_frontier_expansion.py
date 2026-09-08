from __future__ import annotations

from pathlib import Path

import continual.engine as engine_module
from agi.evidence_frontier_expansion import run_evidence_frontier_expansion


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-evidence-frontier-expansion"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(
            f"model call was not expected in bounded post-exhaustion evidence learning: {component}"
        )


def test_exhausted_failure_frontier_derives_a_new_objective_without_forgetting(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)

    report = run_evidence_frontier_expansion(
        runtime_repo,
        "evidence-frontier-expansion-test",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "evidence-frontier-expansion-v1"
    assert report["target_generation_source"] == "persisted_exhausted_heterogeneous_evidence"
    assert report["finite_exhausted_control_replayed_as_new_objective"] is False
    assert report["fresh_global_target_generator_used_for_objective_choice"] is False
    assert report["target_selection_uses_audit_storage_digest"] is False
    assert report["expansion_schedule_precommitted_before_support_checks"] is True
    assert report["expanded_target_absent_from_exhausted_frontier"] is True
    assert report["expanded_target_failed_closed_before_learning"] is True
    assert report["derived_control_failed_closed_before_learning"] is True
    assert report["new_candidate_negative_evidence_retained"] is True
    assert report["transactional_commit"]["atomic_directory_rename"] is True
    assert report["transactional_commit"]["overwrite_allowed"] is False
    assert report["prior_candidate_state_unchanged"] is True
    assert report["prior_trial_state_unchanged"] is True
    assert report["first_candidate_rediscovered_after_expansion"] is True
    assert report["second_candidate_rediscovered_after_expansion"] is True
    assert report["expanded_candidate_rediscovered_without_caller_ids"] is True
    assert report["all_replays_avoided_caller_candidate_ids"] is True
    assert report["derived_control_gap_remained_failed_closed"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_state_unchanged"] is True
    assert report["live_model_invocation_required"] is False
    assert NeverModelClient.calls == []
    assert len(report["support_attempts"]) >= 2
    assert all(
        item["candidate_ids_supplied_by_caller"] is False
        for item in report["support_attempts"]
    )
    assert "not open-domain autonomous objective invention" in report["claim_boundary"]
    assert "independent production evidence" in report["claim_boundary"]
    assert "AGI" in report["claim_boundary"]
