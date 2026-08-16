from __future__ import annotations

import json
from pathlib import Path

import continual.engine as engine_module
from agi.acquired_program_cegis import run_acquired_program_cegis_campaign


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-cegis-test"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(f"model call was not expected in mechanical CEGIS campaign: {component}")


def test_failed_hypothesis_is_corrected_by_counterexample_without_leaking_final_holdout(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)

    report = run_acquired_program_cegis_campaign(runtime_repo)

    assert report["passed"] is True
    assert report["commitment_verified"] is True
    assert report["initial_hypothesis_was_wrong"] is True
    assert report["counterexample_count"] == 1
    assert report["refinement_rounds"] == 2
    assert len(report["failed_hypotheses"]) == 1
    assert report["failed_hypotheses"][0]["round"] == 1
    assert report["failed_hypotheses"][0]["counterexample_index"] == 0
    assert report["final_answers_exposed_to_learner"] is False
    assert report["baseline_score"] == 0.0
    assert report["candidate_score"] == 1.0
    assert report["promotion"]["adopt_candidate"] is True
    assert report["post_restart_runtime_score"] == 1.0
    assert report["prior_capability_score_before_promotion"] == 1.0
    assert report["prior_capability_score_after_promotion"] == 1.0
    assert report["failed_hypothesis_history_retained"] is True
    assert all(item["success"] for item in report["runtime_results"])
    assert NeverModelClient.calls == []

    candidate_dir = runtime_repo / ".continual" / "candidates" / report["candidate_id"]
    candidate = json.loads((candidate_dir / "candidate.json").read_text(encoding="utf-8"))
    failures = [
        item
        for item in candidate["contradictory_evidence"]
        if item.get("type") == "cegis_failed_hypothesis"
    ]
    assert len(failures) == 1
    assert candidate["scope_states"][report["scope"]] == "VERIFIED_FOR_SCOPE"

    history = json.loads(
        (candidate_dir / "learning-history" / "cegis.json").read_text(encoding="utf-8")
    )
    assert history["final_heldout_answers_persisted"] is False
    assert len(history["hypotheses"]) == 2
    assert history["hypotheses"][0]["diagnostic_passed"] is False
    assert history["hypotheses"][1]["diagnostic_passed"] is True
    assert history["counterexamples"] == [{"input": -4, "output": 4}]
    assert all(value not in history["initial_demonstrations"] for value in [
        {"input": -17, "output": 17},
        {"input": -6, "output": 6},
        {"input": 5, "output": 5},
        {"input": 21, "output": 21},
    ])

    trial_files = list((candidate_dir / "trials").glob("*.json"))
    assert len(trial_files) == 1
    ledger = json.loads(trial_files[0].read_text(encoding="utf-8"))
    assert len(ledger["attempts"]) == 1
    assert ledger["attempts"][0]["state"] == "COMPLETED"

    evidence_path = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "acquired-program-cegis"
        / f"{report['campaign_id']}.json"
    )
    persisted = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert "final_inputs" not in persisted
    assert "final_expected" not in persisted
    assert "independent production AGI evidence" in persisted["claim_boundary"]
