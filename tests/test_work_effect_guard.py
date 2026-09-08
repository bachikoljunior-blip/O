from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from continual.work_effects import (
    WorkEffectError,
    authorize_work_effect,
    complete_work_effect,
    prepare_work_effect,
    verify_work_effect,
)
from continual.work_session import (
    WorkModelClient,
    WorkModelPending,
    WorkSession,
    submit_work_response,
)


def _root(tmp_path: Path) -> Path:
    shutil.copytree(Path("prompts"), tmp_path / "prompts")
    return tmp_path


def _component_output(component: str) -> dict:
    if component == "entry":
        result = {"objective": "exercise one guarded external effect"}
    elif component == "root":
        result = {"component": "execute", "goal": "perform one exact effect"}
    else:  # pragma: no cover - fixture misuse must not silently expand
        raise AssertionError(component)
    return {
        "result": result,
        "local_learn": {"decision": "NO_CHANGE", "candidates": []},
        "fragment": {
            "component": component,
            "observations": [f"completed {component}"],
        },
    }


def _native_execute_pending(root: Path) -> tuple[WorkSession, dict]:
    session = WorkSession(
        root,
        executor_binding="session-a",
        model_identity="model-a",
    )
    run_id = "run-work-effect-guard"
    state = session.start("guard one exact action", run_id=run_id)
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
        state = session.resume(run_id)
    request = state["pending"][0]
    assert request["component"] == "execute"
    return session, request


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


def _bytes_under(path: Path) -> dict[str, bytes]:
    if not path.exists():
        return {}
    return {
        child.relative_to(path).as_posix(): child.read_bytes()
        for child in sorted(path.rglob("*"))
        if child.is_file()
    }


def test_exact_native_execute_action_is_bound_idempotent_and_immutable(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    _, request = _native_execute_pending(root)
    run_id = request["run_id"]
    effect_id = "publish-safe-file-v1"
    effect_dir = (
        root / ".continual" / "runs" / run_id / "external-effects" / effect_id
    )

    plan = prepare_work_effect(
        root,
        run_id=run_id,
        effect_id=effect_id,
        invocation_id=request["invocation_id"],
        action=_action(),
        executor_binding="session-a",
        model_identity="model-a",
    )
    assert plan["request_digest"] == request["request_digest"]
    assert prepare_work_effect(
        root,
        run_id=run_id,
        effect_id=effect_id,
        invocation_id=request["invocation_id"],
        action=_action(),
        executor_binding="session-a",
        model_identity="model-a",
    )["plan_digest"] == plan["plan_digest"]

    before_denial = _bytes_under(effect_dir)
    with pytest.raises(WorkEffectError, match="immutable effect plan conflict"):
        prepare_work_effect(
            root,
            run_id=run_id,
            effect_id=effect_id,
            invocation_id=request["invocation_id"],
            action=_action("different.txt"),
            executor_binding="session-a",
            model_identity="model-a",
        )
    assert _bytes_under(effect_dir) == before_denial

    with pytest.raises(WorkEffectError, match="action does not match"):
        authorize_work_effect(
            root,
            run_id=run_id,
            effect_id=effect_id,
            invocation_id=request["invocation_id"],
            request_digest=request["request_digest"],
            action=_action("primary.txt"),
            executor_binding="session-a",
            model_identity="model-a",
        )
    assert _bytes_under(effect_dir) == before_denial

    with pytest.raises(WorkEffectError, match="effect identity"):
        authorize_work_effect(
            root,
            run_id=run_id,
            effect_id=effect_id,
            invocation_id=request["invocation_id"],
            request_digest="f" * 64,
            action=_action(),
            executor_binding="session-a",
            model_identity="model-a",
        )
    assert _bytes_under(effect_dir) == before_denial

    with pytest.raises(WorkEffectError, match="executor_binding"):
        authorize_work_effect(
            root,
            run_id=run_id,
            effect_id=effect_id,
            invocation_id=request["invocation_id"],
            request_digest=request["request_digest"],
            action=_action(),
            executor_binding="session-b",
            model_identity="model-a",
        )
    assert _bytes_under(effect_dir) == before_denial

    authorization = authorize_work_effect(
        root,
        run_id=run_id,
        effect_id=effect_id,
        invocation_id=request["invocation_id"],
        request_digest=request["request_digest"],
        action=_action(),
        executor_binding="session-a",
        model_identity="model-a",
    )
    replay = authorize_work_effect(
        root,
        run_id=run_id,
        effect_id=effect_id,
        invocation_id=request["invocation_id"],
        request_digest=request["request_digest"],
        action=_action(),
        executor_binding="session-a",
        model_identity="model-a",
    )
    assert replay["authorization_digest"] == authorization["authorization_digest"]
    assert replay["idempotency_key"] == authorization["idempotency_key"]

    result = {
        "remote_commit_sha": "c" * 40,
        "verified_readback": True,
    }
    receipt = complete_work_effect(
        root,
        run_id=run_id,
        effect_id=effect_id,
        authorization_digest=authorization["authorization_digest"],
        result=result,
    )
    assert complete_work_effect(
        root,
        run_id=run_id,
        effect_id=effect_id,
        authorization_digest=authorization["authorization_digest"],
        result=result,
    )["receipt_digest"] == receipt["receipt_digest"]

    receipt_path = effect_dir / "receipt.json"
    first_receipt = receipt_path.read_bytes()
    with pytest.raises(WorkEffectError, match="immutable effect receipt conflict"):
        complete_work_effect(
            root,
            run_id=run_id,
            effect_id=effect_id,
            authorization_digest=authorization["authorization_digest"],
            result={"remote_commit_sha": "d" * 40, "verified_readback": True},
        )
    assert receipt_path.read_bytes() == first_receipt

    verified = verify_work_effect(root, run_id=run_id, effect_id=effect_id)
    assert verified["valid"] is True
    assert verified["status"] == "completed"
    assert verified["action"] == _action()
    assert verified["result"] == result


def test_orphan_tampered_and_private_effects_fail_before_authorization(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    run_id = "run-orphan-effect"
    client = WorkModelClient(
        root,
        run_id=run_id,
        executor_binding="session-a",
        model_identity="model-a",
    )
    with pytest.raises(WorkModelPending) as pending:
        client.call(
            "execute",
            {"execution_unit": {"goal": "orphan request"}},
            prompt_path="prompts/execute.md",
        )
    orphan_id = pending.value.invocation_id
    effect_dir = (
        root / ".continual" / "runs" / run_id / "external-effects" / "orphan-v1"
    )
    with pytest.raises(WorkEffectError, match="active native Execute journal"):
        prepare_work_effect(
            root,
            run_id=run_id,
            effect_id="orphan-v1",
            invocation_id=orphan_id,
            action=_action(),
            executor_binding="session-a",
            model_identity="model-a",
        )
    assert not effect_dir.exists()

    _, request = _native_execute_pending(root)
    run_id = request["run_id"]

    wrong_run_dir = (
        root
        / ".continual"
        / "runs"
        / "run-other-effect"
        / "external-effects"
        / "cross-run-v1"
    )
    with pytest.raises(WorkEffectError, match="run_id mismatch"):
        prepare_work_effect(
            root,
            run_id="run-other-effect",
            effect_id="cross-run-v1",
            invocation_id=request["invocation_id"],
            action=_action(),
            executor_binding="session-a",
            model_identity="model-a",
        )
    assert not wrong_run_dir.exists()

    with pytest.raises(WorkEffectError, match="model_identity"):
        prepare_work_effect(
            root,
            run_id=run_id,
            effect_id="wrong-model-v1",
            invocation_id=request["invocation_id"],
            action=_action(),
            executor_binding="session-a",
            model_identity="model-b",
        )
    assert not (
        root
        / ".continual"
        / "runs"
        / run_id
        / "external-effects"
        / "wrong-model-v1"
    ).exists()

    effect_id = "tamper-v1"
    plan = prepare_work_effect(
        root,
        run_id=run_id,
        effect_id=effect_id,
        invocation_id=request["invocation_id"],
        action=_action(),
        executor_binding="session-a",
        model_identity="model-a",
    )
    plan_path = (
        root / ".continual" / "runs" / run_id / "external-effects" / effect_id / "plan.json"
    )
    tampered = json.loads(plan_path.read_text(encoding="utf-8"))
    tampered["action"]["target"]["path"] = "primary.txt"
    plan_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(WorkEffectError, match="tampered effect plan"):
        authorize_work_effect(
            root,
            run_id=run_id,
            effect_id=effect_id,
            invocation_id=request["invocation_id"],
            request_digest=request["request_digest"],
            action=_action(),
            executor_binding="session-a",
            model_identity="model-a",
        )
    assert not (plan_path.parent / "authorization.json").exists()

    with pytest.raises(WorkEffectError, match="forbidden private field"):
        prepare_work_effect(
            root,
            run_id=run_id,
            effect_id="private-v1",
            invocation_id=request["invocation_id"],
            action={**_action(), "credentials": "must-not-persist"},
            executor_binding="session-a",
            model_identity="model-a",
        )
    assert plan["action"] == _action()


def test_authorization_tampering_and_private_receipt_fail_closed(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    _, request = _native_execute_pending(root)
    run_id = request["run_id"]
    effect_id = "authorization-tamper-v1"
    prepare_work_effect(
        root,
        run_id=run_id,
        effect_id=effect_id,
        invocation_id=request["invocation_id"],
        action=_action(),
        executor_binding="session-a",
        model_identity="model-a",
    )
    authorization = authorize_work_effect(
        root,
        run_id=run_id,
        effect_id=effect_id,
        invocation_id=request["invocation_id"],
        request_digest=request["request_digest"],
        action=_action(),
        executor_binding="session-a",
        model_identity="model-a",
    )
    effect_dir = (
        root / ".continual" / "runs" / run_id / "external-effects" / effect_id
    )
    with pytest.raises(WorkEffectError, match="forbidden private field"):
        complete_work_effect(
            root,
            run_id=run_id,
            effect_id=effect_id,
            authorization_digest=authorization["authorization_digest"],
            result={"credentials": "must-not-persist"},
        )
    assert not (effect_dir / "receipt.json").exists()

    authorization_path = effect_dir / "authorization.json"
    tampered = json.loads(authorization_path.read_text(encoding="utf-8"))
    tampered["action_digest"] = "0" * 64
    authorization_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(WorkEffectError, match="tampered effect authorization"):
        verify_work_effect(root, run_id=run_id, effect_id=effect_id)
    with pytest.raises(WorkEffectError, match="tampered effect authorization"):
        complete_work_effect(
            root,
            run_id=run_id,
            effect_id=effect_id,
            authorization_digest=authorization["authorization_digest"],
            result={"verified_readback": True},
        )
    assert not (effect_dir / "receipt.json").exists()
