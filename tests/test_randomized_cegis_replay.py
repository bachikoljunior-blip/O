from __future__ import annotations

import json
from pathlib import Path

import continual.engine as engine_module
from agi.randomized_cegis import run_randomized_cegis_campaign


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-randomized-cegis-replay"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(f"model call was not expected during randomized CEGIS replay: {component}")


def test_same_committed_randomized_campaign_replays_without_consuming_new_trial_budget(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)
    seed = "random-cegis-replay-stable"

    first = run_randomized_cegis_campaign(runtime_repo, seed)
    second = run_randomized_cegis_campaign(runtime_repo, seed)

    assert first["passed"] is True
    assert second["passed"] is True
    assert second["campaign_id"] == first["campaign_id"]
    assert second["candidate_id"] == first["candidate_id"]
    assert second["tool_id"] == first["tool_id"]
    assert second["scope"] == first["scope"]
    assert second["commitment"] == first["commitment"]
    assert second["variant_index"] == first["variant_index"]
    assert second["promotion"]["regression_evidence_sha256"] == first["promotion"][
        "regression_evidence_sha256"
    ]
    assert second["post_restart_runtime_score"] == 1.0

    candidate_dir = runtime_repo / ".continual" / "candidates" / first["candidate_id"]
    trial_files = list((candidate_dir / "trials").glob("*.json"))
    assert len(trial_files) == 1
    ledger = json.loads(trial_files[0].read_text(encoding="utf-8"))
    assert len(ledger["attempts"]) == 1
    assert ledger["attempts"][0]["attempt"] == 1
    assert ledger["attempts"][0]["state"] == "COMPLETED"

    history = json.loads(
        (candidate_dir / "learning-history" / "randomized-cegis.json").read_text(encoding="utf-8")
    )
    assert history["final_heldout_answers_persisted"] is False
    assert NeverModelClient.calls == []
