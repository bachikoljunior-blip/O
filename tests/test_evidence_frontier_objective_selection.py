from __future__ import annotations

from pathlib import Path

import continual.engine as engine_module
from agi.evidence_frontier_objective_selection import (
    run_evidence_frontier_objective_selection,
)


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-evidence-frontier-objective"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(
            f"model call was not expected in mechanical evidence-frontier objective selection: {component}"
        )


def test_evidence_frontier_selects_next_objective_without_caller_domain(
    runtime_repo: Path,
    monkeypatch,
):
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)

    report = run_evidence_frontier_objective_selection(
        runtime_repo,
        "evidence-frontier-objective-selection-test",
    )

    assert report["passed"] is True
    assert report["objective_selection_source"] == "persisted_heterogeneous_fail_closed_controls"
    assert report["caller_selected_source_domain"] is False
    assert report["fresh_target_generator_used_for_next_objective"] is False
    assert report["frontier_precommitted_before_support_checks"] is True
    assert report["frontier_size"] >= 2
    assert len(report["support_attempts"]) == report["frontier_size"]
    assert report["selected_objective_failed_closed_before_learning"] is True
    assert report["new_candidate_negative_evidence_retained"] is True
    assert report["prior_candidate_state_unchanged"] is True
    assert report["prior_trial_state_unchanged"] is True
    assert report["new_candidate_rediscovered_without_caller_ids"] is True
    assert report["all_prior_source_behaviors_retained"] is True
    assert report["all_prior_derived_behaviors_retained"] is True
    assert report["all_unselected_controls_failed_closed_after_learning"] is True
    assert report["all_replays_used_fresh_unique_engines"] is True
    assert report["all_replays_avoided_caller_candidate_ids"] is True
    assert report["fresh_behavior_only_transfer"]["solver_candidate_ids_supplied_by_caller"] is False
    assert len(report["prior_behavior_replays"]) >= 2
    assert report["untouched_controls"]
    assert report["live_model_invocation_required"] is False
    assert NeverModelClient.calls == []
    assert "independent production evidence" in report["claim_boundary"]
    assert "AGI" in report["claim_boundary"]
