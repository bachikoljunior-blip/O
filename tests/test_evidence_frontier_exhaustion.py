from __future__ import annotations

from pathlib import Path

import continual.engine as engine_module
from agi.evidence_frontier_exhaustion import run_evidence_frontier_exhaustion


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-evidence-frontier-exhaustion"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(
            f"model call was not expected in mechanical repeated evidence-frontier learning: {component}"
        )


def test_evidence_frontier_takes_second_objective_without_forgetting(
    runtime_repo: Path,
    monkeypatch,
):
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)

    report = run_evidence_frontier_exhaustion(
        runtime_repo,
        "evidence-frontier-exhaustion-test",
    )

    assert report["passed"] is True
    assert report["frontier_precommitted_before_round_two_support_checks"] is True
    assert report["caller_selected_source_domain_round_two"] is False
    assert report["fresh_target_generator_used_for_round_two_objective"] is False
    assert report["second_selected_semantic_signature"] != report["first_selected_semantic_signature"]
    assert report["second_objective_failed_closed_before_learning"] is True
    assert report["second_candidate_negative_evidence_retained"] is True
    assert report["first_candidate_state_unchanged_during_round_two"] is True
    assert report["prior_candidate_state_unchanged"] is True
    assert report["prior_trial_state_unchanged"] is True
    assert report["first_candidate_rediscovered_after_round_two"] is True
    assert report["second_candidate_rediscovered_without_caller_ids"] is True
    assert report["all_base_source_behaviors_retained"] is True
    assert report["all_base_derived_behaviors_retained"] is True
    assert report["all_remaining_controls_failed_closed"] is True
    assert report["all_replays_avoided_caller_candidate_ids"] is True
    assert report["base_behavior_replays"]
    assert report["live_model_invocation_required"] is False
    assert NeverModelClient.calls == []
    assert "independent production evidence" in report["claim_boundary"]
    assert "AGI" in report["claim_boundary"]
