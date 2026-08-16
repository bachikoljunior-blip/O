from __future__ import annotations

import json
from pathlib import Path

import pytest

import continual.engine as engine_module
from agi.randomized_cegis import run_randomized_cegis_campaign


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-randomized-cegis"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(f"model call was not expected in mechanical randomized CEGIS: {component}")


@pytest.mark.parametrize(
    ("seed", "expected_variant"),
    [
        ("random-cegis-d", 0),
        ("random-cegis-c", 1),
        ("random-cegis-a", 2),
    ],
)
def test_randomized_committed_ambiguities_refine_without_final_holdout_leakage(
    runtime_repo: Path,
    monkeypatch,
    seed: str,
    expected_variant: int,
) -> None:
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)

    report = run_randomized_cegis_campaign(runtime_repo, seed)

    assert report["passed"] is True
    assert report["variant_index"] == expected_variant
    assert report["commitment_verified"] is True
    assert report["initial_hypothesis_was_wrong"] is True
    assert report["counterexample_count"] >= 1
    assert report["refinement_rounds"] >= 2
    assert report["baseline_score"] == 0.0
    assert report["candidate_score"] == 1.0
    assert report["promotion"]["adopt_candidate"] is True
    assert report["post_restart_runtime_score"] == 1.0
    assert report["failed_hypothesis_history_retained"] is True
    assert report["final_answers_exposed_to_learner"] is False
    assert report["final_heldout_answers_persisted"] is False
    assert all(item["success"] for item in report["runtime_results"])
    assert NeverModelClient.calls == []

    candidate_dir = runtime_repo / ".continual" / "candidates" / report["candidate_id"]
    state = json.loads((candidate_dir / "candidate.json").read_text(encoding="utf-8"))
    assert state["scope_states"][report["scope"]] == "VERIFIED_FOR_SCOPE"
    failures = [
        item
        for item in state["contradictory_evidence"]
        if item.get("type") == "cegis_failed_hypothesis"
    ]
    assert len(failures) >= 1

    history = json.loads(
        (candidate_dir / "learning-history" / "randomized-cegis.json").read_text(encoding="utf-8")
    )
    assert history["variant_index"] == expected_variant
    assert history["final_heldout_answers_persisted"] is False
    assert len(history["counterexamples"]) >= 1
    assert all("expected" not in key for key in history)

    evidence = json.loads(
        (
            runtime_repo
            / ".continual"
            / "evidence"
            / "randomized-cegis"
            / f"{report['campaign_id']}.json"
        ).read_text(encoding="utf-8")
    )
    assert evidence["digest"] == report["digest"]
    assert "independent production AGI evidence" in evidence["claim_boundary"]
