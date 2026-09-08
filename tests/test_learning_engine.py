from __future__ import annotations

import json
from pathlib import Path

import pytest

import continual.engine as engine_module
from agi.execution_learning import EXECUTION_SCOPE, run_learned_tool_execution_campaign
from continual.candidate_regression import CandidateIntegrityError, record_candidate_regression
from continual.learned_tools import LearnedToolError
from continual.learning_engine import LearningEnabledEngine


class RecordingModelClient:
    def __init__(self, root: Path):
        self.root = root
        self.model = "recording-test-model"
        self.calls: list[tuple[str, dict, str | None]] = []

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        self.calls.append((component, payload, prompt_path))
        local = {"decision": "NO_CHANGE", "candidates": []}
        fragment = {
            "component": component,
            "purpose": "test",
            "observations": [],
            "evidence_refs": [],
            "unresolved": [],
        }
        if component == "root":
            return {
                "result": {
                    "component": "execute",
                    "goal": "use a verified learned tool when useful",
                    "scope": EXECUTION_SCOPE,
                },
                "local_learn": local,
                "fragment": fragment,
            }
        if component == "execute":
            return {
                "result": {"status": "implemented", "kind": "model-fallback"},
                "local_learn": local,
                "fragment": fragment,
            }
        raise AssertionError(f"unexpected model call: {component}")


def _engine(runtime_repo: Path, monkeypatch) -> LearningEnabledEngine:
    monkeypatch.setattr(engine_module, "ModelClient", RecordingModelClient)
    return LearningEnabledEngine(runtime_repo)


def _new_run(engine: LearningEnabledEngine) -> str:
    run_id = engine.store.new_id("run")
    run_dir = engine.store.run_dir(run_id)
    run_dir.mkdir(parents=True)
    engine.store.atomic_json(
        run_dir / "snapshot.json",
        {"revision": 0, "status": "continue", "phase": "unit_pending"},
    )
    return run_id


def _measurement(task: str, repeat: int, score: float) -> dict:
    return {
        "task_id": task,
        "criterion": "transfer",
        "domain": "prompt-candidate",
        "repeat_index": repeat,
        "passed": True,
        "score": score,
        "artifact_sha256": f"{repeat + 11:02x}" * 32,
    }


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _prompt_candidate(runtime_repo: Path) -> dict:
    candidate_id = "candidate-prompt-integrity"
    scope = "agi/prompt-integrity-test"
    candidate_dir = runtime_repo / ".continual" / "candidates" / candidate_id
    candidate_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = candidate_dir / "prompt.md"
    prompt_path.write_text("Use falsifiable steps only.\n", encoding="utf-8")
    candidate = {
        "candidate_id": candidate_id,
        "target_component": "execute",
        "expected_scope": scope,
        "prompt_path": prompt_path.relative_to(runtime_repo).as_posix(),
        "prompt_mode": "overlay",
        "what_it_changes": "Adds a falsifiability reminder to one exact scope.",
        "status": "candidate",
        "scope_states": {scope: "candidate"},
        "verified_scope_states": {},
        "supporting_evidence": [],
        "contradictory_evidence": [],
        "regression_decision_refs": [],
    }
    _write_json(candidate_dir / "candidate.json", candidate)
    return candidate


def _promote_prompt_candidate(runtime_repo: Path, candidate: dict) -> dict:
    baseline = runtime_repo / "baseline-prompt.json"
    trial = runtime_repo / "trial-prompt.json"
    _write_json(
        baseline,
        [
            _measurement("target-prompt", 0, 0.2),
            _measurement("target-prompt", 1, 0.2),
            _measurement("protected-prompt", 0, 0.9),
        ],
    )
    _write_json(
        trial,
        [
            _measurement("target-prompt", 0, 0.8),
            _measurement("target-prompt", 1, 0.8),
            _measurement("protected-prompt", 0, 0.9),
        ],
    )
    return record_candidate_regression(
        runtime_repo,
        candidate_id=candidate["candidate_id"],
        scope=candidate["expected_scope"],
        baseline_path=baseline,
        candidate_path=trial,
        target_task_ids=["target-prompt"],
    )


def test_root_receives_only_verified_learned_tool_descriptors(runtime_repo: Path, monkeypatch) -> None:
    report = run_learned_tool_execution_campaign(runtime_repo, "root-catalog-seed")
    engine = _engine(runtime_repo, monkeypatch)
    run_id = _new_run(engine)

    output = engine._invoke(run_id, "root", {"snapshot": {"revision": 0}})

    assert output["result"]["component"] == "execute"
    root_calls = [payload for component, payload, _ in engine.model.calls if component == "root"]
    assert len(root_calls) == 1
    catalog = root_calls[0]["verified_learned_tools"]
    assert len(catalog) == 1
    assert catalog[0]["tool_id"] == report["tool_id"]
    assert catalog[0]["scope"] == EXECUTION_SCOPE
    assert "expanded_primitives" not in catalog[0]


def test_verified_tool_call_executes_mechanically_and_is_journal_reused(
    runtime_repo: Path, monkeypatch
) -> None:
    report = run_learned_tool_execution_campaign(runtime_repo, "engine-execution-seed")
    engine = _engine(runtime_repo, monkeypatch)
    run_id = _new_run(engine)
    unit = {
        "goal": "apply acquired transform",
        "scope": EXECUTION_SCOPE,
        "learned_tool_call": {
            "tool_id": report["tool_id"],
            "input": "FreshInput",
        },
    }
    payload = {"snapshot": {"revision": 0}, "execution_unit": unit}

    first = engine._invoke(run_id, "execute", payload)
    second = engine._invoke(run_id, "execute", payload)

    assert first == second
    assert first["result"]["execution_kind"] == "verified_learned_tool"
    assert first["result"]["output"] == "tupnIhserF#tupnIhserF#"
    assert [component for component, _, _ in engine.model.calls if component == "execute"] == []
    invocations = list((engine.store.run_dir(run_id) / "invocations").glob("invoke-learned-tool-*.json"))
    assert len(invocations) == 1
    events = (engine.store.run_dir(run_id) / "events.jsonl").read_text(encoding="utf-8")
    assert "learned_tool_invocation_completed" in events
    assert "learned_tool_invocation_reused" in events


def test_wrong_scope_cannot_call_verified_tool(runtime_repo: Path, monkeypatch) -> None:
    report = run_learned_tool_execution_campaign(runtime_repo, "wrong-scope-seed")
    engine = _engine(runtime_repo, monkeypatch)
    run_id = _new_run(engine)

    with pytest.raises(LearnedToolError, match="not verified"):
        engine._invoke(
            run_id,
            "execute",
            {
                "snapshot": {"revision": 0},
                "execution_unit": {
                    "goal": "attempt wrong scope",
                    "scope": "agi/other-scope",
                    "learned_tool_call": {
                        "tool_id": report["tool_id"],
                        "input": "abc",
                    },
                },
            },
        )


def test_explicit_call_scope_must_match_execution_unit(runtime_repo: Path, monkeypatch) -> None:
    report = run_learned_tool_execution_campaign(runtime_repo, "mismatch-seed")
    engine = _engine(runtime_repo, monkeypatch)
    run_id = _new_run(engine)

    with pytest.raises(LearnedToolError, match="exactly match"):
        engine._invoke(
            run_id,
            "execute",
            {
                "snapshot": {"revision": 0},
                "execution_unit": {
                    "goal": "attempt scope mismatch",
                    "scope": EXECUTION_SCOPE,
                    "learned_tool_call": {
                        "scope": "agi/other-scope",
                        "tool_id": report["tool_id"],
                        "input": "abc",
                    },
                },
            },
        )


def test_semantic_evaluator_cannot_self_promote_candidate(runtime_repo: Path, monkeypatch) -> None:
    candidate = _prompt_candidate(runtime_repo)
    engine = _engine(runtime_repo, monkeypatch)

    engine._apply_scope_update(
        {
            "result": {
                "decision": "ACTIVE_FOR_SCOPE",
                "candidate_id": candidate["candidate_id"],
                "scope": candidate["expected_scope"],
                "evidence": ["model says the trial was useful"],
            }
        }
    )

    state = json.loads(
        (
            runtime_repo
            / ".continual"
            / "candidates"
            / candidate["candidate_id"]
            / "candidate.json"
        ).read_text(encoding="utf-8")
    )
    assert state["status"] == "candidate"
    assert state["scope_states"][candidate["expected_scope"]] == "candidate"
    assert state["verified_scope_states"] == {}
    recommendation = state["supporting_evidence"][-1]
    assert recommendation["type"] == "semantic_candidate_recommendation"
    assert recommendation["recommended_state"] == "ACTIVE_FOR_SCOPE"
    assert "deterministic regression is required" in recommendation["note"]


def test_persistent_candidate_use_requires_real_bound_regression_record(
    runtime_repo: Path, monkeypatch
) -> None:
    candidate = _prompt_candidate(runtime_repo)
    engine = _engine(runtime_repo, monkeypatch)
    trial_selection = {
        "result": {
            "decision": "TRIAL_CANDIDATE",
            "candidate_id": candidate["candidate_id"],
            "scope": candidate["expected_scope"],
        }
    }
    persistent_selection = {
        "result": {
            "decision": "USE_CANDIDATE",
            "candidate_id": candidate["candidate_id"],
            "scope": candidate["expected_scope"],
        }
    }

    assert engine._selected_candidate(trial_selection, "execute") is not None
    assert engine._selected_candidate(persistent_selection, "execute") is None

    report = _promote_prompt_candidate(runtime_repo, candidate)
    assert report["decision"]["adopt_candidate"] is True
    selected = engine._selected_candidate(persistent_selection, "execute")
    assert selected is not None
    assert selected["candidate_id"] == candidate["candidate_id"]

    prompt_path = runtime_repo / candidate["prompt_path"]
    prompt_path.write_text("Silently changed after approval.\n", encoding="utf-8")
    with pytest.raises(CandidateIntegrityError, match="artifact changed"):
        engine._selected_candidate(persistent_selection, "execute")


def test_fabricated_verified_fields_without_decision_record_are_rejected(
    runtime_repo: Path, monkeypatch
) -> None:
    candidate = _prompt_candidate(runtime_repo)
    state_path = (
        runtime_repo
        / ".continual"
        / "candidates"
        / candidate["candidate_id"]
        / "candidate.json"
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    scope = candidate["expected_scope"]
    state["status"] = "active-for-scope"
    state["scope_states"][scope] = "VERIFIED_FOR_SCOPE"
    state["verified_scope_states"] = {
        scope: {
            "state": "VERIFIED_FOR_SCOPE",
            "candidate_spec_sha256": "a" * 64,
            "candidate_integrity_sha256": "b" * 64,
            "regression_evidence_sha256": "c" * 64,
            "decision_ref": (
                f".continual/candidates/{candidate['candidate_id']}/regression/fabricated.json"
            ),
        }
    }
    _write_json(state_path, state)
    engine = _engine(runtime_repo, monkeypatch)
    selection = {
        "result": {
            "decision": "USE_CANDIDATE",
            "candidate_id": candidate["candidate_id"],
            "scope": scope,
        }
    }

    with pytest.raises(CandidateIntegrityError):
        engine._selected_candidate(selection, "execute")
