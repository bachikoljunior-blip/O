from __future__ import annotations

import json
from pathlib import Path

import continual.engine as engine_module
from agi.acquired_program_continual import run_acquired_program_continual_campaign


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-continual-test"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(f"model call was not expected in mechanical continual campaign: {component}")


def test_three_hidden_cross_domain_capabilities_accumulate_without_forgetting(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    seeds = (
        "continual-hidden-0",  # numeric -> string under the deterministic hidden generator
        "continual-hidden-1",  # sequence -> numeric
        "continual-hidden-4",  # string -> numeric
    )
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)

    report = run_acquired_program_continual_campaign(runtime_repo, seeds)

    assert report["passed"] is True
    assert report["stage_count"] == 3
    assert report["seed_values_persisted"] is False
    assert report["heldout_answers_exposed_to_learner"] is False
    assert report["contamination_events"] == []
    assert NeverModelClient.calls == []
    assert {item["input_domain"] for item in report["stage_reports"]} == {
        "numeric",
        "sequence",
        "string",
    }
    assert [len(item["retained_after_stage"]) for item in report["stage_reports"]] == [1, 2, 3]
    assert all(item["baseline_score"] == 0.0 for item in report["stage_reports"])
    assert all(item["candidate_score"] == 1.0 for item in report["stage_reports"])
    assert all(item["forced_regression_rejected"] for item in report["stage_reports"])
    assert all(item["promotion_adopted"] for item in report["stage_reports"])
    assert all(item["negative_evidence_retained"] for item in report["stage_reports"])
    assert all(item["all_retained_after_stage"] for item in report["stage_reports"])
    assert len(report["final_catalog"]) == 3
    assert all(item["program_kind"] == "acquired_program" for item in report["final_catalog"])
    assert len(report["final_retests"]) == 3
    assert all(item["score"] == 1.0 for item in report["final_retests"])

    for stage in report["stage_reports"]:
        candidate_dir = (
            runtime_repo / ".continual" / "candidates" / stage["candidate_id"]
        )
        candidate = json.loads((candidate_dir / "candidate.json").read_text(encoding="utf-8"))
        assert candidate["scope_states"][stage["scope"]] == "VERIFIED_FOR_SCOPE"
        assert len(candidate["contradictory_evidence"]) == 1
        assert len(candidate["regression_decision_refs"]) == 2
        trial_files = list((candidate_dir / "trials").glob("*.json"))
        assert len(trial_files) == 1
        ledger = json.loads(trial_files[0].read_text(encoding="utf-8"))
        assert [item["attempt"] for item in ledger["attempts"]] == [1, 2]
        assert [item["state"] for item in ledger["attempts"]] == ["COMPLETED", "COMPLETED"]

    evidence_path = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "acquired-program-continual"
        / f"{report['campaign_id']}.json"
    )
    text = evidence_path.read_text(encoding="utf-8")
    persisted = json.loads(text)
    assert persisted["digest"] == report["digest"]
    assert all(seed not in text for seed in seeds)
    assert "independent production AGI evidence" in persisted["claim_boundary"]
