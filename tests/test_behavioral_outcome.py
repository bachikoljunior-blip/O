from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from continual.behavioral_outcome import (
    CLAIM_SCOPE,
    BehavioralOutcomeError,
    behavioral_child_binding,
    prepare_behavioral_outcome,
    record_behavioral_outcome_from_work_invocation,
    verify_behavioral_outcome,
)
from continual.store import Store
from continual.work_session import WorkSessionError, submit_work_response


RUN_ID = "run-behavioral-test"
EXECUTOR = "current_chatgpt_work_session"
MODEL = "chatgpt-work-model-unverified"


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _state(now: str | None = None) -> dict:
    return {
        "status": "running",
        "owner_kind": "work_recovery_automation",
        "execution_id": "work-recovery-test",
        "lease_generation": 6,
        "fence_token": "opaque-fence-token",
        "heartbeat_at": now or _now(),
        "stale_after_seconds": 900,
        "user_input_inbox": {"highest_acknowledged_revision": 15},
    }


def _task() -> dict:
    return {
        "task_id": "canonical-inventory-v1",
        "instruction": "Return the inventory rows sorted by sku and the integer total.",
        "input": {
            "rows": [
                {"sku": "b", "quantity": 2},
                {"sku": "a", "quantity": 3},
            ]
        },
        "answer_format": "canonical_json",
        "response_pointer": ["result", "behavioral_answer"],
    }


def _answer() -> dict:
    return {
        "rows": [
            {"quantity": 3, "sku": "a"},
            {"quantity": 2, "sku": "b"},
        ],
        "total": 5,
    }


def _rubric() -> dict:
    return {
        "judge_kind": "exact_canonical_json",
        "judge_version": "exact-canonical-json-v1",
        "expected_answer": _answer(),
        "success_threshold": 1.0,
    }


def _prepare(root: Path, *, state: dict | None = None) -> dict:
    exact_state = state or _state()
    return prepare_behavioral_outcome(
        root,
        run_id=RUN_ID,
        state=exact_state,
        task=_task(),
        rubric=_rubric(),
        executor_binding=EXECUTOR,
        model_identity=MODEL,
        now=exact_state["heartbeat_at"],
    )


def _freeze_child(
    root: Path,
    request: dict,
    *,
    answer: object | None = None,
    executor: str = EXECUTOR,
    model: str = MODEL,
) -> tuple[str, dict]:
    store = Store(root)
    unit = {
        "unit_id": "unit-child-behavioral-test",
        "scope": "behavioral-outcome/live-child",
        "goal": "Answer the precommitted public task.",
        "behavioral_outcome": behavioral_child_binding(request),
    }
    payload = {"snapshot": {"phase": "unit_pending"}, "execution_unit": unit}
    prompt_content = "# EXECUTE\nReturn one public result."
    body = {
        "schema_version": 1,
        "invocation_id": "invoke-" + store.stable_digest(
            {
                "run_id": RUN_ID,
                "unit": unit,
                "executor": executor,
                "model": model,
            },
            length=24,
        ),
        "run_id": RUN_ID,
        "component": "execute",
        "provider": "chatgpt-work-external",
        "executor_binding": executor,
        "model_identity": model,
        "prompt_path": "prompts/execute.md",
        "prompt_digest": store.stable_digest(prompt_content, length=64),
        "prompt_content": prompt_content,
        "payload_digest": store.stable_digest(payload, length=64),
        "payload": payload,
        "contract": {
            "response_shape": "component output with result, fragment, and local_learn except Learn",
            "private_reasoning_forbidden": True,
            "secrets_forbidden": True,
        },
    }
    body["request_digest"] = store.stable_digest(body, length=64)
    body["created_at"] = _now()
    path = root / ".continual" / "work-model" / "invocations" / body["invocation_id"] / "request.json"
    store.atomic_json(path, body)
    output = {
        "result": {"behavioral_answer": _answer() if answer is None else answer},
        "fragment": {"public_observation": "bounded task response"},
        "local_learn": {"observed": "response recorded without hidden reasoning"},
    }
    response = submit_work_response(
        root,
        body["invocation_id"],
        output,
        executor_binding=executor,
        model_identity=model,
    )
    return body["invocation_id"], response


def _record(root: Path, request: dict, invocation_id: str, state: dict) -> dict:
    return record_behavioral_outcome_from_work_invocation(
        root,
        run_id=RUN_ID,
        outcome_id=request["outcome_id"],
        request_digest=request["request_digest"],
        work_invocation_id=invocation_id,
        state=state,
        now=_now(),
    )


def _outcome_files(root: Path, request: dict) -> list[Path]:
    base = root / ".continual" / "runs" / RUN_ID / "behavioral-outcomes" / request["outcome_id"]
    return [base / name for name in ("response.json", "judgment.json", "receipt.json")]


def test_live_bound_response_is_judged_once_and_replay_is_byte_stable(tmp_path: Path) -> None:
    state = _state()
    external = tmp_path / "evidence" / "external_ledger.json"
    external.parent.mkdir(parents=True)
    external.write_text('{"independent_observations":0}\n', encoding="utf-8")
    external_before = external.read_bytes()
    request = _prepare(tmp_path, state=state)
    invocation_id, _ = _freeze_child(tmp_path, request)

    first = _record(tmp_path, request, invocation_id, state)
    paths = _outcome_files(tmp_path, request) + [
        tmp_path / ".continual" / "runs" / RUN_ID / "behavioral-outcomes" / "ledger.json"
    ]
    before = {path: path.read_bytes() for path in paths}
    replay = _record(tmp_path, request, invocation_id, state)

    assert first["judgment"]["passed"] is True
    assert first["judgment"]["score"] == 1.0
    assert first["receipt"]["claim_scope"] == CLAIM_SCOPE
    assert first["ledger"]["internal_observation_count"] == 1
    assert replay["receipt"]["receipt_digest"] == first["receipt"]["receipt_digest"]
    assert {path: path.read_bytes() for path in paths} == before
    assert external.read_bytes() == external_before
    assert verify_behavioral_outcome(
        tmp_path, run_id=RUN_ID, outcome_id=request["outcome_id"]
    )["receipt"] == first["receipt"]


@pytest.mark.parametrize("mutated", ["task", "rubric"])
def test_post_precommit_task_or_rubric_mutation_is_rejected_atomically(
    tmp_path: Path, mutated: str
) -> None:
    state = _state()
    request = _prepare(tmp_path, state=state)
    invocation_id, _ = _freeze_child(tmp_path, request)
    path = (
        tmp_path
        / ".continual"
        / "runs"
        / RUN_ID
        / "behavioral-outcomes"
        / request["outcome_id"]
        / "request.json"
    )
    value = json.loads(path.read_text(encoding="utf-8"))
    if mutated == "task":
        value["task"]["input"]["rows"][0]["quantity"] = 99
    else:
        value["rubric"]["expected_answer"]["total"] = 99
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(BehavioralOutcomeError, match="tampered"):
        _record(tmp_path, request, invocation_id, state)
    assert all(not path.exists() for path in _outcome_files(tmp_path, request))


def test_wrong_executor_model_and_child_binding_fail_before_receipt(tmp_path: Path) -> None:
    state = _state()
    request = _prepare(tmp_path, state=state)
    invocation_id, _ = _freeze_child(
        tmp_path, request, executor="different-executor", model="different-model"
    )
    with pytest.raises(BehavioralOutcomeError, match="identity mismatch"):
        _record(tmp_path, request, invocation_id, state)
    assert all(not path.exists() for path in _outcome_files(tmp_path, request))

    other = tmp_path / "other"
    request = _prepare(other, state=state)
    invocation_id, _ = _freeze_child(other, request)
    child_path = other / ".continual" / "work-model" / "invocations" / invocation_id / "request.json"
    child = json.loads(child_path.read_text(encoding="utf-8"))
    child["payload"]["execution_unit"]["behavioral_outcome"]["task_digest"] = "0" * 64
    body = deepcopy(child)
    body.pop("created_at")
    body.pop("request_digest")
    child["request_digest"] = Store(other).stable_digest(body, length=64)
    child_path.write_text(json.dumps(child), encoding="utf-8")
    with pytest.raises(BehavioralOutcomeError, match="invalid bound Work invocation"):
        _record(other, request, invocation_id, state)
    assert all(not path.exists() for path in _outcome_files(other, request))


def test_stale_authority_and_tampered_response_fail_closed(tmp_path: Path) -> None:
    state = _state()
    request = _prepare(tmp_path, state=state)
    invocation_id, _ = _freeze_child(tmp_path, request)
    stale = deepcopy(state)
    stale["heartbeat_at"] = (
        datetime.now(UTC) - timedelta(hours=1)
    ).isoformat().replace("+00:00", "Z")
    with pytest.raises(BehavioralOutcomeError, match="heartbeat is stale"):
        _record(tmp_path, request, invocation_id, stale)
    assert all(not path.exists() for path in _outcome_files(tmp_path, request))

    response_path = tmp_path / ".continual" / "work-model" / "invocations" / invocation_id / "response.json"
    response = json.loads(response_path.read_text(encoding="utf-8"))
    response["output"]["result"]["behavioral_answer"]["total"] = 500
    response_path.write_text(json.dumps(response), encoding="utf-8")
    with pytest.raises(BehavioralOutcomeError, match="invalid bound Work invocation"):
        _record(tmp_path, request, invocation_id, state)
    assert all(not path.exists() for path in _outcome_files(tmp_path, request))


@pytest.mark.parametrize(
    "answer,match,blocked_by_work_journal",
    [
        ({"hidden_reasoning": "private"}, "forbidden private field", True),
        ({"token": "Bearer abcdefghijklmnopqrst"}, "secret-like text", True),
        ({"data": "x" * 5000}, "byte budget", False),
    ],
)
def test_private_secret_or_unbounded_answer_is_rejected(
    tmp_path: Path, answer: object, match: str, blocked_by_work_journal: bool
) -> None:
    state = _state()
    request = _prepare(tmp_path, state=state)
    if blocked_by_work_journal:
        with pytest.raises(WorkSessionError, match=match):
            _freeze_child(tmp_path, request, answer=answer)
        assert all(not path.exists() for path in _outcome_files(tmp_path, request))
        return
    invocation_id, _ = _freeze_child(tmp_path, request, answer=answer)
    with pytest.raises(BehavioralOutcomeError, match=match):
        _record(tmp_path, request, invocation_id, state)
    assert all(not path.exists() for path in _outcome_files(tmp_path, request))


def test_wrong_answer_is_nonzero_observation_but_not_a_pass(tmp_path: Path) -> None:
    state = _state()
    request = _prepare(tmp_path, state=state)
    invocation_id, _ = _freeze_child(tmp_path, request, answer={"total": 4})
    result = _record(tmp_path, request, invocation_id, state)

    assert result["judgment"]["score"] == 0.0
    assert result["judgment"]["passed"] is False
    assert result["ledger"]["internal_observation_count"] == 1
    assert "independent" in result["receipt"]["claim_scope"]


def test_missing_or_rehashed_judge_field_is_rejected_on_replay(tmp_path: Path) -> None:
    state = _state()
    request = _prepare(tmp_path, state=state)
    invocation_id, _ = _freeze_child(tmp_path, request)
    _record(tmp_path, request, invocation_id, state)
    path = _outcome_files(tmp_path, request)[1]
    judgment = json.loads(path.read_text(encoding="utf-8"))
    judgment.pop("judge_version")
    judgment.pop("judgment_digest")
    judgment["judgment_digest"] = Store(tmp_path).stable_digest(judgment, length=64)
    path.write_text(json.dumps(judgment), encoding="utf-8")

    with pytest.raises(BehavioralOutcomeError, match="immutable behavioral_outcome_judgment conflict"):
        _record(tmp_path, request, invocation_id, state)
