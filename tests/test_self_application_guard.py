from __future__ import annotations

import json
from pathlib import Path

import pytest

from continual.self_application import (
    SelfApplicationConflict,
    SelfApplicationError,
    record_self_application,
)


def _record() -> dict:
    return {
        "execution_id": "self-guard-test-001",
        "run_id": "run-self-guard-test-001",
        "episode_id": "episode-self-guard-test-001",
        "evidence_id": "evidence-self-guard-test-001",
        "request": "Record one guarded O development execution.",
        "objective": "Fail closed before malformed self-application state can mutate native records.",
        "executor": {"kind": "task_chat_model", "model_verified": False},
        "started_at": "2026-08-17T12:00:00Z",
        "completed_at": "2026-08-17T12:01:00Z",
        "verdict": "PASS",
        "context": {"boundary": "development", "risk_signals": []},
    }


def test_context_boundary_flags_require_actual_booleans_before_mutation(tmp_path: Path):
    record = _record()
    record["context"]["logical_reset_used"] = "false"

    with pytest.raises(SelfApplicationError, match="context.logical_reset_used must be a boolean"):
        record_self_application(tmp_path, record)

    assert not (tmp_path / ".continual").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_id", "../outside"),
        ("run_id", "run/escape"),
        ("episode_id", "episode escape"),
        ("evidence_id", "evidence:escape"),
    ],
)
def test_native_ids_are_validated_before_path_inspection(
    tmp_path: Path,
    field: str,
    value: str,
):
    record = _record()
    record[field] = value

    with pytest.raises(SelfApplicationError, match=f"{field} must contain only"):
        record_self_application(tmp_path, record)

    assert not (tmp_path / ".continual").exists()


def test_existing_native_run_cannot_be_overwritten_by_self_application(tmp_path: Path):
    snapshot_path = (
        tmp_path
        / ".continual"
        / "runs"
        / "run-self-guard-test-001"
        / "snapshot.json"
    )
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(
        json.dumps({"run_id": "run-self-guard-test-001", "status": "continue"}) + "\n",
        encoding="utf-8",
    )
    original = snapshot_path.read_text(encoding="utf-8")

    with pytest.raises(SelfApplicationConflict, match="already belongs to another native run"):
        record_self_application(tmp_path, _record())

    assert snapshot_path.read_text(encoding="utf-8") == original
    assert not (
        snapshot_path.parent / "invocations" / "self-application.json"
    ).exists()


@pytest.mark.parametrize(
    "kind,path",
    [
        (
            "episode",
            ".continual/episodes/episode-self-guard-test-001/episode.json",
        ),
        (
            "evidence",
            ".continual/evidence/self-application/evidence-self-guard-test-001.json",
        ),
    ],
)
def test_existing_episode_or_evidence_id_from_another_execution_fails_closed(
    tmp_path: Path,
    kind: str,
    path: str,
):
    occupied = tmp_path / path
    occupied.parent.mkdir(parents=True)
    occupied.write_text(
        json.dumps({"execution_id": "different-execution"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SelfApplicationConflict, match=f"{kind}_id already belongs"):
        record_self_application(tmp_path, _record())

    assert json.loads(occupied.read_text(encoding="utf-8"))["execution_id"] == "different-execution"
    assert not (
        tmp_path
        / ".continual"
        / "runs"
        / "run-self-guard-test-001"
        / "invocations"
        / "self-application.json"
    ).exists()
