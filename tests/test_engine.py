from __future__ import annotations

import json
from pathlib import Path

import continual.engine as engine_module


class FakeModelClient:
    def __init__(self, root: Path):
        self.root = root
        self.model = "fake-test-model"
        self.calls: list[tuple[str, str | None]] = []
        self.root_calls = 0

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        self.calls.append((component, prompt_path))
        fragment = {
            "component": component,
            "purpose": f"test {component}",
            "observations": [],
            "evidence_refs": [],
            "unresolved": [],
        }
        local = {"decision": "NO_CHANGE", "candidates": []}

        if component == "candidate_evaluate":
            candidates = payload.get("candidate_index", {}).get("candidates", [])
            if payload["mode"] == "pre-application":
                candidate = candidates[0]
                return {
                    "result": {
                        "decision": "TRIAL_CANDIDATE",
                        "candidate_id": candidate["candidate_id"],
                        "scope": payload["execution_unit"].get("scope", "test-scope"),
                    },
                    "local_learn": local,
                    "fragment": fragment,
                }
            candidate = payload["candidate"]
            return {
                "result": {
                    "decision": "ACTIVE_FOR_SCOPE",
                    "candidate_id": candidate["candidate_id"],
                    "scope": payload["execution_unit"].get("scope", "test-scope"),
                    "evidence": ["fake objective result"],
                },
                "local_learn": local,
                "fragment": fragment,
            }

        if component == "entry":
            return {
                "result": {
                    "goal": "AGIを作る",
                    "success_conditions": ["persist artifacts", "evaluate evidence"],
                },
                "local_learn": local,
                "fragment": fragment,
            }

        if component == "root":
            self.root_calls += 1
            chosen = "execute" if self.root_calls == 1 else "task_evaluate"
            return {
                "result": {
                    "component": chosen,
                    "goal": "implement milestone" if chosen == "execute" else "evaluate milestone",
                    "scope": "agi/capability-evaluation",
                },
                "local_learn": local,
                "fragment": fragment,
            }

        if component == "execute":
            return {
                "result": {"status": "implemented", "artifact": "prototype"},
                "local_learn": local,
                "fragment": fragment,
            }

        if component == "task_evaluate":
            return {
                "result": {"verdict": "PASS", "evidence": ["artifact persisted"]},
                "local_learn": local,
                "fragment": fragment,
            }

        if component == "consolidate_episode":
            return {
                "result": {
                    "task": "AGIを作って",
                    "outcome": "development-milestone-completed",
                    "unresolved": ["claim-grade AGI evidence is not complete"],
                },
                "local_learn": local,
                "fragment": fragment,
            }

        if component == "learn":
            return {
                "result": {
                    "decision": "CHANGE_PROPOSED",
                    "candidates": [
                        {
                            "candidate_id": "candidate-test-v1",
                            "target_component": "execute",
                            "expected_scope": "agi/research-planning",
                            "prompt_content": "Decompose AGI work into falsifiable capability experiments.",
                            "prompt_mode": "overlay",
                            "supporting_evidence": ["episode observed"],
                        }
                    ],
                },
                "fragment": fragment,
            }

        raise AssertionError(f"unexpected component: {component}")


def make_engine(runtime_repo: Path, monkeypatch) -> engine_module.Engine:
    monkeypatch.setattr(engine_module, "ModelClient", FakeModelClient)
    return engine_module.Engine(runtime_repo)


def test_task_closes_episode_and_learning_loop(runtime_repo: Path, monkeypatch):
    engine = make_engine(runtime_repo, monkeypatch)
    run_id = engine.start("AGIを作って")
    snapshot = engine.store.snapshot(run_id)

    assert snapshot["status"] == "finished"
    assert snapshot["phase"] == "finished"
    episode = json.loads(
        (runtime_repo / ".continual" / "episodes" / snapshot["episode_id"] / "episode.json").read_text()
    )
    assert episode["outcome"] == "development-milestone-completed"
    assert (runtime_repo / ".continual" / "candidates" / "candidate-test-v1" / "prompt.md").exists()


def test_step_budget_checkpoints_without_semantic_failure(runtime_repo: Path, monkeypatch):
    engine = make_engine(runtime_repo, monkeypatch)
    run_id = engine.start("AGIを作って", max_steps=1)
    snapshot = engine.store.snapshot(run_id)
    assert snapshot["status"] == "continue"
    assert snapshot["checkpoint_reason"] == "step_budget_exhausted"
    assert snapshot["phase"] == "root_pending"


def test_completed_invocation_is_reused(runtime_repo: Path, monkeypatch):
    engine = make_engine(runtime_repo, monkeypatch)
    run_id = engine.store.new_id("run")
    run_dir = engine.store.run_dir(run_id)
    run_dir.mkdir(parents=True)
    engine.store.atomic_json(run_dir / "snapshot.json", {"revision": 0, "status": "continue"})
    payload = {"request": "same"}
    prompt = engine._component_prompt_path("entry")

    first, _ = engine._call_component_direct(run_id, "entry", payload, prompt_path=prompt)
    second, _ = engine._call_component_direct(run_id, "entry", payload, prompt_path=prompt)

    assert first == second
    assert [name for name, _ in engine.model.calls].count("entry") == 1


def test_candidate_preflight_is_target_filtered_and_overlay_keeps_base_prompt(
    runtime_repo: Path, monkeypatch
):
    engine = make_engine(runtime_repo, monkeypatch)
    execute_dir = runtime_repo / ".continual" / "candidates" / "candidate-execute"
    root_dir = runtime_repo / ".continual" / "candidates" / "candidate-root"
    execute_dir.mkdir(parents=True)
    root_dir.mkdir(parents=True)
    (execute_dir / "prompt.md").write_text("EXECUTE OVERLAY", encoding="utf-8")
    (root_dir / "prompt.md").write_text("ROOT OVERLAY", encoding="utf-8")
    execute = {
        "candidate_id": "candidate-execute",
        "target_component": "execute",
        "prompt_path": ".continual/candidates/candidate-execute/prompt.md",
        "prompt_mode": "overlay",
        "status": "candidate",
    }
    root = {
        "candidate_id": "candidate-root",
        "target_component": "root",
        "prompt_path": ".continual/candidates/candidate-root/prompt.md",
        "prompt_mode": "overlay",
        "status": "candidate",
    }
    engine.store.atomic_json(execute_dir / "candidate.json", execute)
    engine.store.atomic_json(root_dir / "candidate.json", root)
    engine.store.atomic_json(
        engine.candidate_index_path,
        {"schema_version": 2, "candidates": [execute, root]},
    )
    assert [item["candidate_id"] for item in engine._candidate_index_for("execute")["candidates"]] == [
        "candidate-execute"
    ]

    run_id = engine.store.new_id("run")
    run_dir = engine.store.run_dir(run_id)
    run_dir.mkdir(parents=True)
    engine.store.atomic_json(run_dir / "snapshot.json", {"revision": 0, "status": "continue"})
    selected = engine._effective_prompt_path(run_id, "execute", execute)
    text = (runtime_repo / selected).read_text(encoding="utf-8")
    assert "# EXECUTE" in text
    assert "EXECUTE OVERLAY" in text


def test_repeated_candidate_proposal_merges_evidence(runtime_repo: Path, monkeypatch):
    engine = make_engine(runtime_repo, monkeypatch)
    source = {
        "candidates": [
            {
                "candidate_id": "candidate-merge",
                "target_component": "execute",
                "prompt_content": "overlay",
                "supporting_evidence": ["one"],
            }
        ]
    }
    engine._register_candidates(source, "first.json")
    source["candidates"][0]["supporting_evidence"] = ["two"]
    engine._register_candidates(source, "second.json")
    candidate = engine._candidate_by_id("candidate-merge")
    assert candidate is not None
    assert candidate["supporting_evidence"] == ["one", "two"]
    assert candidate["source_refs"] == ["first.json", "second.json"]
