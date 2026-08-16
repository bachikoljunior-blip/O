from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Mapping

from .contracts import validate_component_output
from .engine import Engine
from .learned_tools import LearnedToolError, LearnedToolRegistry
from .parameterized_registry import ParameterizedToolRegistry


class LearningEnabledEngine(Engine):
    """Continual Engine that can execute exact-scope, regression-verified learned tools.

    Root receives only verified descriptors plus a machine-readable scheduling protocol. Execute may
    request a ``learned_tool_call`` for either a fixed compositional learned tool or an acquired safe
    parameterized tool. The engine re-verifies exact scope on every call, dispatches mechanically
    without model-generated code or shell execution, and journals the pure result for retry reuse.
    """

    def _learned_tool_registry(self) -> LearnedToolRegistry:
        return LearnedToolRegistry(self.root)

    def _parameterized_tool_registry(self) -> ParameterizedToolRegistry:
        return ParameterizedToolRegistry(self.root)

    @staticmethod
    def _tool_protocol() -> dict[str, Any]:
        return {
            "call_field": "learned_tool_call",
            "required_execution_unit": {
                "component": "execute",
                "scope": "must exactly equal the selected descriptor scope",
                "learned_tool_call": {
                    "tool_id": "must equal a supplied verified descriptor tool_id",
                    "input": "task value to transform",
                },
            },
            "rules": [
                "Use only descriptors supplied in verified_learned_tools.",
                "Never invent a tool_id or widen a descriptor scope.",
                "The engine re-verifies Candidate regression state before every call.",
            ],
        }

    def _candidate_scopes(self) -> tuple[str, ...]:
        candidates_dir = self.root / ".continual" / "candidates"
        if not candidates_dir.exists():
            return ()
        scopes: set[str] = set()
        for path in sorted(candidates_dir.glob("*/candidate.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping) or raw.get("target_component") not in {
                "learned_tool",
                "parameterized_tool",
            }:
                continue
            scope = raw.get("expected_scope")
            if isinstance(scope, str) and scope.strip():
                scopes.add(scope)
        return tuple(sorted(scopes))

    def _descriptors_for_scope(self, scope: str) -> tuple[dict[str, Any], ...]:
        combined: dict[str, dict[str, Any]] = {}
        for descriptor in self._learned_tool_registry().descriptors(scope):
            normalized = {"kind": "composition", **descriptor}
            tool_id = str(normalized["tool_id"])
            if tool_id in combined:
                raise LearnedToolError(f"duplicate verified tool_id across registries: {tool_id}")
            combined[tool_id] = normalized
        for descriptor in self._parameterized_tool_registry().descriptors(scope):
            tool_id = str(descriptor["tool_id"])
            if tool_id in combined:
                raise LearnedToolError(f"duplicate verified tool_id across registries: {tool_id}")
            combined[tool_id] = dict(descriptor)
        return tuple(combined[key] for key in sorted(combined))

    def _verified_tool_catalog(self) -> tuple[dict[str, Any], ...]:
        catalog: dict[tuple[str, str], dict[str, Any]] = {}
        for scope in self._candidate_scopes():
            for descriptor in self._descriptors_for_scope(scope):
                key = (scope, str(descriptor["tool_id"]))
                if key in catalog:
                    raise LearnedToolError(f"duplicate verified tool descriptor: {scope}:{key[1]}")
                catalog[key] = descriptor
        return tuple(catalog[key] for key in sorted(catalog))

    def _apply_verified_tool(self, *, scope: str, descriptor: Mapping[str, Any], value: Any) -> Any:
        tool_id = str(descriptor["tool_id"])
        kind = descriptor.get("kind")
        if kind == "composition":
            return self._learned_tool_registry().apply(scope=scope, tool_id=tool_id, value=value)
        if kind == "parameterized":
            return self._parameterized_tool_registry().apply(scope=scope, tool_id=tool_id, value=value)
        raise LearnedToolError(f"unsupported verified tool kind: {kind}")

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

        descriptors = {str(item["tool_id"]): item for item in self._descriptors_for_scope(scope)}
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
                    "tool_kind": descriptor["kind"],
                },
            )
            return output

        answer = self._apply_verified_tool(scope=scope, descriptor=descriptor, value=call["input"])
        output = {
            "result": {
                "status": "implemented",
                "execution_kind": "verified_learned_tool",
                "tool_kind": descriptor["kind"],
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
                    "The tool was re-verified against persisted Candidate scope state before execution.",
                    "No language-model-generated code or shell command was executed for this tool call.",
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
                "tool_kind": descriptor["kind"],
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
                "tool_kind": descriptor["kind"],
                "candidate_id": descriptor["candidate_id"],
            },
        )
        return output

    def _invoke(self, run_id: str, component: str, payload: dict[str, Any]) -> dict[str, Any]:
        enriched = deepcopy(payload)
        if component == "root":
            enriched["verified_learned_tools"] = list(self._verified_tool_catalog())
            enriched["verified_learned_tool_protocol"] = self._tool_protocol()
        if component == "execute":
            unit = enriched.get("execution_unit")
            scope = unit.get("scope") if isinstance(unit, Mapping) else None
            if isinstance(scope, str) and scope.strip():
                enriched["verified_learned_tools"] = list(self._descriptors_for_scope(scope))
                enriched["verified_learned_tool_protocol"] = self._tool_protocol()
            call = unit.get("learned_tool_call") if isinstance(unit, Mapping) else None
            if call is not None:
                if not isinstance(call, Mapping):
                    raise LearnedToolError("learned_tool_call must be an object")
                return self._mechanical_learned_tool_call(run_id, enriched, call)
        return super()._invoke(run_id, component, enriched)
