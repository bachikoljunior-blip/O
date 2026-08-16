from __future__ import annotations

import json
from pathlib import Path

import pytest

import continual.engine as engine_module
from agi.acquired_program_runtime import _digest
from agi.generated_crossdomain_cegis import (
    generated_crossdomain_target,
    run_generated_crossdomain_cegis_campaign,
)


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-generated-crossdomain-cegis"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(f"model call was not expected in mechanical cross-domain CEGIS: {component}")


def test_seeded_crossdomain_generator_varies_domains_and_compositions() -> None:
    descriptors = []
    families = set()
    domain_pairs = set()
    longest = 0
    for index in range(40):
        descriptor, metadata, initial, diagnostic, final = generated_crossdomain_target(
            f"cross-family-{index}"
        )
        descriptors.append(_digest(descriptor))
        families.add(metadata["family_index"])
        domain_pairs.add((metadata["input_domain"], metadata["output_domain"]))
        longest = max(longest, metadata["nodes"])
        assert descriptor["effects"] == []
        assert metadata["nodes"] <= 8
        assert len(initial) >= 3
        assert set(map(_digest, initial)).isdisjoint(set(map(_digest, diagnostic)))
        assert set(map(_digest, initial)).isdisjoint(set(map(_digest, final)))
        assert set(map(_digest, diagnostic)).isdisjoint(set(map(_digest, final)))

    assert families == {0, 1, 2, 3}
    assert domain_pairs == {
        ("string", "string"),
        ("string", "sequence"),
        ("string", "numeric"),
        ("sequence", "string"),
    }
    assert len(set(descriptors)) >= 20
    assert longest >= 7


@pytest.mark.parametrize(
    ("seed", "input_domain", "output_domain"),
    [
        ("cross-family-2", "string", "sequence"),
        ("cross-family-14", "sequence", "string"),
    ],
)
def test_generated_crossdomain_programs_refine_promote_and_survive_restart(
    runtime_repo: Path,
    monkeypatch,
    seed: str,
    input_domain: str,
    output_domain: str,
) -> None:
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)

    report = run_generated_crossdomain_cegis_campaign(runtime_repo, seed)

    assert report["passed"] is True
    assert report["input_domain"] == input_domain
    assert report["output_domain"] == output_domain
    assert report["commitment_verified"] is True
    assert report["initial_hypothesis_was_wrong"] is True
    assert report["counterexample_count"] >= 1
    assert report["refinement_rounds"] >= 2
    assert report["target_node_count"] <= 8
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
    state = json.loads((candidate_dir / "candidate.json").read_text(encoding="utf-8"))
    assert state["scope_states"][report["scope"]] == "VERIFIED_FOR_SCOPE"
    assert any(
        item.get("type") == "cegis_failed_hypothesis"
        for item in state["contradictory_evidence"]
    )
    assert any(
        item.get("type") == "deterministic_regression_gate_failure"
        for item in state["contradictory_evidence"]
    )

    history_path = candidate_dir / "learning-history" / "generated-crossdomain-cegis.json"
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
        / "generated-crossdomain-cegis"
        / f"{report['campaign_id']}.json"
    )
    evidence_text = evidence_path.read_text(encoding="utf-8")
    evidence = json.loads(evidence_text)
    assert evidence["digest"] == report["digest"]
    assert seed not in evidence_text
    assert "independent production" in evidence["claim_boundary"]
