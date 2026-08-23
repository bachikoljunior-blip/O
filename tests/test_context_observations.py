from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from continual.context_observations import (
    ContextObservationError,
    observation_ledger_entry,
    prepare_context_observation,
    record_context_observation_receipt,
    verify_context_observation,
    verify_context_observation_ledger,
)
from continual.work_session import WorkSession, submit_work_response


RUN_ID = "run-context-observation-test"
OBSERVATION_ID = "github-state-v1"
COMMIT_SHA = "a" * 40
BLOB_SHA = "b" * 40


def _component_output(component: str) -> dict:
    result = (
        {"objective": "observe one public source"}
        if component == "entry"
        else {"component": "execute", "goal": "observe one exact GitHub file"}
    )
    return {
        "result": result,
        "local_learn": {"decision": "NO_CHANGE", "candidates": []},
        "fragment": {"component": component, "observations": ["fixture"]},
    }


def _pending_execute(tmp_path: Path) -> tuple[Path, dict]:
    shutil.copytree(Path("prompts"), tmp_path / "prompts")
    session = WorkSession(
        tmp_path,
        executor_binding="session-a",
        model_identity="model-a",
    )
    state = session.start("observe one source", run_id=RUN_ID)
    for component in ("entry", "root"):
        request = state["pending"][0]
        assert request["component"] == component
        submit_work_response(
            tmp_path,
            request["invocation_id"],
            _component_output(component),
            executor_binding="session-a",
            model_identity="model-a",
        )
        state = session.resume(RUN_ID)
    request = state["pending"][0]
    assert request["component"] == "execute"
    return tmp_path, request


def _source(commit_sha: str = COMMIT_SHA) -> dict:
    return {
        "kind": "github_file",
        "repository_full_name": "owner/repo",
        "path": "agi/WORK_EXECUTION_STATE.json",
        "ref": commit_sha,
        "expected_commit_sha": commit_sha,
    }


def _prepare(root: Path, request: dict, *, freshness: dict | None = None) -> dict:
    return prepare_context_observation(
        root,
        run_id=RUN_ID,
        observation_id=OBSERVATION_ID,
        invocation_id=request["invocation_id"],
        executor_binding="session-a",
        model_identity="model-a",
        source=_source(),
        selected_fields=["status", "lease_generation"],
        freshness=freshness
        or {
            "kind": "immutable_version",
            "invalidates_on": ["commit identity mismatch"],
        },
    )


def _receipt(root: Path, prepared: dict, *, observed_at: str = "2026-08-23T00:00:00Z") -> dict:
    return record_context_observation_receipt(
        root,
        run_id=RUN_ID,
        observation_id=OBSERVATION_ID,
        request_digest=prepared["request_digest"],
        executor_binding="session-a",
        model_identity="model-a",
        source_version={"commit_sha": COMMIT_SHA, "blob_sha": BLOB_SHA},
        projection={"status": "running", "lease_generation": 6},
        observed_at=observed_at,
    )


def test_request_precedes_receipt_and_exact_replay_is_immutable(tmp_path: Path) -> None:
    root, execute = _pending_execute(tmp_path)
    prepared = _prepare(root, execute)
    replay = _prepare(root, execute)
    assert replay == prepared

    receipt = _receipt(root, prepared)
    verified = verify_context_observation(
        root, run_id=RUN_ID, observation_id=OBSERVATION_ID
    )
    assert verified["request"]["request_digest"] == prepared["request_digest"]
    assert verified["receipt"]["receipt_digest"] == receipt["receipt_digest"]
    assert verified["receipt"]["projection"] == {
        "status": "running",
        "lease_generation": 6,
    }

    exact_replay = _receipt(root, prepared)
    assert exact_replay == receipt
    with pytest.raises(ContextObservationError, match="receipt conflict"):
        record_context_observation_receipt(
            root,
            run_id=RUN_ID,
            observation_id=OBSERVATION_ID,
            request_digest=prepared["request_digest"],
            executor_binding="session-a",
            model_identity="model-a",
            source_version={"commit_sha": COMMIT_SHA, "blob_sha": "c" * 40},
            projection={"status": "running", "lease_generation": 6},
            observed_at="2026-08-23T00:00:00Z",
        )


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({"request_digest": "f" * 64}, "request identity mismatch"),
        ({"executor_binding": "session-b"}, "request identity mismatch"),
        ({"model_identity": "model-b"}, "request identity mismatch"),
        (
            {"source_version": {"commit_sha": "d" * 40, "blob_sha": BLOB_SHA}},
            "source commit mismatch",
        ),
        ({"projection": {"status": "running"}}, "projection field set mismatch"),
    ],
)
def test_unsolicited_or_mismatched_receipt_fails_closed(
    tmp_path: Path, mutation: dict, match: str
) -> None:
    root, execute = _pending_execute(tmp_path)
    prepared = _prepare(root, execute)
    kwargs = {
        "run_id": RUN_ID,
        "observation_id": OBSERVATION_ID,
        "request_digest": prepared["request_digest"],
        "executor_binding": "session-a",
        "model_identity": "model-a",
        "source_version": {"commit_sha": COMMIT_SHA, "blob_sha": BLOB_SHA},
        "projection": {"status": "running", "lease_generation": 6},
        "observed_at": "2026-08-23T00:00:00Z",
    }
    kwargs.update(mutation)
    with pytest.raises(ContextObservationError, match=match):
        record_context_observation_receipt(root, **kwargs)

    other = tmp_path / "unrequested"
    other.mkdir()
    with pytest.raises(ContextObservationError, match="malformed context observation request"):
        record_context_observation_receipt(other, **kwargs)


def test_tamper_and_stale_receipt_fail_closed(tmp_path: Path) -> None:
    root, execute = _pending_execute(tmp_path)
    prepared = _prepare(
        root,
        execute,
        freshness={
            "kind": "max_age",
            "max_age_seconds": 60,
            "invalidates_on": ["age exceeds bound", "commit identity mismatch"],
        },
    )
    _receipt(root, prepared, observed_at="2026-08-23T00:00:00Z")
    with pytest.raises(ContextObservationError, match="stale"):
        verify_context_observation(
            root,
            run_id=RUN_ID,
            observation_id=OBSERVATION_ID,
            now="2026-08-23T00:01:01Z",
        )

    receipt_path = (
        root
        / ".continual"
        / "runs"
        / RUN_ID
        / "context-observations"
        / OBSERVATION_ID
        / "receipt.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["projection"]["lease_generation"] = 99
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ContextObservationError, match="tampered"):
        verify_context_observation(
            root,
            run_id=RUN_ID,
            observation_id=OBSERVATION_ID,
            enforce_freshness=False,
        )


def test_ledger_is_minimal_cross_bound_and_rejects_outer_fact(tmp_path: Path) -> None:
    root, execute = _pending_execute(tmp_path)
    prepared = _prepare(root, execute)
    _receipt(root, prepared)
    entry = observation_ledger_entry(
        root,
        run_id=RUN_ID,
        observation_id=OBSERVATION_ID,
        source_id="github_work_state",
    )
    entry["run_id"] = RUN_ID
    ledger = {"schema_version": 1, "entries": [entry]}
    assert verify_context_observation_ledger(root, ledger) == [entry]
    assert "raw_response" not in json.dumps(entry)

    injected = deepcopy(ledger)
    injected["entries"][0]["projection"]["outer_untracked_fact"] = True
    with pytest.raises(ContextObservationError, match="ledger binding mismatch"):
        verify_context_observation_ledger(root, injected)

    changed = deepcopy(ledger)
    changed["entries"][0]["source_version"]["blob_sha"] = "e" * 40
    with pytest.raises(ContextObservationError, match="ledger binding mismatch"):
        verify_context_observation_ledger(root, changed)
