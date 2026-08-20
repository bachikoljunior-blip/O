from __future__ import annotations

from pathlib import Path

import continual.engine as engine_module
from agi.evidence_frontier_iterated_expansion import run_evidence_frontier_iterated_expansion


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-evidence-frontier-iterated-expansion"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(
            f"model call was not expected in bounded iterated evidence-frontier learning: {component}"
        )


def test_iterated_evidence_frontier_expansion_retains_all_accumulated_capabilities(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)

    report = run_evidence_frontier_iterated_expansion(
        runtime_repo,
        "evidence-frontier-iterated-expansion-test",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "evidence-frontier-iterated-expansion-v1"
    assert report["second_objective_source"] == "persisted_first_expansion_untouched_control"
    assert report["fresh_global_target_generator_used_for_second_objective"] is False
    assert report["first_expansion_behavior_supported_before_second_learning"] is True
    assert report["second_expansion_failed_closed_before_learning"] is True
    assert report["second_expansion_negative_evidence_retained"] is True
    assert report["prior_candidate_state_unchanged"] is True
    assert report["prior_trial_state_unchanged"] is True
    assert report["all_four_capabilities_rediscovered"] is True
    assert report["all_replays_avoided_caller_candidate_ids"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_state_unchanged"] is True
    assert report["live_model_invocation_required"] is False
    assert NeverModelClient.calls == []
    assert "not open-domain autonomous objective invention" in report["claim_boundary"]
    assert "not" in report["claim_boundary"]
    assert report["digest"]
