from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agi.acquired_programs import (
    AcquiredProgramError,
    execute_program,
    validate_program_descriptor,
)

from .candidate_regression import CandidateIntegrityError, candidate_verified_for_scope

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AcquiredProgramToolError(ValueError):
    pass


@dataclass(frozen=True)
class AcquiredProgramToolSpec:
    tool_id: str
    candidate_id: str
    scope: str
    description: str
    program: dict[str, Any]

    @classmethod
    def from_candidate(cls, candidate: Mapping[str, Any]) -> "AcquiredProgramToolSpec":
        if candidate.get("target_component") != "acquired_program":
            raise AcquiredProgramToolError("candidate is not an acquired-program candidate")
        raw = candidate.get("acquired_program")
        if not isinstance(raw, Mapping):
            raise AcquiredProgramToolError("candidate acquired_program must be an object")
        program = raw.get("program")
        if not isinstance(program, Mapping):
            raise AcquiredProgramToolError("acquired program candidate requires program descriptor")
        value = cls(
            tool_id=str(raw.get("tool_id", "")),
            candidate_id=str(candidate.get("candidate_id", "")),
            scope=str(raw.get("scope", candidate.get("expected_scope", ""))),
            description=str(raw.get("description", "")),
            program=dict(program),
        )
        value.validate()
        expected_scope = candidate.get("expected_scope")
        if isinstance(expected_scope, str) and expected_scope and value.scope != expected_scope:
            raise AcquiredProgramToolError("acquired program scope does not match candidate expected_scope")
        return value

    def validate(self) -> None:
        if not _SAFE_ID.fullmatch(self.tool_id):
            raise AcquiredProgramToolError("invalid acquired program tool_id")
        if not _SAFE_ID.fullmatch(self.candidate_id):
            raise AcquiredProgramToolError("invalid acquired program candidate_id")
        if not self.scope.strip():
            raise AcquiredProgramToolError("acquired program scope must be non-empty")
        if not self.description.strip():
            raise AcquiredProgramToolError("acquired program description must be non-empty")
        try:
            meta = validate_program_descriptor(self.program)
        except AcquiredProgramError as exc:
            raise AcquiredProgramToolError(str(exc)) from exc
        support = self.program.get("support_sha256")
        if not isinstance(support, str) or not _SHA256.fullmatch(support):
            raise AcquiredProgramToolError("acquired program support_sha256 must be lowercase SHA-256")
        if meta["effects"]:
            raise AcquiredProgramToolError("acquired program must be pure")

    def descriptor(self) -> dict[str, Any]:
        meta = validate_program_descriptor(self.program)
        return {
            "tool_id": self.tool_id,
            "candidate_id": self.candidate_id,
            "scope": self.scope,
            "program_kind": "acquired_program",
            "input_domain": meta["input_domain"],
            "output_domain": meta["output_domain"],
            "description": self.description,
            "program_nodes": meta["nodes"],
            "program_depth": meta["depth"],
            "max_steps": meta["max_steps"],
            "max_output_length": meta["max_output_length"],
            "effects": [],
            "support_sha256": str(self.program["support_sha256"]),
        }


class AcquiredProgramRegistry:
    """Expose only exact-scope, deterministic-regression-verified pure acquired programs."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

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
                raise AcquiredProgramToolError(f"candidate must be an object: {path}")
            if raw.get("target_component") == "acquired_program":
                values.append(raw)
        return tuple(values)

    def _verified_for_scope(self, candidate: Mapping[str, Any], scope: str) -> bool:
        try:
            return candidate_verified_for_scope(self.root, candidate, scope)
        except CandidateIntegrityError as exc:
            raise AcquiredProgramToolError(str(exc)) from exc

    def available(self, scope: str) -> tuple[AcquiredProgramToolSpec, ...]:
        if not isinstance(scope, str) or not scope.strip():
            raise AcquiredProgramToolError("scope must be a non-empty string")
        selected: dict[str, AcquiredProgramToolSpec] = {}
        for candidate in self._candidate_values():
            spec = AcquiredProgramToolSpec.from_candidate(candidate)
            if spec.scope != scope or not self._verified_for_scope(candidate, scope):
                continue
            if spec.tool_id in selected:
                raise AcquiredProgramToolError(
                    f"multiple verified acquired programs provide tool_id {spec.tool_id}"
                )
            selected[spec.tool_id] = spec
        return tuple(selected[key] for key in sorted(selected))

    def descriptors(self, scope: str) -> tuple[dict[str, Any], ...]:
        return tuple(spec.descriptor() for spec in self.available(scope))

    def apply(self, *, scope: str, tool_id: str, value: Any) -> Any:
        selected = {spec.tool_id: spec for spec in self.available(scope)}
        spec = selected.get(tool_id)
        if spec is None:
            raise AcquiredProgramToolError(
                f"acquired program is not verified for scope: {scope}:{tool_id}"
            )
        try:
            return execute_program(spec.program, value)
        except AcquiredProgramError as exc:
            raise AcquiredProgramToolError(str(exc)) from exc


def acquired_program_candidate_payload(
    *,
    candidate_id: str,
    tool_id: str,
    scope: str,
    program: Mapping[str, Any],
    description: str,
) -> dict[str, Any]:
    """Build an inactive acquired-program Candidate; activation still requires regression."""

    spec = AcquiredProgramToolSpec(
        tool_id=tool_id,
        candidate_id=candidate_id,
        scope=scope,
        description=description,
        program=dict(program),
    )
    spec.validate()
    return {
        "candidate_id": candidate_id,
        "target_component": "acquired_program",
        "expected_scope": scope,
        "status": "candidate",
        "scope_states": {},
        "verified_scope_states": {},
        "supporting_evidence": [],
        "contradictory_evidence": [],
        "regression_decision_refs": [],
        "acquired_program": {
            "tool_id": tool_id,
            "scope": scope,
            "description": description,
            "program": dict(program),
        },
    }
