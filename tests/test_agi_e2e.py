from __future__ import annotations

import json
from pathlib import Path

from continual.engine import Engine
from continual.store import Store


class FakeModel:
    model = "fake-model"

    def __init__(self):
        self.calls: list[dict] = []
        self.root_calls = 0

    @staticmethod
    def _output(component: str, result: dict) -> dict:
        return {
            "result": result,
            "fragment": {"component": component, "observed": True},
            "local_learn": {"candidates": []},
        }

    def call(self, component, payload, prompt_path=None):
        self.calls.append(
            {"component": component, "payload": payload, "prompt_path": prompt_path}
        )

        if component == "candidate_evaluate":
            return self._output(
                component,
                {
                    "decision": "USE_ACTIVE",
                    "candidate_id": None,
                    "scope": payload.get("target_component"),
                },
            )

        if component == "entry":
            return self._output(
                component,
                {
                    "goal": payload["request"],
                    "constraints": [],
                    "acceptance": ["verified result"],
                },
            )

        if component == "root":
            self.root_calls += 1
            if self.root_calls == 1:
                return self._output(
                    component,
                    {
                        "component": "execute",
                        "objective": "produce the result",
                    },
                )
            return self._output(
                component,
                {
                    "component": "task_evaluate",
                    "objective": "evaluate the completed result",
                },
            )

        if component == "execute":
            return self._output(
                component,
                {
                    "status": "completed",
                    "artifact": "verified output",
                    "tests": ["passed"],
                },
            )

        if component == "task_evaluate":
            return self._output(
                component,
                {
                    "verdict": "PASS",
                    "evidence": ["artifact exists", "tests passed"],
                },
            )

        if component == "consolidate_episode":
            return self._output(
                component,
                {
                    "summary": "request completed",
                    "outcome": "PASS",
                    "reusable_lessons": ["verify before finishing"],
                },
            )

        if component == "learn":
            return self._output(
                component,
                {
                    "candidates": [
                        {
                            "candidate_id": "candidate-verify-before-finish",
                            "target_component": "execute",
                            "expected_scope": "tasks with objective acceptance checks",
                            "what_it_changes": "Run objective verification before reporting completion.",
                            "prompt_content": "Verify objective acceptance evidence before completion.",
                        }
                    ]
                },
            )

        raise AssertionError(component)


def _engine(root: Path, model: FakeModel) -> Engine:
    engine = object.__new__(Engine)
    engine.root = root.resolve()
    engine.store = Store(engine.root)
    engine.model = model
    return engine


def test_full_request_to_episode_to_candidate_flow(tmp_path: Path) -> None:
    model = FakeModel()
    engine = _engine(tmp_path, model)

    run_id = engine.start("complete a verified task")
    snapshot = engine.status(run_id)

    assert snapshot["status"] == "finished"
    assert snapshot["phase"] == "finished"
    assert model.root_calls == 2

    run_directory = engine.store.run_dir(run_id)
    invocation_files = list((run_directory / "invocations").glob("*.json"))
    assert invocation_files
    assert all(
        json.loads(path.read_text(encoding="utf-8"))["state"] == "completed"
        for path in invocation_files
    )

    episode_id = snapshot["episode_id"]
    episode_path = (
        tmp_path / ".continual" / "episodes" / episode_id / "episode.json"
    )
    episode = json.loads(episode_path.read_text(encoding="utf-8"))
    assert episode["outcome"] == "PASS"
    assert episode["run_id"] == run_id

    candidate_path = (
        tmp_path
        / ".continual"
        / "candidates"
        / "candidate-verify-before-finish"
        / "candidate.json"
    )
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert candidate["status"] == "candidate"
    assert candidate["prompt_mode"] == "overlay"
    assert (tmp_path / candidate["prompt_path"]).exists()

    relations = engine.store.read_jsonl(episode_path.parent / "relations.jsonl")
    assert any(
        relation.get("candidate_id") == "candidate-verify-before-finish"
        for relation in relations
    )

    call_count = len(model.calls)
    assert engine.resume(run_id) == "finished"
    assert len(model.calls) == call_count


def test_feedback_is_appended_to_episode_relations(tmp_path: Path) -> None:
    model = FakeModel()
    engine = _engine(tmp_path, model)
    run_id = engine.start("complete a task")
    episode_id = engine.status(run_id)["episode_id"]

    engine.feedback(episode_id, "The verification was useful.")

    relations = engine.store.read_jsonl(
        tmp_path / ".continual" / "episodes" / episode_id / "relations.jsonl"
    )
    assert relations[-1]["type"] == "user_feedback"
    assert relations[-1]["text"] == "The verification was useful."
