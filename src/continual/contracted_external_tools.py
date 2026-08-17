from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .candidate_regression import CandidateIntegrityError, candidate_verified_for_scope

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DOMAINS = {"string", "numeric", "sequence", "object", "boolean"}


class ContractedExternalToolError(ValueError):
    pass


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContractedExternalToolError("external tool value must be finite JSON") from exc


def _json_size(value: Any) -> int:
    return len(_json_bytes(value))


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _matches_domain(domain: str, value: Any) -> bool:
    if domain == "string":
        return isinstance(value, str)
    if domain == "numeric":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if domain == "sequence":
        return isinstance(value, (list, tuple))
    if domain == "object":
        return isinstance(value, Mapping)
    if domain == "boolean":
        return isinstance(value, bool)
    return False


@dataclass(frozen=True)
class ExternalToolContract:
    tool_id: str
    candidate_id: str
    scope: str
    input_domain: str
    output_domain: str
    effects: tuple[str, ...]
    max_calls: int
    max_input_bytes: int
    max_output_bytes: int
    adapter_sha256: str
    description: str
    binding_challenges: tuple[tuple[Any, str], ...] = ()

    @classmethod
    def from_candidate(cls, candidate: Mapping[str, Any]) -> "ExternalToolContract":
        if candidate.get("target_component") != "contracted_external_tool":
            raise ContractedExternalToolError("candidate is not a contracted external tool")
        raw = candidate.get("contracted_external_tool")
        if not isinstance(raw, Mapping):
            raise ContractedExternalToolError("contracted_external_tool must be an object")
        limits = raw.get("limits")
        if not isinstance(limits, Mapping):
            raise ContractedExternalToolError("external tool limits must be an object")
        raw_challenges = raw.get("binding_challenges", [])
        if not isinstance(raw_challenges, list):
            raise ContractedExternalToolError("external tool binding_challenges must be a list")
        challenges: list[tuple[Any, str]] = []
        for item in raw_challenges:
            if not isinstance(item, Mapping) or "input" not in item:
                raise ContractedExternalToolError("external tool binding challenge must contain input")
            challenges.append((item["input"], str(item.get("output_sha256", ""))))
        value = cls(
            tool_id=str(raw.get("tool_id", "")),
            candidate_id=str(candidate.get("candidate_id", "")),
            scope=str(raw.get("scope", candidate.get("expected_scope", ""))),
            input_domain=str(raw.get("input_domain", "")),
            output_domain=str(raw.get("output_domain", "")),
            effects=tuple(str(item) for item in raw.get("effects", [])),
            max_calls=int(limits.get("max_calls", 0)),
            max_input_bytes=int(limits.get("max_input_bytes", 0)),
            max_output_bytes=int(limits.get("max_output_bytes", 0)),
            adapter_sha256=str(raw.get("adapter_sha256", "")),
            description=str(raw.get("description", "")),
            binding_challenges=tuple(challenges),
        )
        value.validate()
        expected_scope = candidate.get("expected_scope")
        if isinstance(expected_scope, str) and expected_scope and expected_scope != value.scope:
            raise ContractedExternalToolError("external tool scope does not match candidate expected_scope")
        return value

    def validate(self) -> None:
        if not _SAFE_ID.fullmatch(self.tool_id) or not _SAFE_ID.fullmatch(self.candidate_id):
            raise ContractedExternalToolError("external tool and candidate IDs must be safe identifiers")
        if not self.scope.strip():
            raise ContractedExternalToolError("external tool scope must be non-empty")
        if self.input_domain not in _DOMAINS or self.output_domain not in _DOMAINS:
            raise ContractedExternalToolError("external tool domain is unsupported")
        if self.effects:
            raise ContractedExternalToolError(
                "only effect-free external adapters are executable; effectful tools remain quarantined"
            )
        if not 1 <= self.max_calls <= 64:
            raise ContractedExternalToolError("max_calls must be in [1, 64]")
        if not 1 <= self.max_input_bytes <= 65536:
            raise ContractedExternalToolError("max_input_bytes must be in [1, 65536]")
        if not 1 <= self.max_output_bytes <= 65536:
            raise ContractedExternalToolError("max_output_bytes must be in [1, 65536]")
        if not _SHA256.fullmatch(self.adapter_sha256):
            raise ContractedExternalToolError("adapter_sha256 must be lowercase SHA-256")
        if not self.description.strip():
            raise ContractedExternalToolError("external tool description must be non-empty")
        if len(self.binding_challenges) > 8:
            raise ContractedExternalToolError("external tool may have at most 8 binding challenges")
        seen: set[str] = set()
        for challenge_input, output_sha256 in self.binding_challenges:
            if not _matches_domain(self.input_domain, challenge_input):
                raise ContractedExternalToolError(
                    f"binding challenge expected {self.input_domain} input"
                )
            if _json_size(challenge_input) > self.max_input_bytes:
                raise ContractedExternalToolError("binding challenge input exceeds contract byte limit")
            if not _SHA256.fullmatch(output_sha256):
                raise ContractedExternalToolError(
                    "binding challenge output_sha256 must be lowercase SHA-256"
                )
            digest = _json_sha256(challenge_input)
            if digest in seen:
                raise ContractedExternalToolError("duplicate external tool binding challenge input")
            seen.add(digest)

    @property
    def behavior_binding_required(self) -> bool:
        return bool(self.binding_challenges)

    def descriptor(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "candidate_id": self.candidate_id,
            "scope": self.scope,
            "program_kind": "contracted_external_tool",
            "input_domain": self.input_domain,
            "output_domain": self.output_domain,
            "effects": [],
            "max_calls": self.max_calls,
            "max_input_bytes": self.max_input_bytes,
            "max_output_bytes": self.max_output_bytes,
            "adapter_sha256": self.adapter_sha256,
            "binding_challenge_count": len(self.binding_challenges),
            "behavior_binding_required": self.behavior_binding_required,
            "description": self.description,
        }


@dataclass(frozen=True)
class ExternalToolAdapter:
    adapter_sha256: str
    function: Callable[[Any], Any]

    def validate(self) -> None:
        if not _SHA256.fullmatch(self.adapter_sha256):
            raise ContractedExternalToolError("adapter identity must be lowercase SHA-256")
        if not callable(self.function):
            raise ContractedExternalToolError("external adapter function must be callable")


class ContractedExternalToolRegistry:
    """Expose host-supplied unknown tools only after exact contract regression verification.

    The repository persists no executable adapter source. A host must explicitly supply an adapter
    whose declared identity matches the Candidate contract. New executable contracts additionally
    carry deterministic behavior-binding challenges: the registry evaluates the host adapter on those
    committed challenge inputs and compares canonical output hashes before exposing it. Legacy
    contracts without challenges remain readable evidence but fail closed and cannot execute. Only
    effect-free adapters are executable, and every registry instance enforces exact scope, typed I/O,
    call count, and JSON byte limits. Shell, network, filesystem, subprocess, and arbitrary generated-
    code effects therefore do not become available merely because a Candidate requests them.
    """

    def __init__(self, root: Path, adapters: Mapping[str, ExternalToolAdapter]) -> None:
        self.root = root.resolve()
        self.adapters = dict(adapters)
        self._calls: dict[tuple[str, str], int] = {}
        self._behavior_verified: set[tuple[str, str]] = set()
        for key, adapter in self.adapters.items():
            if not isinstance(key, str) or not _SAFE_ID.fullmatch(key):
                raise ContractedExternalToolError("adapter map contains invalid tool_id")
            adapter.validate()

    @property
    def candidates_dir(self) -> Path:
        return self.root / ".continual" / "candidates"

    def _candidate_values(self) -> tuple[dict[str, Any], ...]:
        if not self.candidates_dir.exists():
            return ()
        values: list[dict[str, Any]] = []
        for path in sorted(self.candidates_dir.glob("*/candidate.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("target_component") == "contracted_external_tool":
                values.append(raw)
        return tuple(values)

    def _verify_behavior_binding(
        self,
        contract: ExternalToolContract,
        adapter: ExternalToolAdapter,
    ) -> None:
        key = (contract.scope, contract.tool_id)
        if key in self._behavior_verified:
            return
        if not contract.binding_challenges:
            raise ContractedExternalToolError(
                "external tool contract lacks behavior-binding challenges and is quarantined"
            )
        for challenge_input, expected_sha256 in contract.binding_challenges:
            try:
                output = adapter.function(challenge_input)
            except Exception as exc:
                raise ContractedExternalToolError(
                    "external adapter failed behavior-binding challenge"
                ) from exc
            if not _matches_domain(contract.output_domain, output):
                raise ContractedExternalToolError(
                    f"external adapter binding challenge returned invalid {contract.output_domain} output"
                )
            if _json_size(output) > contract.max_output_bytes:
                raise ContractedExternalToolError(
                    "external adapter binding challenge output exceeds contract byte limit"
                )
            if _json_sha256(output) != expected_sha256:
                raise ContractedExternalToolError(
                    "external adapter behavior does not match verified binding challenges"
                )
        self._behavior_verified.add(key)

    def available(self, scope: str) -> tuple[ExternalToolContract, ...]:
        if not isinstance(scope, str) or not scope.strip():
            raise ContractedExternalToolError("scope must be a non-empty string")
        selected: dict[str, ExternalToolContract] = {}
        for candidate in self._candidate_values():
            contract = ExternalToolContract.from_candidate(candidate)
            if contract.scope != scope:
                continue
            try:
                verified = candidate_verified_for_scope(self.root, candidate, scope)
            except CandidateIntegrityError as exc:
                raise ContractedExternalToolError(str(exc)) from exc
            if not verified:
                continue
            adapter = self.adapters.get(contract.tool_id)
            if adapter is None:
                continue
            if adapter.adapter_sha256 != contract.adapter_sha256:
                raise ContractedExternalToolError("host adapter identity does not match verified contract")
            self._verify_behavior_binding(contract, adapter)
            if contract.tool_id in selected:
                raise ContractedExternalToolError("duplicate verified external tool_id")
            selected[contract.tool_id] = contract
        return tuple(selected[key] for key in sorted(selected))

    def descriptors(self, scope: str) -> tuple[dict[str, Any], ...]:
        return tuple(item.descriptor() for item in self.available(scope))

    def apply(self, *, scope: str, tool_id: str, value: Any) -> Any:
        contract = {item.tool_id: item for item in self.available(scope)}.get(tool_id)
        if contract is None:
            raise ContractedExternalToolError(
                f"external tool is not verified and host-bound for scope: {scope}:{tool_id}"
            )
        if not _matches_domain(contract.input_domain, value):
            raise ContractedExternalToolError(
                f"external tool expected {contract.input_domain} input"
            )
        if _json_size(value) > contract.max_input_bytes:
            raise ContractedExternalToolError("external tool input exceeds contract byte limit")
        key = (scope, tool_id)
        used = self._calls.get(key, 0)
        if used >= contract.max_calls:
            raise ContractedExternalToolError("external tool call budget exhausted")
        self._calls[key] = used + 1
        adapter = self.adapters[tool_id]
        try:
            output = adapter.function(value)
        except Exception as exc:
            raise ContractedExternalToolError("external adapter execution failed") from exc
        if not _matches_domain(contract.output_domain, output):
            raise ContractedExternalToolError(
                f"external adapter returned invalid {contract.output_domain} output"
            )
        if _json_size(output) > contract.max_output_bytes:
            raise ContractedExternalToolError("external tool output exceeds contract byte limit")
        return output


def contracted_external_tool_candidate_payload(
    *,
    candidate_id: str,
    tool_id: str,
    scope: str,
    input_domain: str,
    output_domain: str,
    effects: tuple[str, ...],
    max_calls: int,
    max_input_bytes: int,
    max_output_bytes: int,
    adapter_sha256: str,
    description: str,
    binding_challenges: tuple[Mapping[str, Any], ...] = (),
) -> dict[str, Any]:
    normalized_challenges = tuple(
        (item.get("input"), str(item.get("output_sha256", "")))
        for item in binding_challenges
        if isinstance(item, Mapping)
    )
    if len(normalized_challenges) != len(binding_challenges):
        raise ContractedExternalToolError("binding challenges must be objects")
    contract = ExternalToolContract(
        tool_id=tool_id,
        candidate_id=candidate_id,
        scope=scope,
        input_domain=input_domain,
        output_domain=output_domain,
        effects=effects,
        max_calls=max_calls,
        max_input_bytes=max_input_bytes,
        max_output_bytes=max_output_bytes,
        adapter_sha256=adapter_sha256,
        description=description,
        binding_challenges=normalized_challenges,
    )
    contract.validate()
    return {
        "candidate_id": candidate_id,
        "target_component": "contracted_external_tool",
        "expected_scope": scope,
        "status": "candidate",
        "scope_states": {},
        "verified_scope_states": {},
        "supporting_evidence": [],
        "contradictory_evidence": [],
        "regression_decision_refs": [],
        "contracted_external_tool": {
            "tool_id": tool_id,
            "scope": scope,
            "input_domain": input_domain,
            "output_domain": output_domain,
            "effects": list(effects),
            "limits": {
                "max_calls": max_calls,
                "max_input_bytes": max_input_bytes,
                "max_output_bytes": max_output_bytes,
            },
            "adapter_sha256": adapter_sha256,
            "binding_challenges": [
                {"input": challenge_input, "output_sha256": output_sha256}
                for challenge_input, output_sha256 in normalized_challenges
            ],
            "description": description,
        },
    }
