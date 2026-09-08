from __future__ import annotations

from pathlib import Path

import continual.engine as engine_module
from agi.recursive_evidence_frontier_growth import run_recursive_evidence_frontier_growth


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-recursive-evidence-frontier-growth"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(
            f"model call was not expected in bounded recursive evidence-frontier growth: {component}"
        )


def test_recursive_frontier_derives_another_gap_from_newly_learned_behavior(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)

    report = run_recursive_evidence_frontier_growth(
        runtime_repo,
        "recursive-evidence-frontier-growth-test",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "recursive-evidence-frontier-growth-v1"
    assert report["recursive_target_generation_source"] == "newly_learned_second_expansion_behavior"
    assert report["finite_first_expansion_schedule_replayed_as_recursive_objective"] is False
    assert report["fresh_global_target_generator_used_for_recursive_objective"] is False
    assert report["recursive_schedule_precommitted_before_support_checks"] is True
    assert report["recursive_target_absent_from_prior_semantics"] is True
    assert report["recursive_target_failed_closed_before_learning"] is True
    assert report["recursive_control_remained_failed_closed"] is True
    assert report["new_candidate_negative_evidence_retained"] is True
    assert report["prior_candidate_state_unchanged"] is True
    assert report["prior_trial_state_unchanged"] is True
    assert report["all_five_capabilities_rediscovered"] is True
    assert report["all_replays_avoided_caller_candidate_ids"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_state_unchanged"] is True
    assert report["live_model_invocation_required"] is False
    assert NeverModelClient.calls == []
    assert "not open-domain autonomous objective invention" in report["claim_boundary"]
    assert "not" in report["claim_boundary"]
    assert report["digest"]
