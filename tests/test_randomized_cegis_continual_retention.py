from __future__ import annotations

import json
from pathlib import Path

import continual.engine as engine_module
from agi.randomized_cegis import run_randomized_cegis_campaign
from continual.learning_engine import LearningEnabledEngine


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-randomized-cegis-retention"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(f"model call was not expected during mechanical retention test: {component}")


def _new_run(engine: LearningEnabledEngine) -> str:
    run_id = engine.store.new_id("run")
    run_dir = engine.store.run_dir(run_id)
    run_dir.mkdir(parents=True)
    engine.store.atomic_json(
        run_dir / "snapshot.json",
        {"revision": 0, "status": "continue", "phase": "unit_pending"},
    )
    return run_id


def _invoke(
    engine: LearningEnabledEngine,
    run_id: str,
    *,
    scope: str,
    tool_id: str,
    value: int,
) -> int:
    result = engine._invoke(
        run_id,
        "execute",
        {
            "snapshot": {"revision": 0},
            "execution_unit": {
                "goal": "retest every previously acquired randomized CEGIS capability",
                "scope": scope,
                "learned_tool_call": {"tool_id": tool_id, "input": value},
            },
        },
    )
    return result["result"]["output"]


def test_three_randomized_cegis_promotions_accumulate_across_fresh_engines_without_forgetting(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)
    cases = [
        (
            "random-cegis-d",
            0,
            [(-17, 17), (-6, 6), (5, 5), (21, 21)],
        ),
        (
            "random-cegis-c",
            1,
            [(-20, 0), (-1, 0), (0, 0), (7, 7), (19, 19)],
        ),
        (
            "random-cegis-a",
            2,
            [(-31, -1), (-1, -1), (0, 1), (6, 1), (40, 1)],
        ),
    ]
    acquired: list[tuple[dict, list[tuple[int, int]]]] = []

    for seed, expected_variant, queries in cases:
        report = run_randomized_cegis_campaign(runtime_repo, seed)
        assert report["passed"] is True
        assert report["variant_index"] == expected_variant
        acquired.append((report, queries))

        fresh = LearningEnabledEngine(runtime_repo)
        run_id = _new_run(fresh)
        for previous_report, previous_queries in acquired:
            answers = [
                _invoke(
                    fresh,
                    run_id,
                    scope=previous_report["scope"],
                    tool_id=previous_report["tool_id"],
                    value=query,
                )
                for query, _ in previous_queries
            ]
            assert answers == [expected for _, expected in previous_queries]

        for previous_report, _ in acquired:
            candidate_dir = (
                runtime_repo
                / ".continual"
                / "candidates"
                / previous_report["candidate_id"]
            )
            candidate = json.loads((candidate_dir / "candidate.json").read_text(encoding="utf-8"))
            assert candidate["scope_states"][previous_report["scope"]] == "VERIFIED_FOR_SCOPE"
            trial_files = list((candidate_dir / "trials").glob("*.json"))
            assert len(trial_files) == 1
            ledger = json.loads(trial_files[0].read_text(encoding="utf-8"))
            assert len(ledger["attempts"]) == 1
            assert ledger["attempts"][0]["state"] == "COMPLETED"

    assert len(acquired) == 3
    assert NeverModelClient.calls == []
