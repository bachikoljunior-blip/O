from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Mapping

from .contracts import validate_component_output
from .engine import Engine
from .learned_tools import LearnedToolError, LearnedToolRegistry


class LearningEnabledEngine(Engine):
    """Continual Engine that can execute exact-scope, regression-verified learned tools.

    Verified tool descriptors are supplied to Root and Execute as persistent capability metadata.
    Root may schedule an execute unit containing ``learned_tool_call``. The actual call bypasses the
    language model and is performed mechanically by LearnedToolRegistry, which re-checks exact scope
    and deterministic regression verification before every invocation. Calls are journaled using the
    Engine's normal invocation directory so retries reuse the same completed pure result.
    """

    def _learned_tool_registry(self) -> LearnedToolRegistry:
        return LearnedToolRegistry(self.root)

    def _verified_tool_catalog(self) -> tuple[dict[str, Any], ...]:
        registry = self._learned_tool_registry()
        candidates_dir = self.root / ".continual" / "candidates"
        if not candidates_dir.exists():
            return ()
        scopes: set[str] = set()
        for path in sorted(candidates_dir.glob("*/candidate.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping) or raw.get("target_component") != "learned_tool":
                continue
            scope = raw.get("expected_scope")
            if isinstance(scope, str) and scope.strip():
                scopes.add(scope)
        catalog: dict[tuple[str, str], dict[str, Any]] = {}
        for scope in sorted(scopes):
            for descriptor in registry.descriptors(scope):
                catalog[(scope, str(descriptor["tool_id"]))] = descriptor
        return tuple(catalog[key] for key in sorted(catalog))

    def _mechanical_learned_tool_call(
        self,
        run_id: str,
        payload: dict[str, Any],
        call: Mapping[str, Any],
    ) -> dict[str, Any]:
        unit = payload.get("execution_unit")
        if not isinstance(unit, Mapping):
            raise LearnedToolError("learned tool execution requires an execution_unit")
        scope = unit.get("scope")
        if not isinstance(scope, str) or not scope.strip():
            raise LearnedToolError("learned tool execution unit requires an exact non-empty scope")
        requested_scope = call.get("scope", scope)
        if requested_scope != scope:
            raise LearnedToolError("learned tool call scope must exactly match execution unit scope")
        tool_id = call.get("tool_id")
        if not isinstance(tool_id, str) or not tool_id:
            raise LearnedToolError("learned tool call requires tool_id")
        if "input" not in call:
            raise LearnedToolError("learned tool call requires input")

        registry = self._learned_tool_registry()
        descriptors = {
            str(item["tool_id"]): item
            for item in registry.descriptors(scope)
        }
        descriptor = descriptors.get(tool_id)
        if descriptor is None:
            raise LearnedToolError(f"learned tool is not verified for scope: {scope}:{tool_id}")

        invocation_payload = {
            "scope": scope,
            "tool_id": tool_id,
            "input": call["input"],
            "descriptor": descriptor,
        }
        invocation_id = f"invoke-learned-tool-{self.store.stable_digest(invocation_payload)}"
        path = self._invocation_path(run_id, invocation_id)
        journal = self.store.read_json(path, {})
        if isinstance(journal, dict) and journal.get("status") == "complete":
            output = journal.get("output")
            validate_component_output("execute", output)
            self.store.append_event(
                run_id,
                {
                    "type": "learned_tool_invocation_reused",
                    "invocation_id": invocation_id,
                    "tool_id": tool_id,
                    "scope": scope,
                },
            )
            return output

        answer = registry.apply(scope=scope, tool_id=tool_id, value=call["input"])
        output = {
            "result": {
                "status": "implemented",
                "execution_kind": "verified_learned_tool",
                "tool_id": tool_id,
                "scope": scope,
                "output": answer,
                "candidate_id": descriptor["candidate_id"],
                "support_sha256": descriptor["support_sha256"],
            },
            "local_learn": {"decision": "NO_CHANGE", "candidates": []},
            "fragment": {
                "component": "execute",
                "purpose": "mechanical execution of an exact-scope regression-verified learned tool",
                "observations": [
                    "The learned tool was re-verified against persisted Candidate scope state before execution.",
                    "No language-model-generated code or shell command was executed for this learned tool call.",
                ],
                "evidence_refs": [
                    f".continual/candidates/{descriptor['candidate_id']}/candidate.json"
                ],
                "unresolved": [],
            },
        }
        validate_component_output("execute", output)
        fragment_ref = self._save_component_output(run_id, "execute", output, invocation_id)
        self.store.atomic_json(
            path,
            {
                "invocation_id": invocation_id,
                "component": "execute",
                "execution_kind": "verified_learned_tool",
                "payload_digest": self.store.stable_digest(invocation_payload),
                "status": "complete",
                "attempt": 1,
                "output": output,
                "fragment_ref": fragment_ref,
                "completed_at": self.store.utc_now(),
            },
        )
        self.store.append_event(
            run_id,
            {
                "type": "learned_tool_invocation_completed",
                "invocation_id": invocation_id,
                "tool_id": tool_id,
                "scope": scope,
                "candidate_id": descriptor["candidate_id"],
            },
        )
        return output

    def _invoke(self, run_id: str, component: str, payload: dict[str, Any]) -> dict[str, Any]:
        enriched = deepcopy(payload)
        if component == "root":
            enriched["verified_learned_tools"] = list(self._verified_tool_catalog())
        if component == "execute":
            unit = enriched.get("execution_unit")
            scope = unit.get("scope") if isinstance(unit, Mapping) else None
            if isinstance(scope, str) and scope.strip():
                enriched["verified_learned_tools"] = list(
                    self._learned_tool_registry().descriptors(scope)
                )
            call = unit.get("learned_tool_call") if isinstance(unit, Mapping) else None
            if call is not None:
                if not isinstance(call, Mapping):
                    raise LearnedToolError("learned_tool_call must be an object")
                return self._mechanical_learned_tool_call(run_id, enriched, call)
        return super()._invoke(run_id, component, enriched)
