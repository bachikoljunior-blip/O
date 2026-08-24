from __future__ import annotations

import json
import shutil
from pathlib import Path

from continual.work_checkpoint_integrity import verify_work_checkpoint_integrity
from continual.work_session import WorkSession, submit_work_response


def _root(tmp_path: Path) -> Path:
    shutil.copytree(Path("prompts"), tmp_path / "prompts")
    return tmp_path


def _entry_output() -> dict:
    return {
        "result": {"objective": "validate one durable checkpoint"},
        "local_learn": {"decision": "NO_CHANGE", "candidates": []},
        "fragment": {"component": "entry", "observations": ["bounded fixture"]},
    }


def _completed_checkpoint(tmp_path: Path) -> tuple[Path, dict, str]:
    root = _root(tmp_path)
    run_id = "run-checkpoint-valid"
    session = WorkSession(
        root,
        executor_binding="checkpoint-session",
        model_identity="checkpoint-model",
    )
    request = session.start("create one durable checkpoint", run_id=run_id)["pending"][0]
    submit_work_response(
        root,
        request["invocation_id"],
        _entry_output(),
        executor_binding="checkpoint-session",
        model_identity="checkpoint-model",
    )
    state = {
        "active_run_id": run_id,
        "exact_continuation": {
            "run_snapshot_ref": f".continual/runs/{run_id}/snapshot.json",
            "snapshot_branch": "main",
            "snapshot_head_sha": "a" * 40,
            "pending_work_invocation_id": None,
            "pending_request_ref": None,
            "pending_native_invocation_id": None,
            "completed_work_invocation_id": request["invocation_id"],
        },
        "primary_native_run": {
            "run_id": run_id,
            "executor_binding": "checkpoint-session",
            "model_identity": "checkpoint-model",
            "answered_invocations": [f'{request["invocation_id"]} (Entry)'],
        },
    }
    return root, state, request["invocation_id"]


def _record_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted((root / ".continual").rglob("*"))
        if path.is_file()
    }


def _codes(report: dict) -> set[str]:
    return {issue["code"] for issue in report["issues"]}


def test_valid_completed_checkpoint_is_verified_and_read_only(tmp_path: Path) -> None:
    root, state, invocation_id = _completed_checkpoint(tmp_path)
    before = _record_bytes(root)

    report = verify_work_checkpoint_integrity(root, state=state)

    assert report["valid"] is True
    assert report["issues"] == []
    assert report["claim_boundary"] == (
        "this supplied checkpoint and checked repository snapshot only"
    )
    assert {item["kind"] for item in report["verified_references"]} == {
        "run_snapshot",
        "completed",
    }
    assert any(
        item.get("invocation_id") == invocation_id
        for item in report["verified_references"]
    )
    assert _record_bytes(root) == before


def test_pending_request_is_verified_without_requiring_a_response(tmp_path: Path) -> None:
    root = _root(tmp_path)
    run_id = "run-checkpoint-pending"
    request = WorkSession(
        root,
        executor_binding="checkpoint-session",
        model_identity="checkpoint-model",
    ).start("freeze one pending request", run_id=run_id)["pending"][0]
    invocation_id = request["invocation_id"]
    request_ref = (
        f".continual/work-model/invocations/{invocation_id}/request.json"
    )
    native_ref = next((root / ".continual" / "runs" / run_id / "invocations").glob("*.json"))
    native_id = json.loads(native_ref.read_text(encoding="utf-8"))["invocation_id"]
    state = {
        "active_run_id": run_id,
        "exact_continuation": {
            "run_snapshot_ref": f".continual/runs/{run_id}/snapshot.json",
            "snapshot_branch": "main",
            "snapshot_head_sha": "b" * 40,
            "pending_work_invocation_id": invocation_id,
            "pending_request_ref": request_ref,
            "pending_native_invocation_id": native_id,
            "completed_work_invocation_id": None,
        },
        "primary_native_run": {
            "run_id": run_id,
            "executor_binding": "checkpoint-session",
            "model_identity": "checkpoint-model",
            "answered_invocations": [],
        },
    }

    report = verify_work_checkpoint_integrity(root, state=state)

    assert report["valid"] is True
    assert {item["kind"] for item in report["verified_references"]} == {
        "run_snapshot",
        "pending_work_request",
        "pending_native_invocation",
    }


def test_missing_pending_request_and_claimed_response_fail_closed(tmp_path: Path) -> None:
    root, state, completed_id = _completed_checkpoint(tmp_path)
    state["exact_continuation"].update(
        {
            "pending_work_invocation_id": "invoke-111111111111111111111111",
            "pending_request_ref": ".continual/work-model/invocations/invoke-111111111111111111111111/request.json",
        }
    )
    response = (
        root
        / ".continual"
        / "work-model"
        / "invocations"
        / completed_id
        / "response.json"
    )
    response.unlink()

    report = verify_work_checkpoint_integrity(root, state=state)

    assert report["valid"] is False
    assert _codes(report) == {
        "PENDING_WORK_REQUEST_INVALID",
        "COMPLETED_WORK_INVOCATION_INVALID",
    }
    assert not (
        root
        / ".continual"
        / "work-model"
        / "invocations"
        / "invoke-111111111111111111111111"
    ).exists()


def test_malformed_and_digest_invalid_responses_are_rejected(tmp_path: Path) -> None:
    root, state, invocation_id = _completed_checkpoint(tmp_path)
    response_path = (
        root
        / ".continual"
        / "work-model"
        / "invocations"
        / invocation_id
        / "response.json"
    )
    response_path.write_text("{", encoding="utf-8")
    malformed = verify_work_checkpoint_integrity(root, state=state)
    assert malformed["valid"] is False
    assert _codes(malformed) == {"COMPLETED_WORK_INVOCATION_INVALID"}
    assert "malformed Work response" in malformed["issues"][0]["message"]

    root, state, invocation_id = _completed_checkpoint(tmp_path / "digest")
    response_path = (
        root
        / ".continual"
        / "work-model"
        / "invocations"
        / invocation_id
        / "response.json"
    )
    response = json.loads(response_path.read_text(encoding="utf-8"))
    response["model_verified"] = not response["model_verified"]
    response_path.write_text(json.dumps(response), encoding="utf-8")
    digest_invalid = verify_work_checkpoint_integrity(root, state=state)
    assert digest_invalid["valid"] is False
    assert "Work response digest mismatch" in digest_invalid["issues"][0]["message"]


def test_binding_mismatch_and_malformed_claim_are_structured(tmp_path: Path) -> None:
    root, state, invocation_id = _completed_checkpoint(tmp_path)
    state["primary_native_run"]["run_id"] = "run-different"
    state["active_run_id"] = "run-different"
    state["primary_native_run"]["answered_invocations"].append(
        f"prefix {invocation_id} suffix"
    )

    report = verify_work_checkpoint_integrity(root, state=state)

    assert report["valid"] is False
    assert _codes(report) == {
        "SNAPSHOT_RUN_MISMATCH",
        "ANSWERED_INVOCATION_CLAIM_MALFORMED",
        "WORK_RUN_BINDING_MISMATCH",
    }


def test_missing_snapshot_and_malformed_state_are_rejected(tmp_path: Path) -> None:
    root, state, _ = _completed_checkpoint(tmp_path)
    state["exact_continuation"]["run_snapshot_ref"] = (
        ".continual/runs/run-checkpoint-valid/missing.json"
    )
    missing = verify_work_checkpoint_integrity(root, state=state)
    assert "SNAPSHOT_MISSING" in _codes(missing)

    malformed_path = root / "agi" / "BROKEN_STATE.json"
    malformed_path.parent.mkdir(parents=True, exist_ok=True)
    malformed_path.write_text("[", encoding="utf-8")
    malformed = verify_work_checkpoint_integrity(
        root,
        state_path=Path("agi/BROKEN_STATE.json"),
    )
    assert malformed["valid"] is False
    assert _codes(malformed) == {"STATE_MALFORMED"}
