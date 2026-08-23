from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from continual.work_effect_dispatch import (
    AuthorizedWorkEffect,
    dispatch_work_effect,
    load_authorized_work_effect,
)
from continual.work_effect_reconciliation import reconcile_work_effect
from continual.work_effects import (
    WorkEffectError,
    authorize_work_effect,
    prepare_work_effect,
    verify_work_effect,
)
from continual.work_session import WorkSession, submit_work_response


def _root(tmp_path: Path) -> Path:
    shutil.copytree(Path("prompts"), tmp_path / "prompts")
    return tmp_path


def _output(component: str) -> dict:
    result = (
        {"objective": "reconcile one effect"}
        if component == "entry"
        else {"component": "execute", "goal": "reconcile one effect"}
    )
    return {
        "result": result,
        "local_learn": {"decision": "NO_CHANGE", "candidates": []},
        "fragment": {"component": component, "observations": ["fixture"]},
    }


def _pending(root: Path) -> dict:
    session = WorkSession(
        root, executor_binding="session-a", model_identity="model-a"
    )
    state = session.start("reconcile an effect", run_id="run-reconcile-effect")
    for component in ("entry", "root"):
        request = state["pending"][0]
        submit_work_response(
            root,
            request["invocation_id"],
            _output(component),
            executor_binding="session-a",
            model_identity="model-a",
        )
        state = session.resume("run-reconcile-effect")
    request = state["pending"][0]
    assert request["component"] == "execute"
    return request


def _action(path: str = "safe.txt") -> dict:
    return {
        "kind": "github_update_file",
        "target": {
            "repository": "owner/repo",
            "branch": "work/secondary",
            "path": path,
        },
        "parameters": {"expected_blob_sha": "a" * 40},
    }


def _authorized(root: Path, request: dict, effect_id: str) -> AuthorizedWorkEffect:
    prepare_work_effect(
        root,
        run_id=request["run_id"],
        effect_id=effect_id,
        invocation_id=request["invocation_id"],
        action=_action(),
        executor_binding="session-a",
        model_identity="model-a",
    )
    authorize_work_effect(
        root,
        run_id=request["run_id"],
        effect_id=effect_id,
        invocation_id=request["invocation_id"],
        request_digest=request["request_digest"],
        action=_action(),
        executor_binding="session-a",
        model_identity="model-a",
    )
    return load_authorized_work_effect(
        root,
        run_id=request["run_id"],
        effect_id=effect_id,
        executor_binding="session-a",
        model_identity="model-a",
    )


def _ambiguous(root: Path, request: dict, effect_id: str) -> AuthorizedWorkEffect:
    authorized = _authorized(root, request, effect_id)

    def issued_then_lost(_: dict) -> dict:
        raise RuntimeError("receipt lost")

    with pytest.raises(RuntimeError, match="receipt lost"):
        dispatch_work_effect(
            root, authorization=authorized, callback=issued_then_lost
        )
    return authorized


def test_confirmed_success_reconciliation_is_separate_receipted_and_replayed(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    request = _pending(root)
    authorized = _ambiguous(root, request, "success-v1")
    calls: list[dict] = []

    def readback(envelope: dict) -> dict:
        calls.append(envelope)
        return {
            "outcome": "confirmed_succeeded",
            "readback": {"remote_commit_sha": "c" * 40},
        }

    first = reconcile_work_effect(
        root, authorization=authorized, callback=readback
    )
    assert first["reconciled"] is True
    assert first["replayed"] is False
    assert len(calls) == 1
    assert calls[0]["action"] == _action()
    assert calls[0]["authorization_digest"] == authorized.authorization_digest
    assert calls[0]["idempotency_key"] == authorized.idempotency_key
    assert isinstance(calls[0]["dispatch_digest"], str)

    effect_dir = (
        root
        / ".continual"
        / "runs"
        / request["run_id"]
        / "external-effects"
        / "success-v1"
    )
    reconciliation = json.loads(
        (effect_dir / "reconciliation.json").read_text(encoding="utf-8")
    )
    authorization = json.loads(
        (effect_dir / "authorization.json").read_text(encoding="utf-8")
    )
    assert reconciliation["record_type"] == "effect_reconciliation"
    assert authorization["record_type"] == "effect_authorization"
    assert reconciliation["reconciliation_digest"] == first["reconciliation_digest"]
    assert verify_work_effect(
        root, run_id=request["run_id"], effect_id="success-v1"
    )["status"] == "completed"

    replay = reconcile_work_effect(
        root, authorization=authorized, callback=readback
    )
    assert replay["replayed"] is True
    assert replay["reconciliation_digest"] == first["reconciliation_digest"]
    assert len(calls) == 1


@pytest.mark.parametrize("outcome", ["unknown", "confirmed_not_applied"])
def test_non_success_reconciliation_never_creates_receipt_or_redispatches(
    tmp_path: Path, outcome: str
) -> None:
    root = _root(tmp_path)
    request = _pending(root)
    authorized = _ambiguous(root, request, f"{outcome}-v1")
    calls = 0

    def readback(_: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"outcome": outcome, "readback": {"observed": False}}

    first = reconcile_work_effect(
        root, authorization=authorized, callback=readback
    )
    assert first["outcome"] == outcome
    assert first["receipt_digest"] is None
    assert verify_work_effect(
        root, run_id=request["run_id"], effect_id=f"{outcome}-v1"
    )["status"] == "authorized"
    replay = reconcile_work_effect(
        root, authorization=authorized, callback=readback
    )
    assert replay["replayed"] is True
    assert replay["receipt_digest"] is None
    assert calls == 1


def test_missing_claim_completed_forged_and_tampered_fail_before_readback(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    request = _pending(root)
    calls = 0

    def readback(_: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"outcome": "unknown", "readback": {}}

    unclaimed = _authorized(root, request, "unclaimed-v1")
    with pytest.raises(WorkEffectError, match="requires a dispatch claim"):
        reconcile_work_effect(root, authorization=unclaimed, callback=readback)

    completed = _authorized(root, request, "completed-v1")
    dispatch_work_effect(
        root,
        authorization=completed,
        callback=lambda _: {"verified_readback": True},
    )
    with pytest.raises(WorkEffectError, match="already completed"):
        reconcile_work_effect(root, authorization=completed, callback=readback)

    ambiguous = _ambiguous(root, request, "forged-v1")
    forged = replace(ambiguous, request_digest="f" * 64)
    with pytest.raises(WorkEffectError, match="typed authorization identity"):
        reconcile_work_effect(root, authorization=forged, callback=readback)

    tampered = _ambiguous(root, request, "tampered-v1")
    dispatch_path = (
        root
        / ".continual"
        / "runs"
        / request["run_id"]
        / "external-effects"
        / "tampered-v1"
        / "dispatch.json"
    )
    value = json.loads(dispatch_path.read_text(encoding="utf-8"))
    value["idempotency_key"] = "0" * 64
    dispatch_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(WorkEffectError, match="tampered effect dispatch claim"):
        reconcile_work_effect(root, authorization=tampered, callback=readback)
    assert calls == 0


def test_readback_failure_or_private_data_leaves_claim_and_blocks_retry(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    request = _pending(root)
    authorized = _ambiguous(root, request, "readback-failure-v1")

    def failed(_: dict) -> dict:
        raise RuntimeError("readback unavailable")

    with pytest.raises(RuntimeError, match="readback unavailable"):
        reconcile_work_effect(root, authorization=authorized, callback=failed)

    retries = 0

    def retry(_: dict) -> dict:
        nonlocal retries
        retries += 1
        return {"outcome": "unknown", "readback": {}}

    with pytest.raises(WorkEffectError, match="reconciliation already claimed"):
        reconcile_work_effect(root, authorization=authorized, callback=retry)
    assert retries == 0

    private_authorized = _ambiguous(root, request, "private-readback-v1")
    with pytest.raises(WorkEffectError, match="forbidden private field"):
        reconcile_work_effect(
            root,
            authorization=private_authorized,
            callback=lambda _: {
                "outcome": "confirmed_succeeded",
                "readback": {"credentials": "must-not-persist"},
            },
        )
    assert verify_work_effect(
        root, run_id=request["run_id"], effect_id="private-readback-v1"
    )["status"] == "authorized"
