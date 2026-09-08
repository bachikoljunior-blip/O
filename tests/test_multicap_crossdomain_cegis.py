from __future__ import annotations

import json
from pathlib import Path

import continual.engine as engine_module

from agi.multicap_crossdomain_cegis import run_multicap_crossdomain_cegis_campaign


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-multicap-crossdomain-cegis"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(f"model call was not expected in mechanical multicap CEGIS: {component}")


def test_three_distinct_crossdomain_capabilities_accumulate_without_forgetting_or_trial_spend(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)

    # Deterministic seeds cover string->sequence, string->numeric, and sequence->string families.
    seeds = ("multi-0", "multi-1", "multi-2")
    report = run_multicap_crossdomain_cegis_campaign(runtime_repo, seeds)

    assert report["passed"] is True
    assert report["capability_count"] == 3
    assert report["distinct_candidate_count"] == 3
    assert report["distinct_scope_count"] == 3
    assert report["distinct_family_count"] == 3
    assert report["all_promotions_passed"] is True
    assert report["all_restart_retests_passed"] is True
    assert report["all_trial_budgets_unchanged"] is True
    assert [stage["accumulated_capability_count"] for stage in report["stages"]] == [1, 2, 3]
    assert [len(stage["fresh_engine_retests"]) for stage in report["stages"]] == [1, 2, 3]
    assert all(
        retest["score"] == 1.0 and retest["trial_budget_unchanged"]
        for stage in report["stages"]
        for retest in stage["fresh_engine_retests"]
    )
    assert NeverModelClient.calls == []

    evidence = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "multicap-crossdomain-cegis"
        / f"{report['campaign_id']}.json"
    )
    persisted = json.loads(evidence.read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert "does not prove AGI" in persisted["claim_boundary"]

    # The multi-capability evidence itself contains no raw generator seeds.
    text = evidence.read_text(encoding="utf-8")
    assert all(seed not in text for seed in seeds)
