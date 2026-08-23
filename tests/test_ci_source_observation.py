from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from continual.ci_source_observation import (
    CiSourceObservationError,
    prepare_ci_source_observation,
    record_ci_source_observation_receipt,
    verify_ci_source_observation,
)


RUN_ID = "run-ci-source-observation-test"
HEAD = "a" * 40
EXECUTOR = "ci-source-observation-test-executor"
MODEL = "ci-source-observation-test-model"
WORKFLOW_RUN_ID = 12345
WORKFLOW_ID = 678
REQUIRED_JOBS = [
    {"id": 11, "name": "pytest shard 0 of 4"},
    {"id": 12, "name": "pytest shard 1 of 4"},
    {"id": 13, "name": "pytest shard 2 of 4"},
    {"id": 14, "name": "pytest shard 3 of 4"},
    {"id": 15, "name": "test"},
]


def _state() -> dict:
    return {
        "status": "running",
        "owner_kind": "work_recovery_automation",
        "execution_id": "ci-source-observation-test",
        "lease_generation": 8,
        "fence_token": "opaque-ci-source-fence",
        "ci_source_observation_policy": {
            "required": True,
            "repository_full_name": "owner/repo",
            "exact_head_sha": HEAD,
            "workflow_run_id": WORKFLOW_RUN_ID,
            "workflow_id": WORKFLOW_ID,
            "required_jobs": deepcopy(REQUIRED_JOBS),
            "max_age_seconds": 300,
            "executor_binding": EXECUTOR,
        },
    }


def _run(**overrides) -> dict:
    value = {
        "id": WORKFLOW_RUN_ID,
        "workflow_id": WORKFLOW_ID,
        "name": "test",
        "status": "completed",
        "conclusion": "success",
        "head_sha": HEAD,
    }
    value.update(overrides)
    return value


def _jobs() -> list[dict]:
    return [
        {**job, "status": "completed", "conclusion": "success"}
        for job in REQUIRED_JOBS
    ]


def _prepared(tmp_path: Path):
    state = _state()
    request = prepare_ci_source_observation(
        tmp_path,
        run_id=RUN_ID,
        state=state,
        model_identity=MODEL,
    )
    return state, request


def _record(
    tmp_path: Path,
    request: dict,
    *,
    workflow_run: dict | None = None,
    jobs: list[dict] | None = None,
    observed_at: str | None = None,
):
    return record_ci_source_observation_receipt(
        tmp_path,
        run_id=RUN_ID,
        observation_id=request["observation_id"],
        request_digest=request["request_digest"],
        executor_binding=EXECUTOR,
        model_identity=MODEL,
        workflow_run=workflow_run or _run(),
        jobs=jobs or _jobs(),
        observed_at=observed_at or datetime.now(UTC).isoformat(),
    )


def _receipt_path(tmp_path: Path, request: dict) -> Path:
    return (
        tmp_path
        / ".continual"
        / "runs"
        / RUN_ID
        / "ci-source-observations"
        / request["observation_id"]
        / "receipt.json"
    )


def test_exact_successful_run_and_required_jobs_verify(tmp_path: Path) -> None:
    state, request = _prepared(tmp_path)
    request_path = _receipt_path(tmp_path, request).with_name("request.json")
    assert request_path.is_file()
    assert not _receipt_path(tmp_path, request).exists()
    receipt = _record(tmp_path, request)

    verified = verify_ci_source_observation(
        tmp_path,
        run_id=RUN_ID,
        state=state,
        now=datetime.now(UTC).isoformat(),
    )

    assert verified is not None
    assert verified["receipt_digest"] == receipt["receipt_digest"]
    assert verified["source_version"] == {
        "exact_head_sha": HEAD,
        "workflow_run_id": WORKFLOW_RUN_ID,
        "workflow_id": WORKFLOW_ID,
    }
    assert [job["name"] for job in verified["projection"]["required_jobs"]][-1] == "test"
    assert verified["claim_scope"].endswith("not_behavioral_or_completion_evidence")


def test_missing_receipt_fails_closed(tmp_path: Path) -> None:
    state, _ = _prepared(tmp_path)
    with pytest.raises(CiSourceObservationError, match="matching fresh"):
        verify_ci_source_observation(
            tmp_path,
            run_id=RUN_ID,
            state=state,
            now=datetime.now(UTC).isoformat(),
        )


@pytest.mark.parametrize(
    ("run_change", "message"),
    [
        ({"head_sha": "b" * 40}, "head mismatch"),
        ({"id": 999}, "run id mismatch"),
        ({"workflow_id": 999}, "workflow id mismatch"),
        ({"status": "in_progress", "conclusion": "success"}, "not successful"),
        ({"status": "completed", "conclusion": "failure"}, "not successful"),
    ],
)
def test_wrong_run_identity_or_conclusion_is_atomic(
    tmp_path: Path, run_change: dict, message: str
) -> None:
    _, request = _prepared(tmp_path)
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    with pytest.raises(CiSourceObservationError, match=message):
        _record(tmp_path, request, workflow_run=_run(**run_change))
    assert not _receipt_path(tmp_path, request).exists()
    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_missing_aggregate_or_failed_required_job_is_atomic(tmp_path: Path) -> None:
    _, request = _prepared(tmp_path)
    with pytest.raises(CiSourceObservationError, match="topology mismatch"):
        _record(tmp_path, request, jobs=_jobs()[:-1])
    assert not _receipt_path(tmp_path, request).exists()

    failed = _jobs()
    failed[-1]["conclusion"] = "failure"
    with pytest.raises(CiSourceObservationError, match="required CI job"):
        _record(tmp_path, request, jobs=failed)
    assert not _receipt_path(tmp_path, request).exists()


@pytest.mark.parametrize("conclusion", ["cancelled", "skipped", "timed_out", "neutral"])
def test_non_success_required_job_fails_closed(tmp_path: Path, conclusion: str) -> None:
    _, request = _prepared(tmp_path)
    jobs = _jobs()
    jobs[0]["conclusion"] = conclusion
    with pytest.raises(CiSourceObservationError, match="required CI job"):
        _record(tmp_path, request, jobs=jobs)


def test_duplicate_job_identity_fails_closed(tmp_path: Path) -> None:
    _, request = _prepared(tmp_path)
    jobs = _jobs()
    jobs[1]["id"] = jobs[0]["id"]
    with pytest.raises(CiSourceObservationError, match="unique"):
        _record(tmp_path, request, jobs=jobs)


def test_receipt_cannot_predate_request(tmp_path: Path) -> None:
    _, request = _prepared(tmp_path)
    before = datetime.fromisoformat(request["requested_at"].replace("Z", "+00:00"))
    with pytest.raises(CiSourceObservationError, match="predates"):
        _record(
            tmp_path,
            request,
            observed_at=(before - timedelta(seconds=1)).isoformat(),
        )


def test_stale_and_future_receipts_fail_closed(tmp_path: Path) -> None:
    state, request = _prepared(tmp_path)
    receipt = _record(tmp_path, request)
    observed = datetime.fromisoformat(receipt["observed_at"].replace("Z", "+00:00"))
    with pytest.raises(CiSourceObservationError, match="matching fresh"):
        verify_ci_source_observation(
            tmp_path,
            run_id=RUN_ID,
            state=state,
            now=(observed + timedelta(seconds=301)).isoformat(),
        )
    with pytest.raises(CiSourceObservationError, match="future-skewed"):
        verify_ci_source_observation(
            tmp_path,
            run_id=RUN_ID,
            state=state,
            now=(observed - timedelta(seconds=1)).isoformat(),
        )


def test_tampered_receipt_and_authority_change_fail_closed(tmp_path: Path) -> None:
    state, request = _prepared(tmp_path)
    _record(tmp_path, request)
    receipt_path = _receipt_path(tmp_path, request)
    tampered = json.loads(receipt_path.read_text(encoding="utf-8"))
    tampered["projection"]["required_jobs"][-1]["conclusion"] = "failure"
    receipt_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(CiSourceObservationError, match="tampered"):
        verify_ci_source_observation(
            tmp_path,
            run_id=RUN_ID,
            state=state,
            now=datetime.now(UTC).isoformat(),
        )

    other = tmp_path / "authority"
    changed_state, changed_request = _prepared(other)
    _record(other, changed_request)
    changed_state["lease_generation"] += 1
    with pytest.raises(CiSourceObservationError, match="matching fresh"):
        verify_ci_source_observation(
            other,
            run_id=RUN_ID,
            state=changed_state,
            now=datetime.now(UTC).isoformat(),
        )


def test_policy_change_invalidates_prior_receipt(tmp_path: Path) -> None:
    state, request = _prepared(tmp_path)
    _record(tmp_path, request)
    state["ci_source_observation_policy"]["workflow_run_id"] += 1
    with pytest.raises(CiSourceObservationError, match="matching fresh"):
        verify_ci_source_observation(
            tmp_path,
            run_id=RUN_ID,
            state=state,
            now=datetime.now(UTC).isoformat(),
        )
