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


def _component_output(component: str) -> dict:
    result = (
        {"objective": "dispatch one exact guarded action"}
        if component == "entry"
        else {"component": "execute", "goal": "dispatch one exact effect"}
    )
    return {
        "result": result,
        "local_learn": {"decision": "NO_CHANGE", "candidates": []},
        "fragment": {"component": component, "observations": ["fixture"]},
    }


def _pending_execute(root: Path) -> dict:
    session = WorkSession(
        root,
        executor_binding="session-a",
        model_identity="model-a",
    )
    state = session.start("dispatch an effect", run_id="run-dispatch-effect")
    for component in ("entry", "root"):
        request = state["pending"][0]
        assert request["component"] == component
        submit_work_response(
            root,
            request["invocation_id"],
            _component_output(component),
            executor_binding="session-a",
            model_identity="model-a",
        )
        state = session.resume("run-dispatch-effect")
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
        "parameters": {
            "expected_blob_sha": "a" * 40,
            "content_digest": "b" * 64,
        },
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


def test_authorized_dispatch_passes_exact_envelope_and_replay_does_not_call(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    request = _pending_execute(root)
    authorized = _authorized(root, request, "allowed-v1")
    calls: list[dict] = []

    def provider(envelope: dict) -> dict:
        calls.append(envelope)
        return {"remote_commit_sha": "c" * 40, "verified_readback": True}

    first = dispatch_work_effect(root, authorization=authorized, callback=provider)
    assert first["dispatched"] is True
    assert first["replayed"] is False
    assert len(calls) == 1
    assert calls[0] == {
        "action": _action(),
        "authorization_digest": authorized.authorization_digest,
        "idempotency_key": authorized.idempotency_key,
    }
    verified = verify_work_effect(
        root, run_id=request["run_id"], effect_id="allowed-v1"
    )
    assert verified["status"] == "completed"
    assert first["receipt_digest"] == verified["receipt_digest"]

    replay = dispatch_work_effect(root, authorization=authorized, callback=provider)
    assert replay["dispatched"] is False
    assert replay["replayed"] is True
    assert replay["receipt_digest"] == first["receipt_digest"]
    assert replay["result"] == first["result"]
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({"executor_binding": "session-b"}, "typed authorization identity"),
        ({"model_identity": "model-b"}, "typed authorization identity"),
        ({"request_digest": "f" * 64}, "typed authorization identity"),
        ({"authorization_digest": "e" * 64}, "typed authorization identity"),
        ({"idempotency_key": "d" * 64}, "typed authorization identity"),
        ({"action": _action("different.txt")}, "typed authorization identity"),
    ],
)
def test_forged_or_drifted_typed_authorization_never_calls_provider(
    tmp_path: Path, mutation: dict, match: str
) -> None:
    root = _root(tmp_path)
    request = _pending_execute(root)
    authorized = _authorized(root, request, "drift-v1")
    forged = replace(authorized, **mutation)
    calls = 0

    def provider(_: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"verified_readback": True}

    with pytest.raises(WorkEffectError, match=match):
        dispatch_work_effect(root, authorization=forged, callback=provider)
    assert calls == 0
    assert verify_work_effect(
        root, run_id=request["run_id"], effect_id="drift-v1"
    )["status"] == "authorized"


def test_unprepared_unauthorized_private_and_tampered_states_never_call(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    request = _pending_execute(root)
    calls = 0

    def provider(_: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"verified_readback": True}

    with pytest.raises(WorkEffectError, match="malformed effect plan"):
        load_authorized_work_effect(
            root,
            run_id=request["run_id"],
            effect_id="missing-v1",
            executor_binding="session-a",
            model_identity="model-a",
        )

    prepare_work_effect(
        root,
        run_id=request["run_id"],
        effect_id="prepared-v1",
        invocation_id=request["invocation_id"],
        action=_action(),
        executor_binding="session-a",
        model_identity="model-a",
    )
    with pytest.raises(WorkEffectError, match="not authorized"):
        load_authorized_work_effect(
            root,
            run_id=request["run_id"],
            effect_id="prepared-v1",
            executor_binding="session-a",
            model_identity="model-a",
        )

    with pytest.raises(WorkEffectError, match="forbidden private field"):
        prepare_work_effect(
            root,
            run_id=request["run_id"],
            effect_id="private-v1",
            invocation_id=request["invocation_id"],
            action={**_action(), "credentials": "secret"},
            executor_binding="session-a",
            model_identity="model-a",
        )

    authorized = _authorized(root, request, "tampered-v1")
    auth_path = (
        root
        / ".continual"
        / "runs"
        / request["run_id"]
        / "external-effects"
        / "tampered-v1"
        / "authorization.json"
    )
    tampered = json.loads(auth_path.read_text(encoding="utf-8"))
    tampered["idempotency_key"] = "0" * 64
    auth_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(WorkEffectError, match="tampered effect authorization"):
        dispatch_work_effect(root, authorization=authorized, callback=provider)
    assert calls == 0


def test_completed_state_with_forged_token_is_denied_without_replay_callback(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    request = _pending_execute(root)
    authorized = _authorized(root, request, "completed-v1")
    calls = 0

    def provider(_: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"verified_readback": True}

    dispatch_work_effect(root, authorization=authorized, callback=provider)
    assert calls == 1
    forged = replace(authorized, authorization_digest="f" * 64)
    with pytest.raises(WorkEffectError, match="typed authorization identity"):
        dispatch_work_effect(root, authorization=forged, callback=provider)
    assert calls == 1


def test_callback_failure_is_not_falsely_receipted_and_retry_is_fail_closed(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    request = _pending_execute(root)
    authorized = _authorized(root, request, "callback-failure-v1")

    def failed(_: dict) -> dict:
        raise RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        dispatch_work_effect(root, authorization=authorized, callback=failed)
    assert verify_work_effect(
        root, run_id=request["run_id"], effect_id="callback-failure-v1"
    )["status"] == "authorized"

    retries = 0

    def retry(_: dict) -> dict:
        nonlocal retries
        retries += 1
        return {"verified_readback": True}

    with pytest.raises(WorkEffectError, match="effect dispatch already claimed"):
        dispatch_work_effect(root, authorization=authorized, callback=retry)
    assert retries == 0


def test_private_provider_result_is_rejected_after_the_single_callback(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    request = _pending_execute(root)
    authorized = _authorized(root, request, "private-result-v1")

    calls = 0

    def private(_: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"credentials": "must-not-persist"}

    with pytest.raises(WorkEffectError, match="forbidden private field"):
        dispatch_work_effect(root, authorization=authorized, callback=private)
    assert calls == 1
    assert verify_work_effect(
        root, run_id=request["run_id"], effect_id="private-result-v1"
    )["status"] == "authorized"
