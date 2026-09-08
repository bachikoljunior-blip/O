from __future__ import annotations

import json
from pathlib import Path

import continual.engine as engine_module
from agi.cumulative_crossdomain_failure_retention import (
    run_cumulative_crossdomain_failure_retention,
)


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-cumulative-crossdomain-retention"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(
            f"model call was not expected in mechanical cumulative cross-domain retention: {component}"
        )


def test_cumulative_crossdomain_failure_derived_learning_retains_both_domains(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)

    report = run_cumulative_crossdomain_failure_retention(
        runtime_repo,
        "cumulative-crossdomain-failure-retention-test",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "cumulative-crossdomain-failure-retention-v1"
    assert report["round_count"] == 2
    assert report["round_plan_precommitted_before_learning"] is True
    assert set(report["planned_source_input_domains"]) == {"string", "sequence"}
    assert report["source_input_domains_heterogeneous"] is True
    assert report["source_candidate_ids_unique"] is True
    assert report["learned_candidate_ids_unique"] is True
    assert report["all_round_negative_evidence_retained"] is True
    assert report["all_round_targets_failed_closed_before_learning"] is True
    assert report["all_round_controls_failed_closed_during_learning"] is True
    assert report["first_round_candidate_state_unchanged_after_second_round"] is True
    assert report["first_round_trial_state_unchanged_after_second_round"] is True
    assert report["preexisting_candidate_state_unchanged"] is True
    assert report["preexisting_trial_state_unchanged"] is True
    assert report["all_derived_behaviors_replayed_after_later_learning"] is True
    assert report["all_source_behaviors_replayed_after_later_learning"] is True
    assert report["all_controls_failed_closed_after_later_learning"] is True
    assert report["all_replays_used_fresh_unique_engines"] is True
    assert report["all_replays_avoided_caller_candidate_ids"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_state_unchanged"] is True
    assert report["live_model_invocation_required"] is False
    assert NeverModelClient.calls == []

    assert len(report["fresh_state_only_replays"]) == 2
    for replay in report["fresh_state_only_replays"]:
        assert replay["derived_candidate_id"] in replay["derived_replay"]["solver_selected_candidate_ids"]
        assert replay["source_candidate_id"] in replay["source_replay"]["solver_selected_candidate_ids"]
        assert replay["derived_replay"]["solver_candidate_ids_supplied_by_caller"] is False
        assert replay["source_replay"]["solver_candidate_ids_supplied_by_caller"] is False
        assert replay["derived_replay"]["fresh_engine_runs_unique"] is True
        assert replay["source_replay"]["fresh_engine_runs_unique"] is True
        assert replay["control_gap_failed_closed_after_all_rounds"] is True

    outputs = list(
        (
            runtime_repo
            / ".continual"
            / "evidence"
            / "cumulative-crossdomain-failure-retention"
        ).glob("cumulative-crossdomain-*.json")
    )
    assert len(outputs) == 1
    persisted = json.loads(outputs[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert "not independent production evidence" in persisted["claim_boundary"]
    assert "open-domain autonomous objective invention" in persisted["claim_boundary"]
