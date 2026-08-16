from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .acquired_program_tools import AcquiredProgramRegistry, AcquiredProgramToolError
from .candidate_regression import candidate_verified_for_scope
from .contracted_external_tools import (
    ContractedExternalToolError,
    ContractedExternalToolRegistry,
    ExternalToolAdapter,
)
from .contracts import validate_component_output
from .engine import Engine
from .learned_tools import LearnedToolError, LearnedToolRegistry


class LearningEnabledEngine(Engine):
    """Continual Engine with verified learned tools and fail-closed Candidate activation.

    A semantic Candidate evaluator may select an unverified Candidate for one explicit trial, but it
    cannot promote that Candidate. Continued USE_CANDIDATE/ACTIVE_FOR_SCOPE selection requires a
    matching deterministic regression record bound to the current Candidate body and prompt artifact.
    Verified learned-tool, acquired-program, and host-bound contracted external-tool calls are executed
    mechanically and journaled for idempotent retry.
    """

    def __init__(
        self,
        root: Path,
        *,
        external_tool_adapters: Mapping[str, ExternalToolAdapter] | None = None,
    ) -> None:
        super().__init__(root)
        self._external_tool_registry = ContractedExternalToolRegistry(
            self.root,
            external_tool_adapters or {},
        )

    def _learned_tool_registry(self) -> LearnedToolRegistry:
        return LearnedToolRegistry(self.root)

    def _acquired_program_registry(self) -> AcquiredProgramRegistry:
        return AcquiredProgramRegistry(self.root)

    def _contracted_external_tool_registry(self) -> ContractedExternalToolRegistry:
        return self._external_tool_registry

    def _verified_tool_catalog(self) -> tuple[dict[str, Any], ...]:
        learned = self._learned_tool_registry()
        acquired = self._acquired_program_registry()
        external = self._contracted_external_tool_registry()
        candidates_dir = self.root / ".continual" / "candidates"
        if not candidates_dir.exists():
            return ()
        learned_scopes: set[str] = set()
        acquired_scopes: set[str] = set()
        external_scopes: set[str] = set()
        for path in sorted(candidates_dir.glob("*/candidate.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping):
                continue
            scope = raw.get("expected_scope")
            if not isinstance(scope, str) or not scope.strip():
                continue
            if raw.get("target_component") == "learned_tool":
                learned_scopes.add(scope)
            elif raw.get("target_component") == "acquired_program":
                acquired_scopes.add(scope)
            elif raw.get("target_component") == "contracted_external_tool":
                external_scopes.add(scope)
        catalog: dict[tuple[str, str], dict[str, Any]] = {}
        for scope in sorted(learned_scopes):
            for descriptor in learned.descriptors(scope):
                key = (scope, str(descriptor["tool_id"]))
                if key in catalog:
                    raise LearnedToolError(f"duplicate verified tool identity {scope}:{key[1]}")
                catalog[key] = descriptor
        for scope in sorted(acquired_scopes):
            for descriptor in acquired.descriptors(scope):
                key = (scope, str(descriptor["tool_id"]))
                if key in catalog:
                    raise AcquiredProgramToolError(
                        f"duplicate verified tool identity {scope}:{key[1]}"
                    )
                catalog[key] = descriptor
        for scope in sorted(external_scopes):
            for descriptor in external.descriptors(scope):
                key = (scope, str(descriptor["tool_id"]))
                if key in catalog:
                    raise ContractedExternalToolError(
                        f"duplicate verified tool identity {scope}:{key[1]}"
                    )
                catalog[key] = descriptor
        return tuple(catalog[key] for key in sorted(catalog))

    def _scope_tool_descriptors(self, scope: str) -> dict[str, dict[str, Any]]:
        selected: dict[str, dict[str, Any]] = {}
        for descriptor in self._learned_tool_registry().descriptors(scope):
            selected[str(descriptor["tool_id"])] = descriptor
        for descriptor in self._acquired_program_registry().descriptors(scope):
            tool_id = str(descriptor["tool_id"])
            if tool_id in selected:
                raise AcquiredProgramToolError(
                    f"duplicate verified tool identity {scope}:{tool_id}"
                )
            selected[tool_id] = descriptor
        for descriptor in self._contracted_external_tool_registry().descriptors(scope):
            tool_id = str(descriptor["tool_id"])
            if tool_id in selected:
                raise ContractedExternalToolError(
                    f"duplicate verified tool identity {scope}:{tool_id}"
                )
            selected[tool_id] = descriptor
        return selected

    def _selected_candidate(self, selection: dict[str, Any], target: str) -> dict[str, Any] | None:
        candidate = super()._selected_candidate(selection, target)
        if candidate is None:
            return None
        result = selection.get("result", selection)
        if not isinstance(result, Mapping):
            return None
        decision = result.get("decision")
        if decision == "TRIAL_CANDIDATE":
            return candidate
        scope = result.get("scope")
        if not isinstance(scope, str) or not scope.strip():
            return None
        if not candidate_verified_for_scope(self.root, candidate, scope):
            return None
        return candidate

    def _apply_scope_update(self, post: dict[str, Any]) -> None:
        """Persist semantic observations without allowing semantic self-promotion.

        ACTIVE_FOR_SCOPE and VERIFIED_FOR_SCOPE from a model are recommendations, not state-machine
        authority. They are retained as supporting evidence, while only record_candidate_regression
        may write VERIFIED_FOR_SCOPE. Conservative negative outcomes still deactivate the exact scope.
        """

        result = post.get("result", post)
        if not isinstance(result, Mapping):
            return
        state = result.get("scope_state") or result.get("decision")
        if state not in {"ACTIVE_FOR_SCOPE", "VERIFIED_FOR_SCOPE", "USE_CANDIDATE"}:
            super()._apply_scope_update(post)
            return

        candidate_id = result.get("candidate_id")
        scope = result.get("scope")
        if not all(isinstance(value, str) and value for value in (candidate_id, scope)):
            return
        candidate = self._candidate_by_id(candidate_id)
        if not candidate:
            return
        supporting = list(candidate.get("supporting_evidence", []))
        supporting.append(
            {
                "type": "semantic_candidate_recommendation",
                "scope": scope,
                "recommended_state": state,
                "evidence": result.get("evidence", []),
                "at": self.store.utc_now(),
                "note": "Recommendation only; deterministic regression is required for activation.",
            }
        )
        candidate["supporting_evidence"] = self._dedupe_list(supporting)
        self.store.atomic_json(self._candidate_path(candidate_id), candidate)
        index = self._index()
        self._sync_index_candidate(index, candidate)
        self._write_index(index)

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

        descriptors = self._scope_tool_descriptors(scope)
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
                    "program_kind": descriptor.get("program_kind"),
                },
            )
            return output

        program_kind = descriptor.get("program_kind")
        if program_kind == "acquired_program":
            answer = self._acquired_program_registry().apply(
                scope=scope,
                tool_id=tool_id,
                value=call["input"],
            )
        elif program_kind == "contracted_external_tool":
            answer = self._contracted_external_tool_registry().apply(
                scope=scope,
                tool_id=tool_id,
                value=call["input"],
            )
        else:
            answer = self._learned_tool_registry().apply(
                scope=scope,
                tool_id=tool_id,
                value=call["input"],
            )
        support_sha256 = descriptor.get("support_sha256") or descriptor.get("adapter_sha256")
        output = {
            "result": {
                "status": "implemented",
                "execution_kind": "verified_learned_tool",
                "program_kind": program_kind,
                "tool_id": tool_id,
                "scope": scope,
                "output": answer,
                "candidate_id": descriptor["candidate_id"],
                "support_sha256": support_sha256,
            },
            "local_learn": {"decision": "NO_CHANGE", "candidates": []},
            "fragment": {
                "component": "execute",
                "purpose": "mechanical execution of an exact-scope regression-verified learned capability",
                "observations": [
                    "The learned capability and its deterministic regression record were re-verified before execution.",
                    "Acquired programs remain in the pure bounded interpreter; contracted external tools require a host-bound adapter identity and enforce zero effects plus typed call/byte resource limits.",
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
                "program_kind": program_kind,
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
                "program_kind": program_kind,
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
                    self._scope_tool_descriptors(scope).values()
                )
            call = unit.get("learned_tool_call") if isinstance(unit, Mapping) else None
            if call is not None:
                if not isinstance(call, Mapping):
                    raise LearnedToolError("learned_tool_call must be an object")
                return self._mechanical_learned_tool_call(run_id, enriched, call)
        return super()._invoke(run_id, component, enriched)
