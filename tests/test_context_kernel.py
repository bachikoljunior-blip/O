from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from continual.context_kernel import verify_decision_context_manifest
from continual.store import Store
from continual.work_session import WorkModelClient, WorkModelPending, WorkSessionError


RUN_ID = "run-context-kernel-test"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _root(tmp_path: Path) -> tuple[Path, dict]:
    shutil.copytree(Path("prompts"), tmp_path / "prompts")
    inbox = {
        "schema_version": 1,
        "revision": 1,
        "entries": [
            {
                "sequence": 1,
                "id": "direction-context-v1",
                "kind": "user_direction",
                "status": "active",
                "summary": "Put decision context under O control.",
                "directives": [
                    "An outside-known constraint must not silently disappear from O."
                ],
                "supersedes": [],
            }
        ],
        "updated_at": "2026-08-23T00:00:00Z",
    }
    state = {
        "schema_version": 1,
        "mode": "work_o_engine_single_writer",
        "status": "running",
        "owner_kind": "work_primary",
        "execution_id": "work-context-kernel-test",
        "lease_generation": 3,
        "fence_token": "opaque-fence-must-not-be-copied",
        "heartbeat_at": "2026-08-23T00:00:01Z",
        "stale_after_seconds": 900,
        "active_run_id": RUN_ID,
        "user_input_inbox": {
            "path": "agi/USER_INPUT_INBOX.json",
            "highest_acknowledged_revision": 1,
            "application_note": "Context direction is active; this owner is sole writer.",
        },
        "result_publication_policy": {
            "destination": "main",
            "rule": "isolated branch then exact-head CI",
        },
        "primary_run_contract": {
            "normal_completion_condition": "actual user objective or explicit stop"
        },
    }
    strategy = {
        "schema_version": 1,
        "optimization_objective": "Minimize elapsed time to the actual objective.",
        "execution_rules": {"single_writer": True},
        "claim_boundary": {"agi_claim_supported": False},
        "immediate_sequence": ["Build the Root manifest slice."],
        "context_management": {
            "decision_authority": "O Engine",
            "raw_authority": "source systems",
        },
        "updated_at": "2026-08-23T00:00:02Z",
    }
    snapshot = {
        "run_id": RUN_ID,
        "revision": 7,
        "status": "continue",
        "phase": "root_pending",
        "current_component": "task_evaluate",
        "current_unit": "unit-before-context",
        "task_completion_verdict": "FAIL",
        "unit_completion_verdict": "PASS",
        "last_result_ref": "artifacts/previous.json",
        "updated_at": "2026-08-23T00:00:03Z",
    }
    _write_json(tmp_path / "agi" / "USER_INPUT_INBOX.json", inbox)
    _write_json(tmp_path / "agi" / "WORK_EXECUTION_STATE.json", state)
    _write_json(tmp_path / "agi" / "WORK_STRATEGY.json", strategy)
    _write_json(
        tmp_path / ".continual" / "runs" / RUN_ID / "snapshot.json", snapshot
    )
    return tmp_path, snapshot


def _client(root: Path) -> WorkModelClient:
    return WorkModelClient(
        root,
        run_id=RUN_ID,
        executor_binding="context-kernel-test-session",
        model_identity="context-kernel-test-model",
    )


def _freeze_root(client: WorkModelClient, snapshot: dict) -> tuple[dict, bytes]:
    with pytest.raises(WorkModelPending) as pending:
        client.call(
            "root",
            {"snapshot": deepcopy(snapshot), "last_result": {"verdict": "FAIL"}},
            prompt_path="prompts/root.md",
        )
    path = (
        client.invocation_root / pending.value.invocation_id / "request.json"
    )
    return json.loads(path.read_text(encoding="utf-8")), path.read_bytes()


def test_root_manifest_is_deterministic_minimal_and_o_owned(tmp_path: Path) -> None:
    root, snapshot = _root(tmp_path)
    client = _client(root)

    request, before = _freeze_root(client, snapshot)
    manifest = request["payload"]["decision_context"]
    verified = verify_decision_context_manifest(manifest, store=Store(root))
    assert verified == manifest
    assert manifest["policy"]["decision_authority"] == "O Engine"
    assert manifest["policy"]["copy_all_raw_context"] is False
    assert [source["source_id"] for source in manifest["sources"]] == [
        "work_execution_state",
        "user_input_inbox",
        "work_strategy",
        "native_run_snapshot",
    ]
    request_text = json.dumps(request, ensure_ascii=False)
    assert "opaque-fence-must-not-be-copied" not in request_text
    assert "An outside-known constraint must not silently disappear from O." in request_text
    assert "outer_session_untracked_memory" in request_text

    replay, after = _freeze_root(client, snapshot)
    assert replay["invocation_id"] == request["invocation_id"]
    assert replay["request_digest"] == request["request_digest"]
    assert after == before


def test_root_manifest_fails_closed_on_partial_or_malformed_control_plane(
    tmp_path: Path,
) -> None:
    root, snapshot = _root(tmp_path)
    (root / "agi" / "WORK_STRATEGY.json").unlink()
    with pytest.raises(WorkSessionError, match="partial Context Kernel control plane"):
        _freeze_root(_client(root), snapshot)

    _root(tmp_path / "second")
    second = tmp_path / "second"
    state_path = second / "agi" / "WORK_EXECUTION_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.pop("fence_token")
    _write_json(state_path, state)
    durable_snapshot = json.loads(
        (
            second
            / ".continual"
            / "runs"
            / RUN_ID
            / "snapshot.json"
        ).read_text(encoding="utf-8")
    )
    with pytest.raises(WorkSessionError, match="state.fence_token"):
        _freeze_root(_client(second), durable_snapshot)


def test_root_manifest_fails_closed_on_inbox_binding_disagreement(
    tmp_path: Path,
) -> None:
    root, snapshot = _root(tmp_path)
    state_path = root / "agi" / "WORK_EXECUTION_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["pending_user_input"] = {
        "revision": 1,
        "inbox_blob_sha": "0" * 40,
    }
    _write_json(state_path, state)

    with pytest.raises(WorkSessionError, match="inbox blob binding mismatch"):
        _freeze_root(_client(root), snapshot)


def test_frozen_request_survives_source_advance_and_next_root_changes(
    tmp_path: Path,
) -> None:
    root, snapshot = _root(tmp_path)
    client = _client(root)
    first, first_bytes = _freeze_root(client, snapshot)
    first_path = client.invocation_root / first["invocation_id"] / "request.json"
    outside_only = "Never dispatch a destructive effect without a fresh revocation check."
    assert outside_only not in json.dumps(first, ensure_ascii=False)

    inbox_path = root / "agi" / "USER_INPUT_INBOX.json"
    inbox = json.loads(inbox_path.read_text(encoding="utf-8"))
    inbox["revision"] = 2
    inbox["updated_at"] = "2026-08-23T00:01:00Z"
    inbox["entries"].append(
        {
            "sequence": 2,
            "id": "direction-revocation-v2",
            "kind": "user_direction",
            "status": "active",
            "summary": "Bind destructive effects to current revocations.",
            "directives": [outside_only],
            "supersedes": [],
        }
    )
    _write_json(inbox_path, inbox)

    second, _ = _freeze_root(client, snapshot)
    assert first_path.read_bytes() == first_bytes
    assert second["invocation_id"] != first["invocation_id"]
    assert second["request_digest"] != first["request_digest"]
    assert (
        second["payload"]["decision_context"]["source_clock"]["user_input_inbox"]
        != first["payload"]["decision_context"]["source_clock"]["user_input_inbox"]
    )
    assert outside_only in json.dumps(second, ensure_ascii=False)
    inbox_projection = next(
        source["projection"]
        for source in second["payload"]["decision_context"]["sources"]
        if source["source_id"] == "user_input_inbox"
    )
    assert inbox_projection["unacknowledged_entries"][0]["id"] == (
        "direction-revocation-v2"
    )


def test_outer_payload_cannot_inject_a_competing_decision_context(
    tmp_path: Path,
) -> None:
    root, snapshot = _root(tmp_path)
    with pytest.raises(WorkSessionError, match="may not inject"):
        _client(root).call(
            "root",
            {"snapshot": snapshot, "decision_context": {"authority": "outer"}},
            prompt_path="prompts/root.md",
        )
