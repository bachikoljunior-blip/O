from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agi.compositional import Primitive, default_primitives
from agi.typed_composition import TypedPrimitive, default_typed_primitives

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROGRAM_KINDS = {"legacy_same_domain", "typed"}


class LearnedToolError(ValueError):
    pass


def _matches_domain(domain: str, value: Any) -> bool:
    if domain == "string":
        return isinstance(value, str)
    if domain == "numeric":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if domain == "sequence":
        return isinstance(value, (list, tuple))
    return False


@dataclass(frozen=True)
class LearnedToolSpec:
    tool_id: str
    candidate_id: str
    scope: str
    domain: str
    output_domain: str
    program_kind: str
    expanded_primitives: tuple[str, ...]
    support_sha256: str
    description: str

    @property
    def input_domain(self) -> str:
        return self.domain

    @classmethod
    def from_candidate(cls, candidate: Mapping[str, Any]) -> "LearnedToolSpec":
        candidate_id = str(candidate.get("candidate_id", ""))
        expected_scope = str(candidate.get("expected_scope", ""))
        raw = candidate.get("learned_tool")
        if candidate.get("target_component") != "learned_tool" or not isinstance(raw, Mapping):
            raise LearnedToolError("candidate is not a learned-tool candidate")
        domain = str(raw.get("input_domain", raw.get("domain", "")))
        output_domain = str(raw.get("output_domain", raw.get("domain", domain)))
        raw_kind = raw.get("program_kind")
        if raw_kind is None:
            program_kind = (
                "typed"
                if "input_domain" in raw or "output_domain" in raw
                else "legacy_same_domain"
            )
        else:
            program_kind = str(raw_kind)
        value = cls(
            tool_id=str(raw.get("tool_id", "")),
            candidate_id=candidate_id,
            scope=str(raw.get("scope", expected_scope)),
            domain=domain,
            output_domain=output_domain,
            program_kind=program_kind,
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
        if not self.domain.strip() or not self.output_domain.strip():
            raise LearnedToolError("learned tool input/output domains must be non-empty")
        if self.program_kind not in _PROGRAM_KINDS:
            raise LearnedToolError("invalid learned tool program_kind")
        if self.program_kind == "legacy_same_domain" and self.output_domain != self.domain:
            raise LearnedToolError("legacy learned tool cannot change domains")
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
            "input_domain": self.input_domain,
            "output_domain": self.output_domain,
            "program_kind": self.program_kind,
            "description": self.description,
            "program_length": len(self.expanded_primitives),
            "support_sha256": self.support_sha256,
        }


class LearnedToolRegistry:
    """Expose only deterministically verified declarative learned tools for one exact scope.

    The registry has no independent activation bit. Its source of truth is the Candidate regression
    record persisted in candidate.json: a tool is callable only when the exact scope is
    VERIFIED_FOR_SCOPE in both scope state views. Legacy tools compose a fixed same-domain primitive
    set. Typed tools compose a separately fixed safe primitive set whose input/output domains are
    checked both statically and at every invocation. Arbitrary source code and shell commands are
    never loaded.
    """

    def __init__(
        self,
        root: Path,
        primitives: tuple[Primitive, ...] | None = None,
        typed_primitives: tuple[TypedPrimitive, ...] | None = None,
    ) -> None:
        self.root = root.resolve()
        legacy_values = tuple(primitives or default_primitives())
        self._primitives: dict[tuple[str, str], Primitive] = {}
        for primitive in legacy_values:
            key = (primitive.domain, primitive.name)
            if key in self._primitives:
                raise LearnedToolError(f"duplicate primitive {primitive.domain}:{primitive.name}")
            self._primitives[key] = primitive

        typed_values = tuple(typed_primitives or default_typed_primitives())
        self._typed_primitives: dict[str, TypedPrimitive] = {}
        for primitive in typed_values:
            if primitive.name in self._typed_primitives:
                raise LearnedToolError(f"duplicate typed primitive {primitive.name}")
            self._typed_primitives[primitive.name] = primitive

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

    def _validate_program(self, spec: LearnedToolSpec) -> None:
        if spec.program_kind == "legacy_same_domain":
            for primitive_name in spec.expanded_primitives:
                if (spec.domain, primitive_name) not in self._primitives:
                    raise LearnedToolError(
                        f"learned tool references unknown primitive {spec.domain}:{primitive_name}"
                    )
            return

        current_domain = spec.input_domain
        for primitive_name in spec.expanded_primitives:
            primitive = self._typed_primitives.get(primitive_name)
            if primitive is None:
                raise LearnedToolError(
                    f"learned tool references unknown typed primitive {primitive_name}"
                )
            if primitive.input_domain != current_domain:
                raise LearnedToolError(
                    "learned tool contains an ill-typed primitive chain: "
                    f"expected {current_domain}, got {primitive.input_domain} at {primitive_name}"
                )
            current_domain = primitive.output_domain
        if current_domain != spec.output_domain:
            raise LearnedToolError(
                "learned tool declared output_domain does not match typed primitive chain"
            )

    def available(self, scope: str) -> tuple[LearnedToolSpec, ...]:
        if not isinstance(scope, str) or not scope.strip():
            raise LearnedToolError("scope must be a non-empty string")
        selected: dict[str, LearnedToolSpec] = {}
        for candidate in self._candidate_values():
            spec = LearnedToolSpec.from_candidate(candidate)
            self._validate_program(spec)
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

        if spec.program_kind == "legacy_same_domain":
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

        if not _matches_domain(spec.input_domain, value):
            raise LearnedToolError(
                f"typed learned tool expected {spec.input_domain} input"
            )
        current_domain = spec.input_domain
        current = value
        for primitive_name in spec.expanded_primitives:
            primitive = self._typed_primitives[primitive_name]
            if primitive.input_domain != current_domain:
                raise LearnedToolError("typed learned tool chain changed after validation")
            try:
                current = primitive.function(current)
            except (TypeError, ValueError, OverflowError) as exc:
                raise LearnedToolError(
                    f"typed learned tool input failed {primitive_name}"
                ) from exc
            current_domain = primitive.output_domain
            if not _matches_domain(current_domain, current):
                raise LearnedToolError(
                    f"typed primitive {primitive_name} returned invalid {current_domain} value"
                )
        if current_domain != spec.output_domain:
            raise LearnedToolError("typed learned tool output domain mismatch")
        return current


def _candidate_payload(
    *,
    candidate_id: str,
    tool_id: str,
    scope: str,
    input_domain: str,
    output_domain: str,
    program_kind: str,
    expanded_primitives: tuple[str, ...],
    support_sha256: str,
    description: str,
) -> dict[str, Any]:
    spec = LearnedToolSpec(
        tool_id=tool_id,
        candidate_id=candidate_id,
        scope=scope,
        domain=input_domain,
        output_domain=output_domain,
        program_kind=program_kind,
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
            "input_domain": spec.input_domain,
            "output_domain": spec.output_domain,
            "program_kind": spec.program_kind,
            "expanded_primitives": list(spec.expanded_primitives),
            "support_sha256": spec.support_sha256,
            "description": spec.description,
        },
    }


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
    """Build an inactive legacy same-domain Candidate payload."""

    return _candidate_payload(
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=scope,
        input_domain=domain,
        output_domain=domain,
        program_kind="legacy_same_domain",
        expanded_primitives=expanded_primitives,
        support_sha256=support_sha256,
        description=description,
    )


def typed_learned_tool_candidate_payload(
    *,
    candidate_id: str,
    tool_id: str,
    scope: str,
    input_domain: str,
    output_domain: str,
    expanded_primitives: tuple[str, ...],
    support_sha256: str,
    description: str,
) -> dict[str, Any]:
    """Build an inactive well-typed Candidate payload without granting activation.

    Activation remains exclusively owned by record_candidate_regression. The declarative program is
    validated against the fixed typed primitive graph again when catalogued and on every invocation.
    """

    return _candidate_payload(
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=scope,
        input_domain=input_domain,
        output_domain=output_domain,
        program_kind="typed",
        expanded_primitives=expanded_primitives,
        support_sha256=support_sha256,
        description=description,
    )
