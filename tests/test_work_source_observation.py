from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from continual.store import Store
from continual.work_source_observation import (
    WorkSourceObservationError,
    prepare_work_source_observation,
    record_work_source_observation_receipt,
    verify_work_source_observation,
)


RUN_ID = "run-source-observation-test"
COMMIT = "a" * 40
MODEL = "work-source-observation-test-model"
EXECUTOR = "work-source-observation-test-executor"


def _blob(raw: bytes) -> str:
    return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()  # noqa: S324


def _state(now: datetime) -> dict:
    return {
        "status": "running",
        "owner_kind": "work_recovery_automation",
        "execution_id": "work-source-observation-test",
        "lease_generation": 7,
        "fence_token": "opaque-fence-value",
        "heartbeat_at": now.isoformat(),
        "stale_after_seconds": 900,
        "authoritative_source_observation_policy": {
            "required": True,
            "repository_full_name": "owner/repo",
            "ref": "main",
            "max_age_seconds": 300,
            "executor_binding": EXECUTOR,
        },
    }


def _authority(state: dict, store: Store) -> dict:
    return {
        "status": state["status"],
        "owner_kind": state["owner_kind"],
        "execution_id": state["execution_id"],
        "lease_generation": state["lease_generation"],
        "fence_token_digest": store.stable_digest(
            state["fence_token"], length=64
        ),
        "heartbeat_at": state["heartbeat_at"],
    }


def _prepared(tmp_path: Path, *, now: datetime | None = None):
    now = now or datetime.now(UTC)
    state = _state(now)
    raw = (json.dumps(state, indent=2, sort_keys=True) + "\n").encode()
    blob = _blob(raw)
    request = prepare_work_source_observation(
        tmp_path,
        run_id=RUN_ID,
        state=state,
        state_blob_sha=blob,
        expected_commit_sha=COMMIT,
        model_identity=MODEL,
    )
    return state, blob, request


def _record(
    tmp_path: Path,
    state: dict,
    blob: str,
    request: dict,
    *,
    observed_at: str | None = None,
    projection: dict | None = None,
):
    return record_work_source_observation_receipt(
        tmp_path,
        run_id=RUN_ID,
        observation_id=request["observation_id"],
        request_digest=request["request_digest"],
        executor_binding=EXECUTOR,
        model_identity=MODEL,
        commit_sha=COMMIT,
        blob_sha=blob,
        projection=projection or _authority(state, Store(tmp_path)),
        observed_at=observed_at or datetime.now(UTC).isoformat(),
    )


def test_precommitted_exact_connector_receipt_verifies(tmp_path: Path) -> None:
    state, blob, request = _prepared(tmp_path)
    request_path = (
        tmp_path
        / ".continual"
        / "runs"
        / RUN_ID
        / "work-source-observations"
        / request["observation_id"]
        / "request.json"
    )
    assert request_path.is_file()
    assert not (request_path.parent / "receipt.json").exists()
    receipt = _record(tmp_path, state, blob, request)

    verified = verify_work_source_observation(
        tmp_path,
        run_id=RUN_ID,
        state=state,
        state_blob_sha=blob,
        now=datetime.now(UTC).isoformat(),
    )

    assert verified is not None
    assert verified["request_digest"] == request["request_digest"]
    assert verified["receipt_digest"] == receipt["receipt_digest"]
    assert verified["source_version"] == {
        "commit_sha": COMMIT,
        "blob_sha": blob,
    }
    assert verified["claim_scope"].endswith("not_linearizable_latest_proof")


def test_missing_receipt_fails_closed(tmp_path: Path) -> None:
    state, blob, _ = _prepared(tmp_path)

    with pytest.raises(WorkSourceObservationError, match="matching fresh"):
        verify_work_source_observation(
            tmp_path,
            run_id=RUN_ID,
            state=state,
            state_blob_sha=blob,
            now=datetime.now(UTC).isoformat(),
        )


def test_same_source_can_precommit_a_new_fresh_observation(tmp_path: Path) -> None:
    state, blob, first = _prepared(tmp_path)
    first_path = (
        tmp_path
        / ".continual"
        / "runs"
        / RUN_ID
        / "work-source-observations"
        / first["observation_id"]
        / "request.json"
    )
    first_bytes = first_path.read_bytes()
    first_receipt = _record(tmp_path, state, blob, first)

    second = prepare_work_source_observation(
        tmp_path,
        run_id=RUN_ID,
        state=state,
        state_blob_sha=blob,
        expected_commit_sha=COMMIT,
        model_identity=MODEL,
    )

    second_path = (
        tmp_path
        / ".continual"
        / "runs"
        / RUN_ID
        / "work-source-observations"
        / second["observation_id"]
        / "request.json"
    )
    assert second["observation_id"] != first["observation_id"]
    assert second["request_digest"] != first["request_digest"]
    assert first_path.read_bytes() == first_bytes
    assert first_receipt["request_digest"] == first["request_digest"]
    assert second_path.is_file()
    assert not (second_path.parent / "receipt.json").exists()


def test_verification_prefers_latest_same_source_renewal(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    state, blob, first = _prepared(tmp_path, now=now)
    _record(
        tmp_path,
        state,
        blob,
        first,
        observed_at=(now + timedelta(seconds=1)).isoformat(),
    )
    second = prepare_work_source_observation(
        tmp_path,
        run_id=RUN_ID,
        state=state,
        state_blob_sha=blob,
        expected_commit_sha=COMMIT,
        model_identity=MODEL,
    )
    second_receipt = _record(
        tmp_path,
        state,
        blob,
        second,
        observed_at=(now + timedelta(seconds=2)).isoformat(),
    )

    verified = verify_work_source_observation(
        tmp_path,
        run_id=RUN_ID,
        state=state,
        state_blob_sha=blob,
        now=(now + timedelta(seconds=3)).isoformat(),
    )

    assert verified is not None
    assert verified["observation_id"] == second["observation_id"]
    assert verified["receipt_digest"] == second_receipt["receipt_digest"]


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("commit_sha", "b" * 40, "commit mismatch"),
        ("blob_sha", "b" * 40, "blob mismatch"),
        ("executor_binding", "different-executor", "identity mismatch"),
    ],
)
def test_receipt_rejects_cross_binding_mismatch(
    tmp_path: Path, field: str, value: str, match: str
) -> None:
    state, blob, request = _prepared(tmp_path)
    values = {
        "root": tmp_path,
        "run_id": RUN_ID,
        "observation_id": request["observation_id"],
        "request_digest": request["request_digest"],
        "executor_binding": EXECUTOR,
        "model_identity": MODEL,
        "commit_sha": COMMIT,
        "blob_sha": blob,
        "projection": _authority(state, Store(tmp_path)),
        "observed_at": datetime.now(UTC).isoformat(),
    }
    values[field] = value

    with pytest.raises(WorkSourceObservationError, match=match):
        record_work_source_observation_receipt(**values)


def test_receipt_rejects_authority_projection_mismatch(tmp_path: Path) -> None:
    state, blob, request = _prepared(tmp_path)
    projection = _authority(state, Store(tmp_path))
    projection["lease_generation"] += 1

    with pytest.raises(WorkSourceObservationError, match="authority mismatch"):
        _record(tmp_path, state, blob, request, projection=projection)


def test_receipt_cannot_claim_an_observation_before_its_precommit(
    tmp_path: Path,
) -> None:
    state, blob, request = _prepared(tmp_path)
    requested = datetime.fromisoformat(
        request["requested_at"].replace("Z", "+00:00")
    )

    with pytest.raises(WorkSourceObservationError, match="predates"):
        _record(
            tmp_path,
            state,
            blob,
            request,
            observed_at=(requested - timedelta(microseconds=1)).isoformat(),
        )


def test_stale_and_future_receipts_fail_closed(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    for observation_offset, verification_offset, match in [
        (timedelta(seconds=1), timedelta(seconds=302), "matching fresh"),
        (timedelta(seconds=1), timedelta(0), "future-skewed"),
    ]:
        root = tmp_path / match.replace("-", "_").replace(" ", "_")
        state, blob, request = _prepared(root, now=now)
        _record(
            root,
            state,
            blob,
            request,
            observed_at=(now + observation_offset).isoformat(),
        )
        with pytest.raises(WorkSourceObservationError, match=match):
            verify_work_source_observation(
                root,
                run_id=RUN_ID,
                state=state,
                state_blob_sha=blob,
                now=(now + verification_offset).isoformat(),
            )


def test_prior_heartbeat_receipt_does_not_authorize_new_state_blob(
    tmp_path: Path,
) -> None:
    state, blob, request = _prepared(tmp_path)
    _record(tmp_path, state, blob, request)
    advanced = deepcopy(state)
    advanced["heartbeat_at"] = (
        datetime.fromisoformat(state["heartbeat_at"]) + timedelta(seconds=1)
    ).isoformat()
    advanced_raw = (json.dumps(advanced, indent=2, sort_keys=True) + "\n").encode()

    with pytest.raises(WorkSourceObservationError, match="matching fresh"):
        verify_work_source_observation(
            tmp_path,
            run_id=RUN_ID,
            state=advanced,
            state_blob_sha=_blob(advanced_raw),
            now=datetime.now(UTC).isoformat(),
        )


def test_tampered_receipt_fails_closed(tmp_path: Path) -> None:
    state, blob, request = _prepared(tmp_path)
    receipt = _record(tmp_path, state, blob, request)
    path = (
        tmp_path
        / ".continual"
        / "runs"
        / RUN_ID
        / "work-source-observations"
        / request["observation_id"]
        / "receipt.json"
    )
    receipt["projection"]["status"] = "released"
    path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(WorkSourceObservationError, match="tampered"):
        verify_work_source_observation(
            tmp_path,
            run_id=RUN_ID,
            state=state,
            state_blob_sha=blob,
            now=datetime.now(UTC).isoformat(),
        )


def test_policy_absent_preserves_existing_local_readiness_behavior(
    tmp_path: Path,
) -> None:
    state = _state(datetime.now(UTC))
    state.pop("authoritative_source_observation_policy")

    assert (
        verify_work_source_observation(
            tmp_path,
            run_id=RUN_ID,
            state=state,
            state_blob_sha="a" * 40,
            now=datetime.now(UTC).isoformat(),
        )
        is None
    )
