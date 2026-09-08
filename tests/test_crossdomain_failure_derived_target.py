from __future__ import annotations

import json
from pathlib import Path

import continual.engine as engine_module
from agi.crossdomain_failure_derived_target import (
    _expand_support_inputs,
    run_crossdomain_failure_derived_target,
)


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-crossdomain-failure-derived-target"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(f"model call was not expected in mechanical failure-derived target: {component}")


def test_sequence_support_expansion_spans_a_broad_numeric_frontier() -> None:
    support = _expand_support_inputs(
        {"input_domain": "sequence"},
        ([], [1, -1], [2, -3, 1], [1]),
        history_digest="0" * 64,
    )

    sums = {
        sum(value)
        for value in support
        if isinstance(value, list)
        and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
    }
    assert len(support) > 8
    assert min(sums) <= -10
    assert max(sums) >= 10
    assert all(not isinstance(value, list) or len(value) <= 32 for value in support)


def test_crossdomain_counterexample_history_drives_next_target(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)

    report = run_crossdomain_failure_derived_target(
        runtime_repo,
        "crossdomain-failure-derived-target-test",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "crossdomain-failure-derived-target-v1"
    assert report["source_counterexample_count"] >= 1
    assert report["source_negative_evidence_retained"] is True
    assert report["target_generation_source"] == "persisted_crossdomain_counterexample_history"
    assert report["source_seed_value_persisted"] is False
    assert report["derived_schedule_precommitted_before_support_checks"] is True
    assert len(report["support_attempts"]) >= 2
    assert all(
        item["candidate_ids_supplied_by_caller"] is False
        for item in report["support_attempts"]
    )
    assert report["derived_target_failed_closed_before_learning"] is True
    assert report["derived_control_failed_closed_before_learning"] is True
    assert report["new_candidate_negative_evidence_retained"] is True
    assert report["transactional_commit"]["atomic_directory_rename"] is True
    assert report["transactional_commit"]["overwrite_allowed"] is False
    assert report["prior_candidate_state_unchanged"] is True
    assert report["prior_trial_state_unchanged"] is True
    assert report["new_candidate_rediscovered_without_caller_ids"] is True
    assert report["source_candidate_rediscovered_after_new_learning"] is True
    assert report["derived_control_gap_remained_failed_closed"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_state_unchanged"] is True
    assert report["live_model_invocation_required"] is False
    assert NeverModelClient.calls == []

    transfer = report["fresh_behavior_only_transfer"]
    assert transfer["solver_candidate_ids_supplied_by_caller"] is False
    assert report["new_candidate_id"] in transfer["solver_selected_candidate_ids"]
    assert transfer["fresh_engine_runs_unique"] is True
    assert all(
        item["expected_sha256"] == item["output_sha256"]
        for item in transfer["heldback_outputs"]
    )

    retained = report["source_behavior_retention"]
    assert retained["solver_candidate_ids_supplied_by_caller"] is False
    assert report["source_candidate_id"] in retained["solver_selected_candidate_ids"]
    assert retained["fresh_engine_runs_unique"] is True

    outputs = list(
        (
            runtime_repo
            / ".continual"
            / "evidence"
            / "crossdomain-failure-derived-target"
        ).glob("crossdomain-derived-*.json")
    )
    assert len(outputs) == 1
    persisted = json.loads(outputs[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert "not independent production evidence" in persisted["claim_boundary"]
    assert "open-domain autonomous objective invention" in persisted["claim_boundary"]
