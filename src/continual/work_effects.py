from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .context_kernel import (
    ContextKernelError,
    build_effect_dispatch_context,
)
from .store import Store
from .work_session import (
    _INVOCATION_ID,
    _digest_matches,
    _verified_request,
    _walk_public,
    WorkSessionError,
)


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class WorkEffectError(ValueError):
    """Raised when an external effect is not bound to the active Work action."""


def _require_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise WorkEffectError(f"invalid {label}")
    return value


def _require_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkEffectError(f"{label} must be non-empty")
    return value


def _public_object(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkEffectError(f"{label} must be an object")
    public = deepcopy(dict(value))
    try:
        _walk_public(public, label)
    except WorkSessionError as exc:
        raise WorkEffectError(str(exc)) from exc
    return public


def _validated_action(value: Mapping[str, Any]) -> dict[str, Any]:
    action = _public_object(value, "action")
    if not isinstance(action.get("kind"), str) or not action["kind"].strip():
        raise WorkEffectError("action.kind must be non-empty")
    if not isinstance(action.get("target"), Mapping) or not action["target"]:
        raise WorkEffectError("action.target must be a non-empty object")
    if "parameters" in action and not isinstance(action["parameters"], Mapping):
        raise WorkEffectError("action.parameters must be an object")
    return action


def _effect_dir(root: Path, run_id: str, effect_id: str) -> Path:
    return (
        root.resolve()
        / ".continual"
        / "runs"
        / run_id
        / "external-effects"
        / effect_id
    )


def _read_record(
    store: Store,
    path: Path,
    *,
    record_type: str,
    digest_field: str,
    timestamp_field: str,
) -> dict[str, Any]:
    try:
        value = store.read_json(path, None)
    except (OSError, ValueError) as exc:
        raise WorkEffectError(
            f"malformed {record_type.replace('_', ' ')}"
        ) from exc
    if not isinstance(value, dict):
        raise WorkEffectError(f"malformed {record_type.replace('_', ' ')}")
    if value.get("record_type") != record_type or not _digest_matches(
        store,
        value,
        digest_field=digest_field,
        volatile_fields=(timestamp_field,),
    ):
        raise WorkEffectError(f"tampered {record_type.replace('_', ' ')}")
    return value


def _create_or_replay(
    store: Store,
    path: Path,
    record: dict[str, Any],
    *,
    record_type: str,
    digest_field: str,
    timestamp_field: str,
    conflict: str,
) -> dict[str, Any]:
    def existing() -> dict[str, Any]:
        current = _read_record(
            store,
            path,
            record_type=record_type,
            digest_field=digest_field,
            timestamp_field=timestamp_field,
        )
        if current.get(digest_field) != record.get(digest_field):
            raise WorkEffectError(conflict)
        return deepcopy(current)

    if path.exists():
        return existing()
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return existing()
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        # A partial exclusive file is intentionally retained. Its failed digest
        # makes all later operations fail closed instead of silently retrying a
        # possibly issued external action.
        raise
    return deepcopy(record)


def _active_execute_request(
    root: Path,
    *,
    run_id: str,
    invocation_id: str,
    executor_binding: str,
    model_identity: str,
) -> dict[str, Any]:
    store = Store(root)
    request_path = (
        root
        / ".continual"
        / "work-model"
        / "invocations"
        / invocation_id
        / "request.json"
    )
    if not request_path.exists():
        raise WorkEffectError("Work request does not exist")
    try:
        request = _verified_request(store, request_path)
    except (OSError, ValueError) as exc:
        raise WorkEffectError(str(exc)) from exc
    if request.get("run_id") != run_id:
        raise WorkEffectError("Work request run_id mismatch")
    if request.get("component") != "execute":
        raise WorkEffectError("external effect requires an Execute Work request")
    if request.get("executor_binding") != executor_binding:
        raise WorkEffectError("executor_binding does not match frozen request")
    if request.get("model_identity") != model_identity:
        raise WorkEffectError("model_identity does not match frozen request")
    if (request_path.parent / "response.json").exists():
        raise WorkEffectError("Execute Work request already has a response")

    awaiting: list[dict[str, Any]] = []
    journal_root = root / ".continual" / "runs" / run_id / "invocations"
    for journal_path in sorted(journal_root.glob("*.json")):
        try:
            journal = store.read_json(journal_path, None)
        except (OSError, ValueError) as exc:
            raise WorkEffectError("malformed native invocation journal") from exc
        if not isinstance(journal, dict):
            raise WorkEffectError("malformed native invocation journal")
        if journal.get("status") == "awaiting_work_model":
            awaiting.append(journal)
    if (
        len(awaiting) != 1
        or awaiting[0].get("work_invocation_id") != invocation_id
    ):
        raise WorkEffectError(
            "effect requires exactly one active native Execute journal"
        )
    journal = awaiting[0]
    request_ref = request_path.relative_to(root).as_posix()
    if (
        journal.get("component") != "execute"
        or journal.get("work_request_ref") != request_ref
        or journal.get("work_request_digest") != request.get("request_digest")
    ):
        raise WorkEffectError("native Execute journal identity mismatch")
    return request


def _dispatch_context(
    root: Path,
    *,
    request: Mapping[str, Any],
    action: Mapping[str, Any],
    store: Store,
) -> dict[str, Any] | None:
    try:
        return build_effect_dispatch_context(
            root,
            request=request,
            action=action,
            store=store,
        )
    except ContextKernelError as exc:
        raise WorkEffectError(str(exc)) from exc


def prepare_work_effect(
    root: Path,
    *,
    run_id: str,
    effect_id: str,
    invocation_id: str,
    action: Mapping[str, Any],
    executor_binding: str,
    model_identity: str,
) -> dict[str, Any]:
    """Persist one exact action plan for the active native Execute request.

    The plan is immutable and contains no credential material. Preparing the
    same plan is idempotent; reusing an effect id for another action fails.
    """

    root = root.resolve()
    run_id = _require_id(run_id, "run_id")
    effect_id = _require_id(effect_id, "effect_id")
    if not isinstance(invocation_id, str) or not _INVOCATION_ID.fullmatch(
        invocation_id
    ):
        raise WorkEffectError("invalid invocation_id")
    executor_binding = _require_text(executor_binding, "executor_binding")
    model_identity = _require_text(model_identity, "model_identity")
    exact_action = _validated_action(action)
    request = _active_execute_request(
        root,
        run_id=run_id,
        invocation_id=invocation_id,
        executor_binding=executor_binding,
        model_identity=model_identity,
    )
    store = Store(root)
    dispatch_context = _dispatch_context(
        root,
        request=request,
        action=exact_action,
        store=store,
    )
    body = {
        "schema_version": 1,
        "record_type": "effect_plan",
        "effect_id": effect_id,
        "run_id": run_id,
        "invocation_id": invocation_id,
        "request_digest": request["request_digest"],
        "executor_binding": executor_binding,
        "model_identity": model_identity,
        "action_digest": store.stable_digest(exact_action, length=64),
        "action": exact_action,
    }
    if dispatch_context is not None:
        body["dispatch_context"] = dispatch_context
        body["dispatch_context_digest"] = dispatch_context[
            "dispatch_context_digest"
        ]
    body["plan_digest"] = store.stable_digest(body, length=64)
    body["prepared_at"] = store.utc_now()
    return _create_or_replay(
        store,
        _effect_dir(root, run_id, effect_id) / "plan.json",
        body,
        record_type="effect_plan",
        digest_field="plan_digest",
        timestamp_field="prepared_at",
        conflict="immutable effect plan conflict",
    )


def authorize_work_effect(
    root: Path,
    *,
    run_id: str,
    effect_id: str,
    invocation_id: str,
    request_digest: str,
    action: Mapping[str, Any],
    executor_binding: str,
    model_identity: str,
) -> dict[str, Any]:
    """Authorize only the exact action frozen by ``prepare_work_effect``."""

    root = root.resolve()
    run_id = _require_id(run_id, "run_id")
    effect_id = _require_id(effect_id, "effect_id")
    if not isinstance(invocation_id, str) or not _INVOCATION_ID.fullmatch(
        invocation_id
    ):
        raise WorkEffectError("invalid invocation_id")
    request_digest = _require_text(request_digest, "request_digest")
    executor_binding = _require_text(executor_binding, "executor_binding")
    model_identity = _require_text(model_identity, "model_identity")
    exact_action = _validated_action(action)
    store = Store(root)
    directory = _effect_dir(root, run_id, effect_id)
    plan = _read_record(
        store,
        directory / "plan.json",
        record_type="effect_plan",
        digest_field="plan_digest",
        timestamp_field="prepared_at",
    )
    request = _active_execute_request(
        root,
        run_id=run_id,
        invocation_id=invocation_id,
        executor_binding=executor_binding,
        model_identity=model_identity,
    )
    if (
        plan.get("run_id") != run_id
        or plan.get("effect_id") != effect_id
        or plan.get("invocation_id") != invocation_id
        or plan.get("request_digest") != request_digest
        or request.get("request_digest") != request_digest
    ):
        raise WorkEffectError("effect identity does not match frozen request")
    if (
        plan.get("executor_binding") != executor_binding
        or plan.get("model_identity") != model_identity
    ):
        raise WorkEffectError("effect executor/model identity mismatch")
    action_digest = store.stable_digest(exact_action, length=64)
    if plan.get("action_digest") != action_digest or plan.get("action") != exact_action:
        raise WorkEffectError("action does not match prepared effect plan")
    dispatch_context = _dispatch_context(
        root,
        request=request,
        action=exact_action,
        store=store,
    )
    if dispatch_context is None:
        if plan.get("dispatch_context") is not None or plan.get(
            "dispatch_context_digest"
        ) is not None:
            raise WorkEffectError("effect dispatch context mismatch")
    elif (
        plan.get("dispatch_context") != dispatch_context
        or plan.get("dispatch_context_digest")
        != dispatch_context["dispatch_context_digest"]
    ):
        raise WorkEffectError("effect dispatch context changed since preparation")

    body = {
        "schema_version": 1,
        "record_type": "effect_authorization",
        "effect_id": effect_id,
        "run_id": run_id,
        "invocation_id": invocation_id,
        "request_digest": request_digest,
        "plan_digest": plan["plan_digest"],
        "action_digest": action_digest,
        "idempotency_key": store.stable_digest(
            {
                "record_type": "work_effect_idempotency",
                "plan_digest": plan["plan_digest"],
            },
            length=64,
        ),
    }
    if dispatch_context is not None:
        body["dispatch_context_digest"] = dispatch_context[
            "dispatch_context_digest"
        ]
    body["authorization_digest"] = store.stable_digest(body, length=64)
    body["authorized_at"] = store.utc_now()
    return _create_or_replay(
        store,
        directory / "authorization.json",
        body,
        record_type="effect_authorization",
        digest_field="authorization_digest",
        timestamp_field="authorized_at",
        conflict="immutable effect authorization conflict",
    )


def complete_work_effect(
    root: Path,
    *,
    run_id: str,
    effect_id: str,
    authorization_digest: str,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist one immutable readback receipt for an authorized action."""

    root = root.resolve()
    run_id = _require_id(run_id, "run_id")
    effect_id = _require_id(effect_id, "effect_id")
    authorization_digest = _require_text(
        authorization_digest, "authorization_digest"
    )
    public_result = _public_object(result, "result")
    store = Store(root)
    directory = _effect_dir(root, run_id, effect_id)
    plan = _read_record(
        store,
        directory / "plan.json",
        record_type="effect_plan",
        digest_field="plan_digest",
        timestamp_field="prepared_at",
    )
    authorization = _read_record(
        store,
        directory / "authorization.json",
        record_type="effect_authorization",
        digest_field="authorization_digest",
        timestamp_field="authorized_at",
    )
    if (
        authorization.get("authorization_digest") != authorization_digest
        or authorization.get("plan_digest") != plan.get("plan_digest")
        or authorization.get("run_id") != run_id
        or authorization.get("effect_id") != effect_id
    ):
        raise WorkEffectError("effect receipt authorization mismatch")

    body = {
        "schema_version": 1,
        "record_type": "effect_receipt",
        "effect_id": effect_id,
        "run_id": run_id,
        "authorization_digest": authorization_digest,
        "result_digest": store.stable_digest(public_result, length=64),
        "result": public_result,
    }
    body["receipt_digest"] = store.stable_digest(body, length=64)
    body["completed_at"] = store.utc_now()
    return _create_or_replay(
        store,
        directory / "receipt.json",
        body,
        record_type="effect_receipt",
        digest_field="receipt_digest",
        timestamp_field="completed_at",
        conflict="immutable effect receipt conflict",
    )


def verify_work_effect(
    root: Path,
    *,
    run_id: str,
    effect_id: str,
) -> dict[str, Any]:
    """Verify every existing record and return the exact durable effect state."""

    root = root.resolve()
    run_id = _require_id(run_id, "run_id")
    effect_id = _require_id(effect_id, "effect_id")
    store = Store(root)
    directory = _effect_dir(root, run_id, effect_id)
    plan = _read_record(
        store,
        directory / "plan.json",
        record_type="effect_plan",
        digest_field="plan_digest",
        timestamp_field="prepared_at",
    )
    if plan.get("run_id") != run_id or plan.get("effect_id") != effect_id:
        raise WorkEffectError("effect plan path identity mismatch")
    authorization_path = directory / "authorization.json"
    receipt_path = directory / "receipt.json"
    authorization: dict[str, Any] | None = None
    receipt: dict[str, Any] | None = None
    status = "prepared"
    if authorization_path.exists():
        authorization = _read_record(
            store,
            authorization_path,
            record_type="effect_authorization",
            digest_field="authorization_digest",
            timestamp_field="authorized_at",
        )
        if (
            authorization.get("plan_digest") != plan.get("plan_digest")
            or authorization.get("run_id") != run_id
            or authorization.get("effect_id") != effect_id
            or authorization.get("action_digest") != plan.get("action_digest")
            or authorization.get("dispatch_context_digest")
            != plan.get("dispatch_context_digest")
        ):
            raise WorkEffectError("effect authorization identity mismatch")
        status = "authorized"
    if receipt_path.exists():
        if authorization is None:
            raise WorkEffectError("effect receipt exists without authorization")
        receipt = _read_record(
            store,
            receipt_path,
            record_type="effect_receipt",
            digest_field="receipt_digest",
            timestamp_field="completed_at",
        )
        if (
            receipt.get("authorization_digest")
            != authorization.get("authorization_digest")
            or receipt.get("run_id") != run_id
            or receipt.get("effect_id") != effect_id
        ):
            raise WorkEffectError("effect receipt identity mismatch")
        status = "completed"
    return {
        "valid": True,
        "status": status,
        "run_id": run_id,
        "effect_id": effect_id,
        "invocation_id": plan.get("invocation_id"),
        "request_digest": plan.get("request_digest"),
        "action": deepcopy(plan.get("action")),
        "plan_digest": plan.get("plan_digest"),
        "authorization_digest": (
            authorization.get("authorization_digest")
            if authorization is not None
            else None
        ),
        "idempotency_key": (
            authorization.get("idempotency_key")
            if authorization is not None
            else None
        ),
        "dispatch_context_digest": (
            authorization.get("dispatch_context_digest")
            if authorization is not None
            else plan.get("dispatch_context_digest")
        ),
        "result": deepcopy(receipt.get("result")) if receipt is not None else None,
        "receipt_digest": (
            receipt.get("receipt_digest") if receipt is not None else None
        ),
    }
