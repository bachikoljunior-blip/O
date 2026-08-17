from __future__ import annotations

import json
from pathlib import Path

import pytest

from continual.self_application import (
    SelfApplicationConflict,
    SelfApplicationError,
    decide_context_boundary,
    record_self_application,
)


def _record() -> dict:
    return {
        "execution_id": "self-test-001",
        "run_id": "run-self-test-001",
        "episode_id": "episode-self-test-001",
        "evidence_id": "evidence-self-test-001",
        "request": "Develop O by applying O's own execution and learning record format.",
        "objective": "Persist repository development as a native continual-learning episode.",
        "scope": "agi/repository-development",
        "executor": {
            "kind": "task_chat_model",
            "model": "test-model",
            "model_verified": False,
        },
        "started_at": "2026-08-17T12:00:00Z",
        "completed_at": "2026-08-17T12:05:00Z",
        "verdict": "PASS",
        "outcome": "completed",
        "success_conditions": [
            "Create native Run, Episode, Candidate and evidence records.",
            "Keep self-produced evidence outside the external AGI claim gate.",
        ],
        "actions": ["Implemented a thin self-application recording adapter."],
        "decisions": ["Reuse current context for ordinary development without contamination signals."],
        "observations": ["The repository is the durable source of continuation state."],
        "failures": [],
        "unknowns": ["Independent production evidence remains unavailable."],
        "artifacts": ["src/continual/self_application.py", "tests/test_self_application.py"],
        "validation": [{"kind": "pytest", "status": "passed"}],
        "claims": [
            {
                "text": "The self-application record is internal development evidence.",
                "kind": "evaluation",
            }
        ],
        "unresolved": ["Obtain independent production-tier AGI evidence."],
        "context": {"boundary": "development", "risk_signals": []},
        "repository": {"base_sha": "abc", "head_sha": "def"},
        "candidate_proposal": {
            "candidate_id": "candidate-self-context-hygiene-v1",
            "target_component": "execute",
            "expected_scope": "agi/repository-development",
            "prompt_mode": "overlay",
            "what_it_changes": "Persist task-chat development outcomes and select a contamination-safe context boundary.",
            "prompt_content": (
                "For O repository development, load latest main as durable state. "
                "Persist outcomes without hidden reasoning. Reuse context only when no contamination risk exists."
            ),
            "applicability": ["O repository development"],
            "non_applicability": ["Independent external evaluation"],
            "depends_on": [],
            "conflicts_with": [],
        },
    }


def test_record_self_application_writes_native_artifacts_and_inactive_candidate(tmp_path: Path):
    result = record_self_application(tmp_path, _record())

    assert result["run_id"] == "run-self-test-001"
    assert result["episode_id"] == "episode-self-test-001"
    assert result["candidate_ids"] == ["candidate-self-context-hygiene-v1"]
    assert result["context_decision"]["mode"] == "reuse_current_context"
    assert result["replayed"] is False

    snapshot = json.loads(
        (tmp_path / ".continual/runs/run-self-test-001/snapshot.json").read_text(encoding="utf-8")
    )
    assert snapshot["execution_mode"] == "task-chat-model-self-application"
    assert snapshot["task_verdict"] == "PASS"
    assert snapshot["external_claim_admissible"] is False

    events = [
        json.loads(line)
        for line in (tmp_path / ".continual/runs/run-self-test-001/events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [event["type"] for event in events] == [
        "run_started",
        "context_boundary_selected",
        "execute_complete",
        "task_evaluate",
        "consolidate_episode",
        "post_task_learn",
        "run_finished",
    ]

    episode = json.loads(
        (tmp_path / ".continual/episodes/episode-self-test-001/episode.json").read_text(
            encoding="utf-8"
        )
    )
    assert episode["source_run_id"] == "run-self-test-001"
    assert episode["candidate_refs"] == ["candidate-self-context-hygiene-v1"]
    assert episode["external_claim_admissible"] is False

    evidence = json.loads(
        (
            tmp_path
            / ".continual/evidence/self-application/evidence-self-test-001.json"
        ).read_text(encoding="utf-8")
    )
    assert evidence["evidence_tier"] == "internal_development"
    assert evidence["admissible_for_agi_claim"] is False
    assert evidence["strict_external_gate_unchanged"] is True

    candidate = json.loads(
        (
            tmp_path
            / ".continual/candidates/candidate-self-context-hygiene-v1/candidate.json"
        ).read_text(encoding="utf-8")
    )
    assert candidate["status"] == "candidate"
    assert candidate["scope_states"]["agi/repository-development"] == "candidate"
    assert candidate["scope_states"]["global"] == "untested"
    assert candidate["prompt_path"].endswith("/prompt.md")

    index = json.loads(
        (tmp_path / ".continual/candidates/index.json").read_text(encoding="utf-8")
    )
    assert [item["candidate_id"] for item in index["candidates"]] == [
        "candidate-self-context-hygiene-v1"
    ]


def test_self_application_replay_is_idempotent_and_conflicting_reuse_fails(tmp_path: Path):
    first = record_self_application(tmp_path, _record())
    event_path = tmp_path / ".continual/runs/run-self-test-001/events.jsonl"
    original_events = event_path.read_text(encoding="utf-8")

    second = record_self_application(tmp_path, _record())

    assert first["payload_digest"] == second["payload_digest"]
    assert second["replayed"] is True
    assert event_path.read_text(encoding="utf-8") == original_events

    changed = _record()
    changed["observations"].append("Different durable result under the same execution id.")
    with pytest.raises(SelfApplicationConflict):
        record_self_application(tmp_path, changed)


def test_context_boundary_uses_freshness_only_when_risk_or_validation_requires_it():
    ordinary = decide_context_boundary("development", [])
    assert ordinary.mode == "reuse_current_context"
    assert ordinary.logical_reset_required is False
    assert ordinary.fresh_execution_required is False

    degraded = decide_context_boundary(
        "development",
        ["context_accumulation_degradation", "duplicate_search_risk"],
    )
    assert degraded.mode == "logical_reset_required"
    assert degraded.logical_reset_required is True
    assert degraded.fresh_execution_required is False
    assert degraded.persist_before_switch is True

    retention = decide_context_boundary("retention_validation", [])
    assert retention.mode == "fresh_execution_required"
    assert retention.fresh_execution_required is True
    assert retention.independent_execution_required is False

    independent = decide_context_boundary("independent_evaluation", [])
    assert independent.mode == "independent_execution_required"
    assert independent.fresh_execution_required is True
    assert independent.independent_execution_required is True


def test_self_application_rejects_hidden_reasoning_and_secret_like_material(tmp_path: Path):
    hidden = _record()
    hidden["hidden_reasoning"] = "private scratch work"
    with pytest.raises(SelfApplicationError):
        record_self_application(tmp_path, hidden)

    secret = _record()
    secret["observations"] = ["Bearer abcdefghijklmnopqrstuvwxyz0123456789"]
    with pytest.raises(SelfApplicationError):
        record_self_application(tmp_path, secret)

    assert not (tmp_path / ".continual").exists()


def test_self_application_can_finish_without_proposing_a_candidate(tmp_path: Path):
    record = _record()
    record.pop("candidate_proposal")
    record["execution_id"] = "self-test-002"
    record["run_id"] = "run-self-test-002"
    record["episode_id"] = "episode-self-test-002"
    record["evidence_id"] = "evidence-self-test-002"

    result = record_self_application(tmp_path, record)

    assert result["candidate_ids"] == []
    learn = json.loads(
        (
            tmp_path
            / ".continual/runs/run-self-test-002/local-learn/self-application-execute.json"
        ).read_text(encoding="utf-8")
    )
    assert learn["decision"] == "NO_CHANGE"


def test_pass_requires_the_selected_contamination_boundary_to_be_satisfied(tmp_path: Path):
    record = _record()
    record["execution_id"] = "self-test-003"
    record["run_id"] = "run-self-test-003"
    record["episode_id"] = "episode-self-test-003"
    record["evidence_id"] = "evidence-self-test-003"
    record["context"] = {"boundary": "retention_validation", "risk_signals": []}

    with pytest.raises(SelfApplicationError):
        record_self_application(tmp_path, record)

    record["context"]["logical_reset_used"] = True
    record["context"]["fresh_execution_used"] = True
    result = record_self_application(tmp_path, record)
    assert result["context_decision"]["mode"] == "fresh_execution_required"

    snapshot = json.loads(
        (tmp_path / ".continual/runs/run-self-test-003/snapshot.json").read_text(
            encoding="utf-8"
        )
    )
    assert snapshot["context"]["boundary_satisfied"] is True
