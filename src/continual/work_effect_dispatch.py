from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .store import Store
from .work_effects import (
    WorkEffectError,
    _active_execute_request,
    _create_or_replay,
    _dispatch_context,
    _effect_dir,
    _read_record,
    _validated_action,
    authorize_work_effect,
    complete_work_effect,
    verify_work_effect,
)


EffectCallback = Callable[[dict[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class AuthorizedWorkEffect:
    """Typed, public-data-only capability for one frozen Work effect.

    Construction alone grants nothing. ``dispatch_work_effect`` compares every
    field with the immutable on-disk plan and authorization immediately before
    calling the provider.
    """

    run_id: str
    effect_id: str
    invocation_id: str
    request_digest: str
    executor_binding: str
    model_identity: str
    action: Mapping[str, Any]
    dispatch_context_digest: str | None
    authorization_digest: str
    idempotency_key: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", _validated_action(self.action))


def _verified_state_and_plan(
    root: Path, authorization: AuthorizedWorkEffect
) -> tuple[dict[str, Any], dict[str, Any]]:
    state = verify_work_effect(
        root,
        run_id=authorization.run_id,
        effect_id=authorization.effect_id,
    )
    store = Store(root)
    plan = _read_record(
        store,
        _effect_dir(root, authorization.run_id, authorization.effect_id)
        / "plan.json",
        record_type="effect_plan",
        digest_field="plan_digest",
        timestamp_field="prepared_at",
    )
    expected = {
        "run_id": plan.get("run_id"),
        "effect_id": plan.get("effect_id"),
        "invocation_id": plan.get("invocation_id"),
        "request_digest": plan.get("request_digest"),
        "executor_binding": plan.get("executor_binding"),
        "model_identity": plan.get("model_identity"),
        "action": plan.get("action"),
        "dispatch_context_digest": plan.get("dispatch_context_digest"),
        "authorization_digest": state.get("authorization_digest"),
        "idempotency_key": state.get("idempotency_key"),
    }
    supplied = {
        "run_id": authorization.run_id,
        "effect_id": authorization.effect_id,
        "invocation_id": authorization.invocation_id,
        "request_digest": authorization.request_digest,
        "executor_binding": authorization.executor_binding,
        "model_identity": authorization.model_identity,
        "action": dict(authorization.action),
        "dispatch_context_digest": authorization.dispatch_context_digest,
        "authorization_digest": authorization.authorization_digest,
        "idempotency_key": authorization.idempotency_key,
    }
    if supplied != expected:
        raise WorkEffectError("typed authorization identity mismatch")
    dispatch_path = (
        _effect_dir(root, authorization.run_id, authorization.effect_id)
        / "dispatch.json"
    )
    if dispatch_path.exists():
        dispatch = _read_record(
            store,
            dispatch_path,
            record_type="effect_dispatch_claim",
            digest_field="dispatch_digest",
            timestamp_field="claimed_at",
        )
        if (
            dispatch.get("run_id") != authorization.run_id
            or dispatch.get("effect_id") != authorization.effect_id
            or dispatch.get("authorization_digest")
            != authorization.authorization_digest
            or dispatch.get("idempotency_key") != authorization.idempotency_key
        ):
            raise WorkEffectError("effect dispatch claim identity mismatch")
    return state, plan


def load_authorized_work_effect(
    root: Path,
    *,
    run_id: str,
    effect_id: str,
    executor_binding: str,
    model_identity: str,
) -> AuthorizedWorkEffect:
    """Load and revalidate an existing authorization as a typed capability."""

    root = root.resolve()
    state = verify_work_effect(root, run_id=run_id, effect_id=effect_id)
    if state["status"] == "prepared":
        raise WorkEffectError("effect is not authorized")
    authorization = authorize_work_effect(
        root,
        run_id=run_id,
        effect_id=effect_id,
        invocation_id=state["invocation_id"],
        request_digest=state["request_digest"],
        action=state["action"],
        executor_binding=executor_binding,
        model_identity=model_identity,
    )
    typed = AuthorizedWorkEffect(
        run_id=run_id,
        effect_id=effect_id,
        invocation_id=state["invocation_id"],
        request_digest=state["request_digest"],
        executor_binding=executor_binding,
        model_identity=model_identity,
        action=state["action"],
        dispatch_context_digest=state.get("dispatch_context_digest"),
        authorization_digest=authorization["authorization_digest"],
        idempotency_key=authorization["idempotency_key"],
    )
    _verified_state_and_plan(root, typed)
    return typed


def dispatch_work_effect(
    root: Path,
    *,
    authorization: AuthorizedWorkEffect,
    callback: EffectCallback,
) -> dict[str, Any]:
    """Dispatch once after exact authorization checks, then receipt the result.

    An exact completed replay returns the immutable stored receipt without
    calling the provider. Provider idempotency remains necessary for the crash
    window after the remote effect but before local receipt persistence.
    Callers that bypass this adapter are outside this local enforcement point.
    """

    if not isinstance(authorization, AuthorizedWorkEffect):
        raise WorkEffectError("authorization must be an AuthorizedWorkEffect")
    if not callable(callback):
        raise WorkEffectError("callback must be callable")
    root = root.resolve()
    state, plan = _verified_state_and_plan(root, authorization)
    if state["status"] == "completed":
        return {
            "dispatched": False,
            "replayed": True,
            "run_id": authorization.run_id,
            "effect_id": authorization.effect_id,
            "authorization_digest": authorization.authorization_digest,
            "idempotency_key": authorization.idempotency_key,
            "receipt_digest": state["receipt_digest"],
            "result": deepcopy(state["result"]),
        }
    if state["status"] != "authorized":
        raise WorkEffectError("effect is not authorized")

    exact = authorize_work_effect(
        root,
        run_id=authorization.run_id,
        effect_id=authorization.effect_id,
        invocation_id=authorization.invocation_id,
        request_digest=authorization.request_digest,
        action=authorization.action,
        executor_binding=authorization.executor_binding,
        model_identity=authorization.model_identity,
    )
    if (
        exact.get("authorization_digest") != authorization.authorization_digest
        or exact.get("idempotency_key") != authorization.idempotency_key
    ):
        raise WorkEffectError("typed authorization identity mismatch")

    request = _active_execute_request(
        root,
        run_id=authorization.run_id,
        invocation_id=authorization.invocation_id,
        executor_binding=authorization.executor_binding,
        model_identity=authorization.model_identity,
    )
    current_dispatch_context = _dispatch_context(
        root,
        request=request,
        action=authorization.action,
        store=Store(root),
    )
    current_dispatch_digest = (
        current_dispatch_context.get("dispatch_context_digest")
        if current_dispatch_context is not None
        else None
    )
    if (
        current_dispatch_digest != authorization.dispatch_context_digest
        or current_dispatch_context != plan.get("dispatch_context")
    ):
        raise WorkEffectError("effect dispatch context changed after authorization")

    store = Store(root)
    claim = {
        "schema_version": 1,
        "record_type": "effect_dispatch_claim",
        "run_id": authorization.run_id,
        "effect_id": authorization.effect_id,
        "authorization_digest": authorization.authorization_digest,
        "idempotency_key": authorization.idempotency_key,
        "attempt_id": store.new_id("dispatch"),
    }
    claim["dispatch_digest"] = store.stable_digest(claim, length=64)
    claim["claimed_at"] = store.utc_now()
    _create_or_replay(
        store,
        _effect_dir(root, authorization.run_id, authorization.effect_id)
        / "dispatch.json",
        claim,
        record_type="effect_dispatch_claim",
        digest_field="dispatch_digest",
        timestamp_field="claimed_at",
        conflict="effect dispatch already claimed",
    )

    envelope = {
        "action": deepcopy(dict(authorization.action)),
        "authorization_digest": authorization.authorization_digest,
        "idempotency_key": authorization.idempotency_key,
    }
    result = callback(envelope)
    receipt = complete_work_effect(
        root,
        run_id=authorization.run_id,
        effect_id=authorization.effect_id,
        authorization_digest=authorization.authorization_digest,
        result=result,
    )
    return {
        "dispatched": True,
        "replayed": False,
        "run_id": authorization.run_id,
        "effect_id": authorization.effect_id,
        "authorization_digest": authorization.authorization_digest,
        "idempotency_key": authorization.idempotency_key,
        "receipt_digest": receipt["receipt_digest"],
        "result": deepcopy(receipt["result"]),
    }
