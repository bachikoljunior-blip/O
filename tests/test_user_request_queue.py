from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from agi.user_request_queue import (
    UserRequestQueueError,
    due_user_requests,
    enqueue_user_request,
    load_user_request_queue,
    validate_user_request_queue,
)


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 22, 13, 0, tzinfo=timezone.utc)


def _new_request() -> dict:
    return {
        "id": "user-only-operation-test-v1",
        "kind": "operation",
        "priority": "helpful",
        "status": "open",
        "summary": "A user-only operation could shorten a bounded path.",
        "reason": "The operation is unavailable to the current execution environment.",
        "requested_user_action": "Use the approved control surface; do not paste secrets.",
        "created_at": "2026-08-22T13:00:00Z",
        "reevaluate_on": "The operation becomes observable or the next Root review occurs.",
        "reevaluate_by": "2026-08-23T13:00:00Z",
        "non_blocking": True,
        "safe_alternative_work": ["Continue a safe reversible independent unit."],
    }


def test_checked_in_queue_is_non_blocking_secret_free_and_finitely_reevaluated() -> None:
    queue = load_user_request_queue(ROOT)

    assert validate_user_request_queue(queue) == []
    assert queue["policy"]["project_waiting_is_stop_condition"] is False
    assert queue["policy"]["secrets_allowed"] is False
    assert all(item["non_blocking"] is True for item in queue["requests"] if item["status"] == "open")


def _behavior_queue() -> dict:
    queue = deepcopy(load_user_request_queue(ROOT))
    queue.update(revision=7, requests=[], updated_at=NOW.isoformat())
    return queue


def test_due_requests_include_deadline_and_exclude_closed_requests() -> None:
    queue = _behavior_queue()
    queue["requests"] = [
        {**_new_request(), "id": "due-now", "reevaluate_by": NOW.isoformat()},
        {**_new_request(), "id": "due-later", "reevaluate_by": (NOW + timedelta(days=1)).isoformat()},
        {**_new_request(), "id": "closed", "status": "fulfilled", "reevaluate_by": NOW.isoformat()},
    ]

    assert due_user_requests(queue, now=NOW - timedelta(seconds=1)) == []
    assert [item["id"] for item in due_user_requests(queue, now=NOW)] == ["due-now"]
    assert [item["id"] for item in due_user_requests(queue, now=NOW + timedelta(days=1))] == ["due-now", "due-later"]


def test_enqueue_is_revision_bound_atomic_and_idempotent(tmp_path: Path) -> None:
    (tmp_path / "agi").mkdir()
    queue_path = tmp_path / "agi" / "USER_REQUEST_QUEUE.json"
    fixture = _behavior_queue()
    initial_revision = fixture["revision"]
    queue_path.write_text(json.dumps(fixture), encoding="utf-8")
    request = _new_request()

    updated = enqueue_user_request(
        tmp_path,
        request,
        expected_revision=initial_revision,
        updated_at=NOW,
    )
    replay = enqueue_user_request(
        tmp_path,
        request,
        expected_revision=initial_revision,
        updated_at=NOW,
    )

    assert updated["revision"] == initial_revision + 1
    assert replay == updated
    assert load_user_request_queue(tmp_path) == updated

    before_conflict = queue_path.read_bytes()
    with pytest.raises(UserRequestQueueError, match="revision conflict"):
        enqueue_user_request(
            tmp_path,
            {**_new_request(), "id": "different-request-v1"},
            expected_revision=initial_revision,
            updated_at=NOW,
        )
    assert queue_path.read_bytes() == before_conflict


def test_queue_rejects_secret_bearing_fields_and_duplicate_ids() -> None:
    queue = json.loads((ROOT / "agi" / "USER_REQUEST_QUEUE.json").read_text(encoding="utf-8"))
    unsafe = deepcopy(queue)
    unsafe["requests"][0]["token"] = "must-not-be-stored"
    duplicate = deepcopy(queue)
    duplicate["requests"].append(deepcopy(duplicate["requests"][0]))

    assert any("forbidden secret-bearing field" in error for error in validate_user_request_queue(unsafe))
    assert any("duplicate request id" in error for error in validate_user_request_queue(duplicate))
