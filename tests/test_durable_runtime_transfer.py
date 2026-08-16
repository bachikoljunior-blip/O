from __future__ import annotations

import json
from pathlib import Path

import continual.engine as engine_module
from agi.durable_runtime_transfer import run_durable_runtime_transfer_campaign


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-durable-runtime-test"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(f"model call was not expected in mechanical durable campaign: {component}")


def test_learned_capability_transfers_across_durable_runs_and_rolls_back_fail_closed(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)

    report = run_durable_runtime_transfer_campaign(runtime_repo, "durable-transfer-rollback-41")

    assert report["passed"] is True
    assert report["seed_persisted"] is False
    assert len(set(report["durable_run_ids"])) == 2
    assert report["durable_runs_persisted"] is True
    assert report["durable_transfer_scores"] == [1.0, 1.0]
    assert report["rollback_adopted"] is False
    assert report["revoked_scope_failed_closed"] is True
    assert report["protected_prior_score_before_rollback"] == 1.0
    assert report["protected_prior_score_after_rollback"] == 1.0
    assert report["recovery_adopted"] is True
    assert report["recovered_run_persisted"] is True
    assert report["recovered_score"] == 1.0
    assert report["negative_evidence_retained"] is True
    assert NeverModelClient.calls == []

    for run_id in [*report["durable_run_ids"], report["revoked_runtime_run_id"], report["recovered_run_id"]]:
        assert (runtime_repo / ".continual" / "runs" / run_id / "snapshot.json").is_file()

    candidate_path = (
        runtime_repo / ".continual" / "candidates" / report["candidate_id"] / "candidate.json"
    )
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert candidate["scope_states"][report["scope"]] == "VERIFIED_FOR_SCOPE"
    failures = [
        item
        for item in candidate["contradictory_evidence"]
        if item.get("type") == "deterministic_regression_gate_failure"
        and item.get("scope") == report["scope"]
    ]
    assert len(failures) >= 2
    assert report["rollback_decision_ref"] in candidate["regression_decision_refs"]
    assert report["recovery_decision_ref"] in candidate["regression_decision_refs"]

    evidence_path = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "durable-runtime-transfer"
        / f"{report['campaign_id']}.json"
    )
    persisted = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert "durable-transfer-rollback-41" not in evidence_path.read_text(encoding="utf-8")
    assert "do not prove AGI" in persisted["claim_boundary"]
