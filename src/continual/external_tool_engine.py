from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .contracted_external_tools import (
    ContractedExternalToolError,
    ContractedExternalToolRegistry,
    ExternalToolAdapter,
)
from .contracts import validate_component_output
from .learning_engine import LearningEnabledEngine


class ContractedExternalToolEngine(LearningEnabledEngine):
    """Continual Engine extension for regression-verified host-bound external tools.

    External adapter source is never loaded from Candidate state. The host must supply an adapter
    object whose identity matches the regression-verified contract. Execution is journaled exactly
    like other learned capabilities, and the contract call budget is keyed by durable run ID so
    retries of a completed invocation are idempotent while distinct calls in one run remain bounded.
    """

    def __init__(self, root: Path, adapters: Mapping[str, ExternalToolAdapter]) -> None:
        super().__init__(root)
        self._external_registry = ContractedExternalToolRegistry(self.root, adapters)

    def _verified_tool_catalog(self) -> tuple[dict[str, Any], ...]:
        catalog: dict[tuple[str, str], dict[str, Any]] = {
            (str(item["scope"]), str(item["tool_id"])): item
            for item in super()._verified_tool_catalog()
        }
        for contract in self._external_registry.catalog():
            descriptor = contract.descriptor()
            key = (contract.scope, contract.tool_id)
            if key in catalog:
                raise ContractedExternalToolError(
                    f"duplicate verified tool identity {contract.scope}:{contract.tool_id}"
                )
            catalog[key] = descriptor
        return tuple(catalog[key] for key in sorted(catalog))

    def _scope_tool_descriptors(self, scope: str) -> dict[str, dict[str, Any]]:
        selected = dict(super()._scope_tool_descriptors(scope))
        for descriptor in self._external_registry.descriptors(scope):
            tool_id = str(descriptor["tool_id"])
            if tool_id in selected:
                raise ContractedExternalToolError(
                    f"duplicate verified tool identity {scope}:{tool_id}"
                )
            selected[tool_id] = descriptor
        return selected

    def _mechanical_external_tool_call(
        self,
        run_id: str,
        payload: dict[str, Any],
        call: Mapping[str, Any],
        descriptor: Mapping[str, Any],
    ) -> dict[str, Any]:
        unit = payload.get("execution_unit")
        if not isinstance(unit, Mapping):
            raise ContractedExternalToolError("external tool execution requires an execution_unit")
        scope = unit.get("scope")
        if not isinstance(scope, str) or not scope.strip():
            raise ContractedExternalToolError("external tool execution requires an exact scope")
        requested_scope = call.get("scope", scope)
        if requested_scope != scope:
            raise ContractedExternalToolError("external tool call scope must exactly match execution unit")
        tool_id = call.get("tool_id")
        if not isinstance(tool_id, str) or not tool_id:
            raise ContractedExternalToolError("external tool call requires tool_id")
        if "input" not in call:
            raise ContractedExternalToolError("external tool call requires input")

        invocation_payload = {
            "scope": scope,
            "tool_id": tool_id,
            "input": call["input"],
            "descriptor": dict(descriptor),
        }
        invocation_id = f"invoke-external-tool-{self.store.stable_digest(invocation_payload)}"
        path = self._invocation_path(run_id, invocation_id)
        journal = self.store.read_json(path, {})
        if isinstance(journal, dict) and journal.get("status") == "complete":
            output = journal.get("output")
            validate_component_output("execute", output)
            self.store.append_event(
                run_id,
                {
                    "type": "external_tool_invocation_reused",
                    "invocation_id": invocation_id,
                    "tool_id": tool_id,
                    "scope": scope,
                    "program_kind": "contracted_external_tool",
                },
            )
            return output

        answer = self._external_registry.apply(
            scope=scope,
            tool_id=tool_id,
            value=call["input"],
            budget_key=run_id,
        )
        output = {
            "result": {
                "status": "implemented",
                "execution_kind": "verified_external_tool",
                "program_kind": "contracted_external_tool",
                "tool_id": tool_id,
                "scope": scope,
                "output": answer,
                "candidate_id": descriptor["candidate_id"],
                "adapter_sha256": descriptor["adapter_sha256"],
            },
            "local_learn": {"decision": "NO_CHANGE", "candidates": []},
            "fragment": {
                "component": "execute",
                "purpose": "mechanical execution of an exact-scope regression-verified host-bound external tool",
                "observations": [
                    "The Candidate contract and adapter identity were re-verified before execution.",
                    "Only effect-free host adapters are executable; Candidate state cannot supply adapter source, shell, network, filesystem, subprocess, or generated code.",
                    "Input/output JSON byte limits and a per-run distinct-call budget are enforced fail-closed.",
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
                "execution_kind": "verified_external_tool",
                "program_kind": "contracted_external_tool",
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
                "type": "external_tool_invocation_completed",
                "invocation_id": invocation_id,
                "tool_id": tool_id,
                "scope": scope,
                "candidate_id": descriptor["candidate_id"],
                "adapter_sha256": descriptor["adapter_sha256"],
            },
        )
        return output

    def _invoke(self, run_id: str, component: str, payload: dict[str, Any]) -> dict[str, Any]:
        if component == "execute":
            unit = payload.get("execution_unit")
            scope = unit.get("scope") if isinstance(unit, Mapping) else None
            call = unit.get("learned_tool_call") if isinstance(unit, Mapping) else None
            if isinstance(scope, str) and scope.strip() and isinstance(call, Mapping):
                tool_id = call.get("tool_id")
                if isinstance(tool_id, str) and tool_id:
                    external = {
                        item["tool_id"]: item
                        for item in self._external_registry.descriptors(scope)
                    }
                    descriptor = external.get(tool_id)
                    if descriptor is not None:
                        return self._mechanical_external_tool_call(
                            run_id,
                            payload,
                            call,
                            descriptor,
                        )
        return super()._invoke(run_id, component, payload)
