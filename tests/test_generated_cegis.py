from __future__ import annotations

import json
from pathlib import Path

import continual.engine as engine_module
from agi.acquired_program_runtime import _digest
from agi.acquired_programs import validate_program_descriptor
from agi.generated_cegis import generated_target_descriptor, run_generated_cegis_campaign


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-generated-cegis"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(f"model call was not expected in mechanical generated CEGIS: {component}")


def test_seeded_generator_builds_many_bounded_targets_without_a_task_catalog() -> None:
    targets = []
    modes = set()
    thresholds = set()
    for index in range(32):
        descriptor, metadata = generated_target_descriptor(f"generated-family-{index}")
        validated = validate_program_descriptor(descriptor)
        targets.append(_digest(descriptor))
        modes.add(metadata["mode"])
        thresholds.add(metadata["threshold"])
        assert descriptor["effects"] == []
        assert descriptor["input_domain"] == "numeric"
        assert descriptor["output_domain"] == "numeric"
        assert validated["nodes"] <= 7
        assert metadata["threshold"] != 0

    assert len(set(targets)) >= 24
    assert modes == {0, 1, 2}
    assert len(thresholds) >= 6


def test_generated_target_is_refined_promoted_and_retained_without_holdout_leakage(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    seed = "generated-family-1"
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)

    report = run_generated_cegis_campaign(runtime_repo, seed)

    assert report["passed"] is True
    assert report["commitment_verified"] is True
    assert report["initial_hypothesis_was_wrong"] is True
    assert report["counterexample_count"] >= 1
    assert report["refinement_rounds"] >= 2
    assert report["target_node_count"] <= 7
    assert report["seed_value_persisted"] is False
    assert report["target_descriptor_persisted_to_learner"] is False
    assert report["final_answers_exposed_to_learner"] is False
    assert report["final_heldout_answers_persisted"] is False
    assert report["baseline_score"] == 0.0
    assert report["candidate_score"] == 1.0
    assert report["forced_regression_rejected"] is True
    assert report["promotion"]["adopt_candidate"] is True
    assert report["negative_evidence_retained"] is True
    assert report["post_restart_runtime_score"] == 1.0
    assert report["prior_capability_score_before_promotion"] == 1.0
    assert report["prior_capability_score_after_promotion"] == 1.0
    assert all(item["success"] for item in report["runtime_results"])
    assert NeverModelClient.calls == []

    candidate_dir = runtime_repo / ".continual" / "candidates" / report["candidate_id"]
    candidate = json.loads((candidate_dir / "candidate.json").read_text(encoding="utf-8"))
    assert candidate["scope_states"][report["scope"]] == "VERIFIED_FOR_SCOPE"
    assert any(
        item.get("type") == "cegis_failed_hypothesis"
        for item in candidate["contradictory_evidence"]
    )
    assert any(
        item.get("type") == "deterministic_regression_gate_failure"
        for item in candidate["contradictory_evidence"]
    )

    history_path = candidate_dir / "learning-history" / "generated-cegis.json"
    history_text = history_path.read_text(encoding="utf-8")
    history = json.loads(history_text)
    assert seed not in history_text
    assert history["target_descriptor_persisted"] is False
    assert history["final_heldout_answers_persisted"] is False
    assert len(history["counterexamples"]) >= 1
    assert "target" not in history
    assert "final" not in history

    trial_files = list((candidate_dir / "trials").glob("*.json"))
    assert len(trial_files) == 1
    ledger = json.loads(trial_files[0].read_text(encoding="utf-8"))
    assert len(ledger["attempts"]) == 2
    assert [item["state"] for item in ledger["attempts"]] == ["COMPLETED", "COMPLETED"]

    evidence_path = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "generated-cegis"
        / f"{report['campaign_id']}.json"
    )
    evidence_text = evidence_path.read_text(encoding="utf-8")
    evidence = json.loads(evidence_text)
    assert evidence["digest"] == report["digest"]
    assert seed not in evidence_text
    assert "independent production" in evidence["claim_boundary"]
