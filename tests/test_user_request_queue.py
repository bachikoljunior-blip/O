from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

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
    assert due_user_requests(queue, now=NOW) == []
    assert len(due_user_requests(queue, now=datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc))) == 2


def test_enqueue_is_revision_bound_atomic_and_idempotent(tmp_path: Path) -> None:
    (tmp_path / "agi").mkdir()
    shutil.copy2(ROOT / "agi" / "USER_REQUEST_QUEUE.json", tmp_path / "agi" / "USER_REQUEST_QUEUE.json")
    request = _new_request()

    updated = enqueue_user_request(
        tmp_path,
        request,
        expected_revision=0,
        updated_at=NOW,
    )
    replay = enqueue_user_request(
        tmp_path,
        request,
        expected_revision=0,
        updated_at=NOW,
    )

    assert updated["revision"] == 1
    assert replay == updated
    assert load_user_request_queue(tmp_path) == updated

    with pytest.raises(UserRequestQueueError, match="revision conflict"):
        enqueue_user_request(
            tmp_path,
            {**_new_request(), "id": "different-request-v1"},
            expected_revision=0,
            updated_at=NOW,
        )


def test_queue_rejects_secret_bearing_fields_and_duplicate_ids() -> None:
    queue = json.loads((ROOT / "agi" / "USER_REQUEST_QUEUE.json").read_text(encoding="utf-8"))
    unsafe = deepcopy(queue)
    unsafe["requests"][0]["token"] = "must-not-be-stored"
    duplicate = deepcopy(queue)
    duplicate["requests"].append(deepcopy(duplicate["requests"][0]))

    assert any("forbidden secret-bearing field" in error for error in validate_user_request_queue(unsafe))
    assert any("duplicate request id" in error for error in validate_user_request_queue(duplicate))
