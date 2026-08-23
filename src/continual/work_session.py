from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .contracts import validate_component_output
from .store import Store


_INVOCATION_ID = re.compile(r"^invoke-[0-9a-f]{24}$")
_FORBIDDEN_KEYS = {
    "api_key",
    "authorization",
    "chain_of_thought",
    "cookie",
    "credentials",
    "hidden_reasoning",
    "password",
    "raw_system_prompt",
    "scratchpad",
    "secret",
    "system_prompt",
}
_SECRET_TEXT = re.compile(
    r"(?:\bBearer\s+[A-Za-z0-9._~+/=-]{12,}|\b(?:ghp|github_pat|sk)-[A-Za-z0-9_-]{12,})",
    re.IGNORECASE,
)


class WorkSessionError(ValueError):
    """Raised when a Work-model request or response fails closed."""


class WorkModelPending(RuntimeError):
    """Signals that an O component is frozen pending one Work-model response."""

    def __init__(self, invocation_id: str, request_ref: str, request_digest: str):
        super().__init__(f"Work-model response required: {invocation_id}")
        self.invocation_id = invocation_id
        self.request_ref = request_ref
        self.request_digest = request_digest

    def as_dict(self) -> dict[str, str]:
        return {
            "invocation_id": self.invocation_id,
            "request_ref": self.request_ref,
            "request_digest": self.request_digest,
        }


def _digest_matches(
    store: Store,
    record: Mapping[str, Any],
    *,
    digest_field: str,
    volatile_fields: tuple[str, ...],
) -> bool:
    supplied = record.get(digest_field)
    if not isinstance(supplied, str):
        return False
    body = deepcopy(dict(record))
    body.pop(digest_field, None)
    for field in volatile_fields:
        body.pop(field, None)
    return supplied == store.stable_digest(body, length=64)


def _walk_public(value: Any, path: str = "output") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_KEYS:
                raise WorkSessionError(f"forbidden private field at {path}.{key}")
            _walk_public(child, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _walk_public(child, f"{path}[{index}]")
        return
    if isinstance(value, str) and _SECRET_TEXT.search(value):
        raise WorkSessionError(f"secret-like text is forbidden at {path}")


def _verified_request(store: Store, request_path: Path) -> dict[str, Any]:
    request = store.read_json(request_path, None)
    invocation_id = request_path.parent.name
    if not isinstance(request, dict):
        raise WorkSessionError(f"malformed Work request: {invocation_id}")
    if request.get("invocation_id") != invocation_id or not _digest_matches(
        store,
        request,
        digest_field="request_digest",
        volatile_fields=("created_at",),
    ):
        raise WorkSessionError(f"tampered Work request: {invocation_id}")
    return request


def _verified_response(
    store: Store,
    request: Mapping[str, Any],
    response_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    response = store.read_json(response_path, None)
    invocation_id = response_path.parent.name
    if not isinstance(response, dict):
        raise WorkSessionError(f"malformed Work response: {invocation_id}")
    if response.get("invocation_id") != invocation_id:
        raise WorkSessionError("Work response invocation_id mismatch")
    if response.get("request_digest") != request.get("request_digest"):
        raise WorkSessionError("Work response request_digest mismatch")
    if response.get("executor_binding") != request.get("executor_binding"):
        raise WorkSessionError("Work response executor_binding mismatch")
    if response.get("model_identity") != request.get("model_identity"):
        raise WorkSessionError("Work response model_identity mismatch")
    if not isinstance(response.get("model_verified"), bool):
        raise WorkSessionError("Work response model_verified must be boolean")
    if not _digest_matches(
        store,
        response,
        digest_field="response_digest",
        volatile_fields=("received_at",),
    ):
        raise WorkSessionError("Work response digest mismatch")
    output = response.get("output")
    _walk_public(output)
    component = request.get("component")
    if not isinstance(component, str):
        raise WorkSessionError("frozen request component is malformed")
    request_payload = request.get("payload")
    evaluator_mode = (
        request_payload.get("mode")
        if component == "candidate_evaluate" and isinstance(request_payload, Mapping)
        else None
    )
    validate_component_output(component, output, evaluator_mode=evaluator_mode)
    if response.get("output_digest") != store.stable_digest(output, length=64):
        raise WorkSessionError("Work response output_digest mismatch")
    return response, deepcopy(output)


class WorkModelClient:
    """ModelClient-compatible adapter backed by immutable Work invocation records."""

    # Marks this client as the only provider allowed to complete an invocation
    # journal left in ``awaiting_work_model``. The Engine refuses to answer such
    # a frozen Work request with any other (e.g. default API) provider.
    provides_work_responses = True

    def __init__(
        self,
        root: Path,
        *,
        run_id: str,
        executor_binding: str,
        model_identity: str,
    ) -> None:
        if not executor_binding.strip():
            raise WorkSessionError("executor_binding must be non-empty")
        if not model_identity.strip():
            raise WorkSessionError("model_identity must be non-empty")
        self.root = root.resolve()
        self.store = Store(self.root)
        self.run_id = run_id
        self.executor_binding = executor_binding
        self.model = model_identity

    @property
    def invocation_root(self) -> Path:
        return self.root / ".continual" / "work-model" / "invocations"

    def _request_for(
        self,
        component: str,
        payload: dict[str, Any],
        prompt_path: str,
    ) -> tuple[str, dict[str, Any]]:
        prompt = (self.root / prompt_path).resolve()
        if prompt != self.root and self.root not in prompt.parents:
            raise WorkSessionError("prompt_path escapes repository")
        prompt_content = prompt.read_text(encoding="utf-8")
        payload_digest = self.store.stable_digest(payload, length=64)
        prompt_digest = self.store.stable_digest(prompt_content, length=64)
        invocation_id = "invoke-" + self.store.stable_digest(
            {
                "run_id": self.run_id,
                "component": component,
                "payload_digest": payload_digest,
                "prompt_path": prompt_path,
                "prompt_digest": prompt_digest,
                "executor_binding": self.executor_binding,
            }
        )
        request_body = {
            "schema_version": 1,
            "invocation_id": invocation_id,
            "run_id": self.run_id,
            "component": component,
            "provider": "chatgpt-work-external",
            "executor_binding": self.executor_binding,
            "model_identity": self.model,
            "prompt_path": prompt_path,
            "prompt_digest": prompt_digest,
            "prompt_content": prompt_content,
            "payload_digest": payload_digest,
            "payload": deepcopy(payload),
            "contract": {
                "response_shape": "component output with result, fragment, and local_learn except Learn",
                "private_reasoning_forbidden": True,
                "secrets_forbidden": True,
            },
        }
        request_body["request_digest"] = self.store.stable_digest(request_body, length=64)
        return invocation_id, request_body

    def call(
        self,
        component: str,
        payload: dict[str, Any],
        *,
        prompt_path: str,
    ) -> dict[str, Any]:
        invocation_id, request = self._request_for(component, payload, prompt_path)
        directory = self.invocation_root / invocation_id
        request_path = directory / "request.json"
        response_path = directory / "response.json"
        existing = self.store.read_json(request_path, None)
        if existing is None:
            request["created_at"] = self.store.utc_now()
            self.store.atomic_json(request_path, request)
        elif (
            not isinstance(existing, dict)
            or not _digest_matches(
                self.store,
                existing,
                digest_field="request_digest",
                volatile_fields=("created_at",),
            )
            or existing.get("request_digest") != request["request_digest"]
        ):
            raise WorkSessionError(f"immutable Work request conflict: {invocation_id}")

        response = self.store.read_json(response_path, None)
        request_ref = request_path.relative_to(self.root).as_posix()
        if response is None:
            raise WorkModelPending(invocation_id, request_ref, request["request_digest"])
        _, output = _verified_response(self.store, existing or request, response_path)
        return output


class WorkSession:
    """Run the ordinary O Engine with the current ChatGPT Work model as provider."""

    def __init__(
        self,
        root: Path,
        *,
        executor_binding: str = "current_chatgpt_work_session",
        model_identity: str = "chatgpt-work-model-unverified",
    ) -> None:
        self.root = root.resolve()
        self.executor_binding = executor_binding
        self.model_identity = model_identity
        self.store = Store(self.root)

    def _engine(self, run_id: str):
        from .engine import Engine

        return Engine(
            self.root,
            model=WorkModelClient(
                self.root,
                run_id=run_id,
                executor_binding=self.executor_binding,
                model_identity=self.model_identity,
            ),
        )

    def start(
        self,
        request: str,
        *,
        max_steps: int = 64,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        run_id = run_id or self.store.new_id("run")
        engine = self._engine(run_id)
        engine.start(request, max_steps=max_steps, run_id=run_id)
        return {
            "run_id": run_id,
            "snapshot": engine.store.snapshot(run_id),
            "pending": pending_work_invocations(self.root, run_id=run_id),
        }

    def resume(self, run_id: str, *, max_steps: int = 64) -> dict[str, Any]:
        engine = self._engine(run_id)
        snapshot = engine.resume(run_id, max_steps=max_steps)
        return {
            "run_id": run_id,
            "snapshot": snapshot,
            "pending": pending_work_invocations(self.root, run_id=run_id),
        }


def pending_work_invocations(root: Path, *, run_id: str | None = None) -> list[dict[str, Any]]:
    root = root.resolve()
    base = root / ".continual" / "work-model" / "invocations"
    if not base.exists():
        return []
    pending: list[dict[str, Any]] = []
    for request_path in sorted(base.glob("invoke-*/request.json")):
        if (request_path.parent / "response.json").exists():
            continue
        store = Store(root)
        request = _verified_request(store, request_path)
        if run_id is not None and request.get("run_id") != run_id:
            continue
        pending.append(request)
    return pending


def verify_work_invocations(root: Path, *, run_id: str | None = None) -> dict[str, Any]:
    root = root.resolve()
    store = Store(root)
    base = root / ".continual" / "work-model" / "invocations"
    requests = 0
    responses = 0
    pending = 0
    invocation_ids: list[str] = []
    if not base.exists():
        return {
            "valid": True,
            "run_id": run_id,
            "requests": 0,
            "responses": 0,
            "pending": 0,
            "invocation_ids": [],
        }
    for request_path in sorted(base.glob("invoke-*/request.json")):
        request = _verified_request(store, request_path)
        if run_id is not None and request.get("run_id") != run_id:
            continue
        requests += 1
        invocation_ids.append(request_path.parent.name)
        response_path = request_path.parent / "response.json"
        if response_path.exists():
            _verified_response(store, request, response_path)
            responses += 1
        else:
            pending += 1
    return {
        "valid": True,
        "run_id": run_id,
        "requests": requests,
        "responses": responses,
        "pending": pending,
        "invocation_ids": invocation_ids,
    }


def submit_work_response(
    root: Path,
    invocation_id: str,
    output: Mapping[str, Any],
    *,
    executor_binding: str,
    model_identity: str,
    model_verified: bool = False,
) -> dict[str, Any]:
    if not _INVOCATION_ID.fullmatch(invocation_id):
        raise WorkSessionError("invalid Work invocation_id")
    root = root.resolve()
    store = Store(root)
    directory = root / ".continual" / "work-model" / "invocations" / invocation_id
    request_path = directory / "request.json"
    if not request_path.exists():
        raise WorkSessionError(f"Work request does not exist: {invocation_id}")
    request = _verified_request(store, request_path)
    if request.get("executor_binding") != executor_binding:
        raise WorkSessionError("executor_binding does not match frozen request")
    if not isinstance(model_identity, str) or not model_identity.strip():
        raise WorkSessionError("model_identity must be non-empty")
    if request.get("model_identity") != model_identity:
        raise WorkSessionError("model_identity does not match frozen request")
    component = request.get("component")
    if not isinstance(component, str):
        raise WorkSessionError("frozen request component is malformed")
    if not isinstance(output, Mapping):
        raise WorkSessionError("Work output must be an object")
    public_output = deepcopy(dict(output))
    _walk_public(public_output)
    request_payload = request.get("payload")
    evaluator_mode = (
        request_payload.get("mode")
        if component == "candidate_evaluate" and isinstance(request_payload, Mapping)
        else None
    )
    validate_component_output(component, public_output, evaluator_mode=evaluator_mode)
    response = {
        "schema_version": 1,
        "invocation_id": invocation_id,
        "request_digest": request.get("request_digest"),
        "executor_binding": executor_binding,
        "model_identity": model_identity,
        "model_verified": bool(model_verified),
        "output_digest": store.stable_digest(public_output, length=64),
        "output": public_output,
    }
    response["response_digest"] = store.stable_digest(response, length=64)
    response_path = directory / "response.json"
    existing = store.read_json(response_path, None)
    if existing is not None:
        if (
            not isinstance(existing, dict)
            or not _digest_matches(
                store,
                existing,
                digest_field="response_digest",
                volatile_fields=("received_at",),
            )
            or existing.get("response_digest") != response["response_digest"]
        ):
            raise WorkSessionError(f"immutable Work response conflict: {invocation_id}")
        return deepcopy(existing)
    response["received_at"] = store.utc_now()
    store.atomic_json(response_path, response)
    return deepcopy(response)
