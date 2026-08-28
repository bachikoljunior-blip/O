from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .context_kernel import (
    SEMANTIC_CONTEXT_COMPONENTS,
    ContextKernelError,
    build_decision_context,
    verify_decision_context_manifest,
)
from .contracts import validate_component_output
from .continuity_preflight import assert_work_resume_continuity_preflight
from .store import Store


_INVOCATION_ID = re.compile(r"^invoke-[0-9a-f]{24}$")
_ACTIVE_AUTHORITATIVE_STATUSES = {
    "running",
    "checkpointed",
    "interrupted",
    "released",
}
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
    invocation_id = request_path.parent.name
    try:
        request = store.read_json(request_path, None)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkSessionError(f"malformed Work request: {invocation_id}") from exc
    if not isinstance(request, dict):
        raise WorkSessionError(f"malformed Work request: {invocation_id}")
    if request.get("invocation_id") != invocation_id or not _digest_matches(
        store,
        request,
        digest_field="request_digest",
        volatile_fields=("created_at",),
    ):
        raise WorkSessionError(f"tampered Work request: {invocation_id}")
    component = request.get("component")
    payload = request.get("payload")
    if isinstance(payload, Mapping) and "decision_context" in payload:
        if not isinstance(component, str):
            raise WorkSessionError("Work request component is malformed")
        try:
            verify_decision_context_manifest(
                payload["decision_context"],
                store=store,
                expected_component=component,
            )
        except ContextKernelError as exc:
            raise WorkSessionError(f"invalid frozen decision context: {exc}") from exc
    return request


def _verified_response(
    store: Store,
    request: Mapping[str, Any],
    response_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    invocation_id = response_path.parent.name
    try:
        response = store.read_json(response_path, None)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkSessionError(f"malformed Work response: {invocation_id}") from exc
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


def verified_work_invocation(root: Path, invocation_id: str) -> dict[str, Any]:
    """Read and fully verify one completed immutable Work invocation.

    This is the public, fail-closed boundary for consumers that need to bind
    downstream evidence to the native Work request/response journal.  It
    deliberately reuses the same digest, executor/model binding, public-output,
    and component-contract checks used during normal replay.
    """

    if not _INVOCATION_ID.fullmatch(invocation_id):
        raise WorkSessionError("invalid Work invocation_id")
    root = root.resolve()
    store = Store(root)
    directory = store.base / "work-model" / "invocations" / invocation_id
    request_path = directory / "request.json"
    response_path = directory / "response.json"
    if not request_path.is_file():
        raise WorkSessionError(f"Work request does not exist: {invocation_id}")
    if not response_path.is_file():
        raise WorkSessionError(f"Work response does not exist: {invocation_id}")
    request = _verified_request(store, request_path)
    response, output = _verified_response(store, request, response_path)
    return {
        "request": deepcopy(request),
        "response": deepcopy(response),
        "output": output,
    }


def verified_work_request(root: Path, invocation_id: str) -> dict[str, Any]:
    """Read and fully verify one immutable Work request, response optional.

    Pending checkpoint references need to prove the exact frozen request before
    a response exists.  This public boundary deliberately shares the request
    digest and frozen DecisionContext checks used by completed invocation
    verification instead of creating a weaker checkpoint-specific parser.
    """

    if not _INVOCATION_ID.fullmatch(invocation_id):
        raise WorkSessionError("invalid Work invocation_id")
    root = root.resolve()
    store = Store(root)
    request_path = (
        store.base
        / "work-model"
        / "invocations"
        / invocation_id
        / "request.json"
    )
    if not request_path.is_file():
        raise WorkSessionError(f"Work request does not exist: {invocation_id}")
    return deepcopy(_verified_request(store, request_path))


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
        effective_payload = deepcopy(payload)
        if component in SEMANTIC_CONTEXT_COMPONENTS:
            if "decision_context" in effective_payload:
                raise WorkSessionError(
                    f"{component} payload may not inject an outer decision_context"
                )
            try:
                decision_context = build_decision_context(
                    self.root,
                    run_id=self.run_id,
                    component=component,
                    payload_snapshot=effective_payload.get("snapshot", {}),
                    store=self.store,
                )
            except ContextKernelError as exc:
                raise WorkSessionError(f"Context Kernel failed closed: {exc}") from exc
            if decision_context is not None:
                effective_payload["decision_context"] = decision_context
        prompt = (self.root / prompt_path).resolve()
        if prompt != self.root and self.root not in prompt.parents:
            raise WorkSessionError("prompt_path escapes repository")
        prompt_content = prompt.read_text(encoding="utf-8")
        payload_digest = self.store.stable_digest(effective_payload, length=64)
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
            "payload": effective_payload,
            "contract": {
                "response_shape": "component output with result, fragment, and local_learn except Learn",
                "private_reasoning_forbidden": True,
                "secrets_forbidden": True,
            },
        }
        request_body["request_digest"] = self.store.stable_digest(request_body, length=64)
        return invocation_id, request_body

    def preflight_call(
        self,
        component: str,
        payload: dict[str, Any],
        *,
        prompt_path: str,
    ) -> None:
        """Validate a new semantic request before the native journal mutates.

        Engine calls this hook only for a new invocation.  Bound replay skips
        it and continues to use the exact already-frozen request.
        """

        del prompt_path
        if component not in SEMANTIC_CONTEXT_COMPONENTS:
            return
        if "decision_context" in payload:
            raise WorkSessionError(
                f"{component} payload may not inject an outer decision_context"
            )
        try:
            build_decision_context(
                self.root,
                run_id=self.run_id,
                component=component,
                payload_snapshot=payload.get("snapshot", {}),
                store=self.store,
            )
        except ContextKernelError as exc:
            raise WorkSessionError(f"Context Kernel failed closed: {exc}") from exc

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

    def resume_bound(
        self,
        component: str,
        payload: dict[str, Any],
        *,
        prompt_path: str,
        invocation_id: str,
        request_ref: str,
        request_digest: str,
    ) -> dict[str, Any]:
        """Resume the exact immutable Work request named by the native journal.

        A pending semantic request may outlive a heartbeat or inbox update. Rebuilding
        its Context Kernel manifest would mint a second Work request for the same
        native invocation. The native journal is therefore the sole resume
        authority: source-clock changes affect the next semantic boundary, not the
        already-frozen request.
        """

        if not isinstance(invocation_id, str) or not _INVOCATION_ID.fullmatch(
            invocation_id
        ):
            raise WorkSessionError("invalid bound Work invocation_id")
        if not isinstance(request_ref, str) or not request_ref:
            raise WorkSessionError("invalid bound Work request_ref")
        if not isinstance(request_digest, str) or not request_digest:
            raise WorkSessionError("invalid bound Work request_digest")
        request_path = (self.root / request_ref).resolve()
        expected_path = self.invocation_root / invocation_id / "request.json"
        if request_path != expected_path:
            raise WorkSessionError("bound Work request_ref mismatch")
        request = _verified_request(self.store, request_path)
        if (
            request.get("request_digest") != request_digest
            or request.get("run_id") != self.run_id
            or request.get("component") != component
            or request.get("prompt_path") != prompt_path
            or request.get("executor_binding") != self.executor_binding
            or request.get("model_identity") != self.model
        ):
            raise WorkSessionError("bound Work request identity mismatch")
        frozen_payload = request.get("payload")
        if not isinstance(frozen_payload, Mapping):
            raise WorkSessionError("bound Work request payload is malformed")
        outer_payload = deepcopy(dict(frozen_payload))
        if component in SEMANTIC_CONTEXT_COMPONENTS:
            outer_payload.pop("decision_context", None)
        if self.store.stable_digest(outer_payload, length=64) != self.store.stable_digest(
            payload, length=64
        ):
            raise WorkSessionError("bound Work outer payload mismatch")

        response_path = request_path.parent / "response.json"
        if not response_path.is_file():
            raise WorkModelPending(invocation_id, request_ref, request_digest)
        _, output = _verified_response(self.store, request, response_path)
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

    def _assert_resume_identity(self, run_id: str) -> None:
        """Bind a resume to the exact Work request already frozen by the run.

        The Work invocation id includes the executor binding. Without this
        preflight, resuming an awaiting_work_model native invocation under
        a different binding can mint a second request for the same component
        instead of consuming the request recorded in the native journal.
        Read the journal and immutable request before constructing/running the
        Engine so a mismatch has no native or Work-model side effects.
        """

        journal_root = self.store.run_dir(run_id) / "invocations"
        if not journal_root.exists():
            return
        work_root = (
            self.root / ".continual" / "work-model" / "invocations"
        ).resolve()
        awaiting: list[dict[str, Any]] = []
        for journal_path in sorted(journal_root.glob("*.json")):
            journal = self.store.read_json(journal_path, None)
            if not isinstance(journal, dict):
                raise WorkSessionError(
                    f"malformed native invocation journal: {journal_path.name}"
                )
            if journal.get("status") != "awaiting_work_model":
                continue
            invocation_id = journal.get("work_invocation_id")
            request_ref = journal.get("work_request_ref")
            request_digest = journal.get("work_request_digest")
            if (
                not isinstance(invocation_id, str)
                or not _INVOCATION_ID.fullmatch(invocation_id)
                or not isinstance(request_ref, str)
                or not request_ref
                or not isinstance(request_digest, str)
                or not request_digest
            ):
                raise WorkSessionError(
                    f"malformed pending Work identity: {journal_path.name}"
                )
            request_path = (self.root / request_ref).resolve()
            expected_path = work_root / invocation_id / "request.json"
            if request_path != expected_path:
                raise WorkSessionError(
                    f"pending Work request_ref mismatch: {journal_path.name}"
                )
            request = _verified_request(self.store, request_path)
            if (
                request.get("run_id") != run_id
                or request.get("invocation_id") != invocation_id
                or request.get("request_digest") != request_digest
                or request.get("component") != journal.get("component")
            ):
                raise WorkSessionError(
                    f"pending Work request identity mismatch: {journal_path.name}"
                )
            response_path = request_path.parent / "response.json"
            received_at: str | None = None
            if response_path.exists():
                response, _ = _verified_response(self.store, request, response_path)
                raw_received_at = response.get("received_at")
                if not isinstance(raw_received_at, str) or not raw_received_at.strip():
                    raise WorkSessionError(
                        f"malformed answered Work identity: {journal_path.name}"
                    )
                received_at = raw_received_at
            awaiting.append(
                {
                    "request": request,
                    "response_received_at": received_at,
                }
            )

        unanswered = [
            item for item in awaiting if item["response_received_at"] is None
        ]
        if len(unanswered) > 1:
            raise WorkSessionError(
                "multiple unanswered Work requests for one native run"
            )
        if not awaiting:
            return
        if unanswered:
            active = unanswered[0]
        else:
            # A recovered native run can retain old awaiting journals whose exact
            # Work responses were later reconstructed and consumed through a newer
            # durable boundary.  They are immutable history, not live requests.
            # When every awaiting journal is already answered, the newest verified
            # response is the only request the current snapshot can consume next.
            active = max(
                awaiting,
                key=lambda item: (
                    item["response_received_at"],
                    str(item["request"].get("created_at", "")),
                    str(item["request"].get("invocation_id", "")),
                ),
            )
        request = active["request"]
        if request.get("executor_binding") != self.executor_binding:
            raise WorkSessionError(
                "executor_binding does not match pending Work request"
            )
        if request.get("model_identity") != self.model_identity:
            raise WorkSessionError("model_identity does not match pending Work request")

    def start(
        self,
        request: str,
        *,
        max_steps: int = 64,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        run_id = run_id or self.store.new_id("run")
        state_path = self.root / "agi" / "WORK_EXECUTION_STATE.json"
        if state_path.exists():
            state = self.store.read_json(state_path, None)
            if not isinstance(state, Mapping):
                raise WorkSessionError(
                    "authoritative Work execution state must be an object"
                )
            status = state.get("status")
            if status in _ACTIVE_AUTHORITATIVE_STATUSES:
                active_run_id = state.get("active_run_id")
                if not isinstance(active_run_id, str) or not active_run_id.strip():
                    raise WorkSessionError(
                        "active authoritative Work state has no native run identity"
                    )
                raise WorkSessionError(
                    "cannot start a new Work run while authoritative run "
                    f"{active_run_id} is {status}; repair or resume its exact "
                    "continuation, or durably supersede it first"
                )
        engine = self._engine(run_id)
        engine.start(request, max_steps=max_steps, run_id=run_id)
        return {
            "run_id": run_id,
            "snapshot": engine.store.snapshot(run_id),
            "pending": pending_work_invocations(self.root, run_id=run_id),
        }

    def resume(self, run_id: str, *, max_steps: int = 64) -> dict[str, Any]:
        assert_work_resume_continuity_preflight(
            self.root,
            run_id=run_id,
            executor_binding=self.executor_binding,
            model_identity=self.model_identity,
        )
        self._assert_resume_identity(run_id)
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
    result = {
        "valid": True,
        "run_id": run_id,
        "requests": requests,
        "responses": responses,
        "pending": pending,
        "invocation_ids": invocation_ids,
    }
    if run_id is not None:
        result.update(_verify_completed_native_artifacts(root, run_id=run_id))
    return result


def _verify_completed_native_artifacts(root: Path, *, run_id: str) -> dict[str, int]:
    """Fail closed when a completed native journal lost Engine-owned artifacts.

    A reconstructed Work response is not a complete native lifecycle by itself.
    The Engine persists the semantic fragment and, for non-Learn components,
    Local Learn before it marks the native journal complete.  Recovery and
    publication verification must preserve that ordering invariant too.
    """

    if re.fullmatch(r"run-[A-Za-z0-9._-]{6,128}", run_id) is None:
        raise WorkSessionError("invalid native run_id")
    run_dir = (root / ".continual" / "runs" / run_id).resolve()
    expected_run_dir = root / ".continual" / "runs" / run_id
    if run_dir != expected_run_dir.resolve() or not run_dir.is_dir():
        raise WorkSessionError(f"native run does not exist: {run_id}")

    completed = 0
    fragments = 0
    local_learn = 0
    for journal_path in sorted((run_dir / "invocations").glob("*.json")):
        journal = Store(root).read_json(journal_path, None)
        if not isinstance(journal, Mapping):
            raise WorkSessionError(
                f"malformed native invocation journal: {journal_path.name}"
            )
        if journal.get("status") != "complete":
            continue
        completed += 1
        invocation_id = journal.get("invocation_id")
        component = journal.get("component")
        output = journal.get("output")
        if (
            not isinstance(invocation_id, str)
            or _INVOCATION_ID.fullmatch(invocation_id) is None
            or journal_path.stem != invocation_id
            or not isinstance(component, str)
            or not component
            or not isinstance(output, Mapping)
        ):
            raise WorkSessionError(
                f"malformed completed native invocation: {journal_path.name}"
            )

        expected_fragment_ref = (
            f".continual/runs/{run_id}/fragments/"
            f"{invocation_id}-{component}.json"
        )
        if journal.get("fragment_ref") != expected_fragment_ref:
            raise WorkSessionError(
                f"completed native fragment_ref mismatch: {invocation_id}"
            )
        fragment_path = root / expected_fragment_ref
        fragment = Store(root).read_json(fragment_path, None)
        if not isinstance(fragment, Mapping):
            raise WorkSessionError(
                f"completed native fragment is missing or malformed: {invocation_id}"
            )
        expected_fragment = output.get("fragment") or {
            "component": component,
            "missing": True,
        }
        if not isinstance(expected_fragment, Mapping):
            raise WorkSessionError(
                f"completed native output fragment is malformed: {invocation_id}"
            )
        if any(fragment.get(key) != value for key, value in expected_fragment.items()):
            raise WorkSessionError(
                f"completed native fragment differs from frozen output: {invocation_id}"
            )
        if (
            fragment.get("component") != component
            or fragment.get("invocation_id") != invocation_id
            or not isinstance(fragment.get("environment"), Mapping)
        ):
            raise WorkSessionError(
                f"completed native fragment binding is malformed: {invocation_id}"
            )
        fragments += 1

        if component != "learn" and "local_learn" in output:
            learn_path = (
                run_dir
                / "local-learn"
                / f"{invocation_id}-{component}.json"
            )
            learned = Store(root).read_json(learn_path, None)
            if learned != output["local_learn"]:
                raise WorkSessionError(
                    "completed native Local Learn artifact is missing or differs "
                    f"from frozen output: {invocation_id}"
                )
            local_learn += 1

    return {
        "native_completed": completed,
        "native_fragments": fragments,
        "native_local_learn": local_learn,
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
