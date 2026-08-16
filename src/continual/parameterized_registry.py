from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agi.parameterized_runtime import apply_parameterized_tool, validate_parameterized_tool

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ParameterizedRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class ParameterizedToolSpec:
    tool_id: str
    candidate_id: str
    scope: str
    domain: str
    family: str
    parameters: dict[str, Any]
    support_sha256: str
    description: str

    @classmethod
    def from_candidate(cls, candidate: Mapping[str, Any]) -> "ParameterizedToolSpec":
        raw = candidate.get("parameterized_tool")
        if candidate.get("target_component") != "parameterized_tool" or not isinstance(raw, Mapping):
            raise ParameterizedRegistryError("candidate is not a parameterized-tool candidate")
        spec = cls(
            tool_id=str(raw.get("tool_id", "")),
            candidate_id=str(candidate.get("candidate_id", "")),
            scope=str(raw.get("scope", candidate.get("expected_scope", ""))),
            domain=str(raw.get("domain", "")),
            family=str(raw.get("family", "")),
            parameters=dict(raw.get("parameters", {})),
            support_sha256=str(raw.get("support_sha256", "")),
            description=str(raw.get("description", "")),
        )
        spec.validate()
        expected_scope = candidate.get("expected_scope")
        if isinstance(expected_scope, str) and expected_scope and spec.scope != expected_scope:
            raise ParameterizedRegistryError("parameterized tool scope does not match candidate expected_scope")
        return spec

    def validate(self) -> None:
        if not _SAFE_ID.fullmatch(self.tool_id):
            raise ParameterizedRegistryError("invalid parameterized tool_id")
        if not _SAFE_ID.fullmatch(self.candidate_id):
            raise ParameterizedRegistryError("invalid parameterized candidate_id")
        if not self.scope.strip() or not self.domain.strip() or not self.family.strip():
            raise ParameterizedRegistryError("parameterized scope/domain/family must be non-empty")
        if not _SHA256.fullmatch(self.support_sha256):
            raise ParameterizedRegistryError("parameterized support_sha256 must be lowercase SHA-256")
        if not self.description.strip():
            raise ParameterizedRegistryError("parameterized description must be non-empty")
        try:
            validate_parameterized_tool(self.domain, self.family, self.parameters)
        except Exception as exc:
            raise ParameterizedRegistryError("invalid parameterized tool family or parameters") from exc

    def descriptor(self) -> dict[str, Any]:
        return {
            "kind": "parameterized",
            "tool_id": self.tool_id,
            "candidate_id": self.candidate_id,
            "scope": self.scope,
            "domain": self.domain,
            "family": self.family,
            "description": self.description,
            "support_sha256": self.support_sha256,
            "parameters_sha256": _stable_digest(self.parameters),
        }


def _stable_digest(value: Any) -> str:
    import hashlib

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ParameterizedToolRegistry:
    """Expose parameterized tools only after exact-scope deterministic Candidate regression."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    @property
    def candidates_dir(self) -> Path:
        return self.root / ".continual" / "candidates"

    @staticmethod
    def _read_json(path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _verified(candidate: Mapping[str, Any], scope: str) -> bool:
        scope_states = candidate.get("scope_states")
        verified_states = candidate.get("verified_scope_states")
        if not isinstance(scope_states, Mapping) or not isinstance(verified_states, Mapping):
            return False
        record = verified_states.get(scope)
        return (
            candidate.get("status") == "active-for-scope"
            and scope_states.get(scope) == "VERIFIED_FOR_SCOPE"
            and isinstance(record, Mapping)
            and record.get("state") == "VERIFIED_FOR_SCOPE"
            and isinstance(record.get("regression_evidence_sha256"), str)
            and bool(_SHA256.fullmatch(str(record.get("regression_evidence_sha256"))))
        )

    def available(self, scope: str) -> tuple[ParameterizedToolSpec, ...]:
        if not isinstance(scope, str) or not scope.strip():
            raise ParameterizedRegistryError("scope must be a non-empty string")
        selected: dict[str, ParameterizedToolSpec] = {}
        if not self.candidates_dir.exists():
            return ()
        for path in sorted(self.candidates_dir.glob("*/candidate.json")):
            raw = self._read_json(path)
            if not isinstance(raw, Mapping) or raw.get("target_component") != "parameterized_tool":
                continue
            spec = ParameterizedToolSpec.from_candidate(raw)
            if spec.scope != scope or not self._verified(raw, scope):
                continue
            if spec.tool_id in selected:
                raise ParameterizedRegistryError(
                    f"multiple verified parameterized candidates provide tool_id {spec.tool_id}"
                )
            selected[spec.tool_id] = spec
        return tuple(selected[key] for key in sorted(selected))

    def descriptors(self, scope: str) -> tuple[dict[str, Any], ...]:
        return tuple(spec.descriptor() for spec in self.available(scope))

    def apply(self, *, scope: str, tool_id: str, value: Any) -> Any:
        selected = {spec.tool_id: spec for spec in self.available(scope)}
        spec = selected.get(tool_id)
        if spec is None:
            raise ParameterizedRegistryError(
                f"parameterized tool is not verified for scope: {scope}:{tool_id}"
            )
        return apply_parameterized_tool(spec.domain, spec.family, spec.parameters, value)


def parameterized_tool_candidate_payload(
    *,
    candidate_id: str,
    tool_id: str,
    scope: str,
    domain: str,
    family: str,
    parameters: Mapping[str, Any],
    support_sha256: str,
    description: str,
) -> dict[str, Any]:
    candidate = {
        "candidate_id": candidate_id,
        "target_component": "parameterized_tool",
        "expected_scope": scope,
        "status": "candidate",
        "scope_states": {},
        "verified_scope_states": {},
        "supporting_evidence": [],
        "contradictory_evidence": [],
        "regression_decision_refs": [],
        "parameterized_tool": {
            "tool_id": tool_id,
            "scope": scope,
            "domain": domain,
            "family": family,
            "parameters": dict(parameters),
            "support_sha256": support_sha256,
            "description": description,
        },
    }
    ParameterizedToolSpec.from_candidate(candidate)
    return candidate
