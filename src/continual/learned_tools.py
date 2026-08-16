from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agi.compositional import Primitive, default_primitives

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class LearnedToolError(ValueError):
    pass


@dataclass(frozen=True)
class LearnedToolSpec:
    tool_id: str
    candidate_id: str
    scope: str
    domain: str
    expanded_primitives: tuple[str, ...]
    support_sha256: str
    description: str

    @classmethod
    def from_candidate(cls, candidate: Mapping[str, Any]) -> "LearnedToolSpec":
        candidate_id = str(candidate.get("candidate_id", ""))
        expected_scope = str(candidate.get("expected_scope", ""))
        raw = candidate.get("learned_tool")
        if candidate.get("target_component") != "learned_tool" or not isinstance(raw, Mapping):
            raise LearnedToolError("candidate is not a learned-tool candidate")
        value = cls(
            tool_id=str(raw.get("tool_id", "")),
            candidate_id=candidate_id,
            scope=str(raw.get("scope", expected_scope)),
            domain=str(raw.get("domain", "")),
            expanded_primitives=tuple(str(item) for item in raw.get("expanded_primitives", [])),
            support_sha256=str(raw.get("support_sha256", "")),
            description=str(raw.get("description", "")),
        )
        value.validate()
        if expected_scope and value.scope != expected_scope:
            raise LearnedToolError("learned tool scope does not match candidate expected_scope")
        return value

    def validate(self) -> None:
        if not _SAFE_ID.fullmatch(self.tool_id):
            raise LearnedToolError("invalid learned tool_id")
        if not _SAFE_ID.fullmatch(self.candidate_id):
            raise LearnedToolError("invalid learned tool candidate_id")
        if not self.scope.strip():
            raise LearnedToolError("learned tool scope must be non-empty")
        if not self.domain.strip():
            raise LearnedToolError("learned tool domain must be non-empty")
        if not self.expanded_primitives:
            raise LearnedToolError("learned tool must contain at least one primitive")
        if not _SHA256.fullmatch(self.support_sha256):
            raise LearnedToolError("learned tool support_sha256 must be lowercase SHA-256")
        if not self.description.strip():
            raise LearnedToolError("learned tool description must be non-empty")

    def descriptor(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "candidate_id": self.candidate_id,
            "scope": self.scope,
            "domain": self.domain,
            "description": self.description,
            "program_length": len(self.expanded_primitives),
            "support_sha256": self.support_sha256,
        }


class LearnedToolRegistry:
    """Expose only deterministically verified learned tools for one exact scope.

    The registry does not have an independent activation bit. Its source of truth is the Candidate
    regression record persisted in candidate.json: a tool is callable only when the exact scope is
    VERIFIED_FOR_SCOPE in both scope state views. Tool bodies are declarative compositions over the
    repository's fixed safe primitive set; arbitrary source code or shell commands are never loaded.
    """

    def __init__(self, root: Path, primitives: tuple[Primitive, ...] | None = None) -> None:
        self.root = root.resolve()
        values = tuple(primitives or default_primitives())
        self._primitives: dict[tuple[str, str], Primitive] = {}
        for primitive in values:
            key = (primitive.domain, primitive.name)
            if key in self._primitives:
                raise LearnedToolError(f"duplicate primitive {primitive.domain}:{primitive.name}")
            self._primitives[key] = primitive

    @property
    def candidates_dir(self) -> Path:
        return self.root / ".continual" / "candidates"

    @staticmethod
    def _read_json(path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def _candidate_values(self) -> tuple[dict[str, Any], ...]:
        if not self.candidates_dir.exists():
            return ()
        values: list[dict[str, Any]] = []
        for path in sorted(self.candidates_dir.glob("*/candidate.json")):
            raw = self._read_json(path)
            if not isinstance(raw, dict):
                raise LearnedToolError(f"candidate must be an object: {path}")
            if raw.get("target_component") == "learned_tool":
                values.append(raw)
        return tuple(values)

    @staticmethod
    def _verified_for_scope(candidate: Mapping[str, Any], scope: str) -> bool:
        scope_states = candidate.get("scope_states")
        verified = candidate.get("verified_scope_states")
        if not isinstance(scope_states, Mapping) or not isinstance(verified, Mapping):
            return False
        record = verified.get(scope)
        return (
            candidate.get("status") == "active-for-scope"
            and scope_states.get(scope) == "VERIFIED_FOR_SCOPE"
            and isinstance(record, Mapping)
            and record.get("state") == "VERIFIED_FOR_SCOPE"
            and isinstance(record.get("regression_evidence_sha256"), str)
            and bool(_SHA256.fullmatch(str(record.get("regression_evidence_sha256"))))
        )

    def available(self, scope: str) -> tuple[LearnedToolSpec, ...]:
        if not isinstance(scope, str) or not scope.strip():
            raise LearnedToolError("scope must be a non-empty string")
        selected: dict[str, LearnedToolSpec] = {}
        for candidate in self._candidate_values():
            spec = LearnedToolSpec.from_candidate(candidate)
            for primitive in spec.expanded_primitives:
                if (spec.domain, primitive) not in self._primitives:
                    raise LearnedToolError(
                        f"learned tool references unknown primitive {spec.domain}:{primitive}"
                    )
            if spec.scope != scope or not self._verified_for_scope(candidate, scope):
                continue
            if spec.tool_id in selected:
                raise LearnedToolError(f"multiple verified candidates provide tool_id {spec.tool_id}")
            selected[spec.tool_id] = spec
        return tuple(selected[key] for key in sorted(selected))

    def descriptors(self, scope: str) -> tuple[dict[str, Any], ...]:
        return tuple(spec.descriptor() for spec in self.available(scope))

    def apply(self, *, scope: str, tool_id: str, value: Any) -> Any:
        selected = {spec.tool_id: spec for spec in self.available(scope)}
        spec = selected.get(tool_id)
        if spec is None:
            raise LearnedToolError(f"learned tool is not verified for scope: {scope}:{tool_id}")
        current = value
        for primitive_name in spec.expanded_primitives:
            primitive = self._primitives[(spec.domain, primitive_name)]
            try:
                current = primitive.function(current)
            except (TypeError, ValueError, OverflowError) as exc:
                raise LearnedToolError(
                    f"learned tool input failed {spec.domain}:{primitive_name}"
                ) from exc
        return current


def learned_tool_candidate_payload(
    *,
    candidate_id: str,
    tool_id: str,
    scope: str,
    domain: str,
    expanded_primitives: tuple[str, ...],
    support_sha256: str,
    description: str,
) -> dict[str, Any]:
    """Build a Candidate payload without granting activation.

    Activation remains exclusively owned by record_candidate_regression. This helper deliberately
    creates a plain candidate with empty scope state maps, so merely learning or registering a tool
    can never make it executable.
    """

    spec = LearnedToolSpec(
        tool_id=tool_id,
        candidate_id=candidate_id,
        scope=scope,
        domain=domain,
        expanded_primitives=expanded_primitives,
        support_sha256=support_sha256,
        description=description,
    )
    spec.validate()
    return {
        "candidate_id": candidate_id,
        "target_component": "learned_tool",
        "expected_scope": scope,
        "status": "candidate",
        "scope_states": {},
        "verified_scope_states": {},
        "supporting_evidence": [],
        "contradictory_evidence": [],
        "regression_decision_refs": [],
        "learned_tool": {
            "tool_id": spec.tool_id,
            "scope": spec.scope,
            "domain": spec.domain,
            "expanded_primitives": list(spec.expanded_primitives),
            "support_sha256": spec.support_sha256,
            "description": spec.description,
        },
    }
