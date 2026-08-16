from __future__ import annotations

import json
from pathlib import Path

import continual.engine as engine_module


class FakeModelClient:
    """Deterministic model used to test orchestration without an API key."""

    def __init__(self, root: Path):
        self.root = root
        self.model = "fake-test-model"
        self.root_calls = 0

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        fragment = {
            "component": component,
            "purpose": f"test {component}",
            "important_decisions": [],
            "observations": [],
            "successes": [],
            "failures": [],
            "evidence_refs": [],
            "unresolved": [],
        }

        if component == "candidate_evaluate":
            return {
                "result": {"decision": "USE_ACTIVE", "scope": payload.get("target_component", "unknown")},
                "local_learn": {"decision": "NO_CHANGE", "candidates": []},
                "fragment": fragment,
            }

        if component == "entry":
            assert payload["request"] == "AGIを作って"
            return {
                "result": {
                    "goal": "AGIを作る",
                    "success_conditions": [
                        "AGI開発に向けた実行可能な成果物を作る",
                        "成果を外部証拠で評価する",
                    ],
                },
                "local_learn": {"decision": "NO_CHANGE", "candidates": []},
                "fragment": fragment,
            }

        if component == "root":
            self.root_calls += 1
            if self.root_calls == 1:
                result = {
                    "component": "execute",
                    "goal": "AGI研究・実装の最初の縦切りを作る",
                    "scope": "agi/prototype",
                }
            else:
                result = {
                    "component": "task_evaluate",
                    "goal": "AGI依頼の今回の成果を評価する",
                    "scope": "agi/task-evaluation",
                }
            return {
                "result": result,
                "local_learn": {"decision": "NO_CHANGE", "candidates": []},
                "fragment": fragment,
            }

        if component == "execute":
            return {
                "result": {
                    "status": "implemented-prototype",
                    "artifact": "minimal-continual-agi-research-loop",
                    "claim": "This is not a proven AGI; it is a concrete prototype step toward the request.",
                },
                "local_learn": {
                    "decision": "NO_CHANGE",
                    "observations": ["A broad AGI request should be decomposed into falsifiable capability milestones."],
                    "candidates": [],
                },
                "fragment": fragment,
            }

        if component == "task_evaluate":
            return {
                "result": {
                    "verdict": "PASS",
                    "evidence": ["execute artifact exists in persistent run state"],
                    "confidence": 0.9,
                },
                "local_learn": {"decision": "NO_CHANGE", "candidates": []},
                "fragment": fragment,
            }

        if component == "consolidate_episode":
            return {
                "result": {
                    "task": "AGIを作って",
                    "outcome": "prototype-step-completed",
                    "fragments_integrated": len(payload.get("fragments", [])),
                    "claims": [
                        {"text": "A prototype step was completed", "kind": "observation"},
                        {"text": "AGI itself was not proven", "kind": "observation"},
                    ],
                },
                "local_learn": {"decision": "NO_CHANGE", "candidates": []},
                "fragment": fragment,
            }

        if component == "learn":
            # Learn does not recursively Local Learn itself.
            return {
                "result": {
                    "decision": "CHANGE_PROPOSED",
                    "candidates": [
                        {
                            "candidate_id": "candidate-agi-milestone-v1",
                            "target_component": "execute",
                            "expected_scope": "agi/research-planning",
                            "prompt_content": "For AGI research tasks, decompose the goal into falsifiable capability milestones before implementation.",
                            "status": "candidate",
                        }
                    ],
                },
                "fragment": fragment,
            }

        raise AssertionError(f"unexpected component: {component}")


def test_agi_request_closes_learning_loop_without_api_key(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(engine_module, "ModelClient", FakeModelClient)
    engine = engine_module.Engine(tmp_path)

    run_id = engine.start("AGIを作って")
    run_dir = tmp_path / ".continual" / "runs" / run_id

    snapshot = json.loads((run_dir / "snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["status"] == "finished"
    assert snapshot["phase"] == "finished"
    assert snapshot.get("episode_id")

    episode_dir = tmp_path / ".continual" / "episodes" / snapshot["episode_id"]
    episode = json.loads((episode_dir / "episode.json").read_text(encoding="utf-8"))
    assert episode["task"] == "AGIを作って"
    assert episode["outcome"] == "prototype-step-completed"

    # Preflight happened before runner and semantic components.
    preflight_files = list((run_dir / "preflight").glob("*.json"))
    assert len(preflight_files) >= 1
    assert (run_dir / "preflight" / "runner.json").exists()

    # Each semantic stage persisted fragments/local learning; Learn itself need not recurse.
    assert len(list((run_dir / "fragments").glob("*.json"))) >= 6
    assert len(list((run_dir / "local-learn").glob("*.json"))) >= 5

    # Post-task Learn registered a delayed candidate rather than globally activating it.
    index = json.loads((tmp_path / ".continual" / "candidates" / "index.json").read_text(encoding="utf-8"))
    candidate = next(c for c in index["candidates"] if c["candidate_id"] == "candidate-agi-milestone-v1")
    assert candidate["status"] == "candidate"
    assert candidate["target_component"] == "execute"

    candidate_dir = tmp_path / ".continual" / "candidates" / "candidate-agi-milestone-v1"
    assert (candidate_dir / "candidate.json").exists()
    assert (candidate_dir / "prompt.md").exists()
