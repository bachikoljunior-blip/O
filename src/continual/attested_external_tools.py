from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .candidate_regression import CandidateIntegrityError, candidate_verified_for_scope
from .contracted_external_tools import (
    ContractedExternalToolError,
    ExternalToolContract,
    _detached_json,
    _json_sha256,
    _json_size,
    _matches_domain,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ExternalRunnerAttestationError(ContractedExternalToolError):
    """Raised when an external runner cannot prove the required isolation profile."""


@dataclass(frozen=True)
class RunnerIsolationProfile:
    """Isolation properties required before a host adapter can enter the active runtime.

    These fields are intentionally fixed by repository policy rather than chosen by a Candidate. An
    attestation only states that a runner supplies these properties; a separately configured verifier
    must independently trust the exact attestation digest before the runtime accepts the statement.
    """

    execution_boundary: str = "external-isolated-runner"
    canonical_json_transport_only: bool = True
    filesystem_isolated: bool = True
    network_isolated: bool = True
    subprocess_isolated: bool = True
    environment_isolated: bool = True
    resource_limits_enforced: bool = True

    def validate_required(self) -> None:
        required = RunnerIsolationProfile()
        if self != required:
            raise ExternalRunnerAttestationError(
                "external runner does not attest the complete required isolation profile"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunnerIsolationAttestation:
    verifier_id: str
    runner_id: str
    runner_sha256: str
    adapter_sha256: str
    profile: RunnerIsolationProfile
    statement_sha256: str

    @classmethod
    def create(
        cls,
        *,
        verifier_id: str,
        runner_id: str,
        runner_sha256: str,
        adapter_sha256: str,
        profile: RunnerIsolationProfile | None = None,
    ) -> "RunnerIsolationAttestation":
        active_profile = profile or RunnerIsolationProfile()
        statement = {
            "schema_version": 1,
            "verifier_id": verifier_id,
            "runner_id": runner_id,
            "runner_sha256": runner_sha256,
            "adapter_sha256": adapter_sha256,
            "profile": active_profile.to_dict(),
        }
        digest = hashlib.sha256(
            json.dumps(
                statement,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        value = cls(
            verifier_id=verifier_id,
            runner_id=runner_id,
            runner_sha256=runner_sha256,
            adapter_sha256=adapter_sha256,
            profile=active_profile,
            statement_sha256=digest,
        )
        value.validate()
        return value

    def statement(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "verifier_id": self.verifier_id,
            "runner_id": self.runner_id,
            "runner_sha256": self.runner_sha256,
            "adapter_sha256": self.adapter_sha256,
            "profile": self.profile.to_dict(),
        }

    def recomputed_statement_sha256(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.statement(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()

    def validate(self) -> None:
        if not _SAFE_ID.fullmatch(self.verifier_id) or not _SAFE_ID.fullmatch(self.runner_id):
            raise ExternalRunnerAttestationError("runner verifier and runner IDs must be safe identifiers")
        if not _SHA256.fullmatch(self.runner_sha256):
            raise ExternalRunnerAttestationError("runner_sha256 must be lowercase SHA-256")
        if not _SHA256.fullmatch(self.adapter_sha256):
            raise ExternalRunnerAttestationError("adapter_sha256 must be lowercase SHA-256")
        if not _SHA256.fullmatch(self.statement_sha256):
            raise ExternalRunnerAttestationError("statement_sha256 must be lowercase SHA-256")
        self.profile.validate_required()
        if self.recomputed_statement_sha256() != self.statement_sha256:
            raise ExternalRunnerAttestationError("runner isolation attestation digest mismatch")


class PinnedRunnerAttestationVerifier:
    """Verify runner attestations against trust pins supplied separately from the runner.

    A runner cannot authorize itself merely by embedding an attestation: the exact canonical
    attestation digest must also be present in this independently supplied trust map. Production trust
    pins are expected to come from an external attestation/verifier control plane. Internal tests may
    use deterministic pins only to falsify the fail-closed state machine; such pins are not external
    production evidence.
    """

    def __init__(self, trusted_statement_sha256: Mapping[tuple[str, str], str]) -> None:
        pins: dict[tuple[str, str], str] = {}
        for key, digest in trusted_statement_sha256.items():
            if (
                not isinstance(key, tuple)
                or len(key) != 2
                or not all(isinstance(item, str) and _SAFE_ID.fullmatch(item) for item in key)
            ):
                raise ExternalRunnerAttestationError(
                    "runner attestation trust keys must be (verifier_id, runner_id)"
                )
            if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                raise ExternalRunnerAttestationError(
                    "runner attestation trust pins must be lowercase SHA-256"
                )
            pins[(key[0], key[1])] = digest
        self._pins = pins

    def verify(self, attestation: RunnerIsolationAttestation) -> bool:
        try:
            attestation.validate()
        except ExternalRunnerAttestationError:
            return False
        expected = self._pins.get((attestation.verifier_id, attestation.runner_id))
        return expected == attestation.statement_sha256


@dataclass(frozen=True)
class ExternalToolRunner:
    """Host runner identity plus an independently checkable isolation statement."""

    adapter_sha256: str
    runner_sha256: str
    attestation: RunnerIsolationAttestation
    invoke: Callable[[Any], Any]

    def validate(self) -> None:
        if not _SHA256.fullmatch(self.adapter_sha256):
            raise ExternalRunnerAttestationError("runner adapter identity must be lowercase SHA-256")
        if not _SHA256.fullmatch(self.runner_sha256):
            raise ExternalRunnerAttestationError("runner identity must be lowercase SHA-256")
        if not callable(self.invoke):
            raise ExternalRunnerAttestationError("external runner invoke must be callable")
        self.attestation.validate()
        if self.attestation.adapter_sha256 != self.adapter_sha256:
            raise ExternalRunnerAttestationError("runner attestation is bound to a different adapter")
        if self.attestation.runner_sha256 != self.runner_sha256:
            raise ExternalRunnerAttestationError("runner attestation is bound to a different runner")


class AttestedContractedExternalToolRegistry:
    """Fail closed unless an exact-scope Candidate is attached to an independently trusted runner.

    Candidate regression, adapter identity, behavior-binding challenges, typed JSON boundaries, byte
    limits, call budgets, and detached input/output copies remain required. In addition, the runtime
    refuses to call a runner until a verifier configured separately from that runner trusts an exact
    attestation of the repository-required isolation profile. A missing, weakened, altered, or
    untrusted attestation is rejected *before* behavior-binding challenges or task input are invoked.

    This class verifies the trust boundary; it cannot inspect a remote runner deeply enough to prove
    that the attested isolation properties are true. That truth must be established by the external
    verifier/control plane that issues the pin. Therefore an internally pinned test runner is useful
    safety evidence but is not independent production evidence and cannot support an AGI claim.
    """

    def __init__(
        self,
        root: Path,
        runners: Mapping[str, ExternalToolRunner],
        verifier: PinnedRunnerAttestationVerifier | None,
    ) -> None:
        self.root = root.resolve()
        self.runners = dict(runners)
        self.verifier = verifier
        self._calls: dict[tuple[str, str], int] = {}
        self._behavior_verified: set[tuple[str, str]] = set()
        self._runner_verified: set[tuple[str, str]] = set()
        if self.runners and self.verifier is None:
            raise ExternalRunnerAttestationError(
                "external runners require a separately configured isolation attestation verifier"
            )
        for tool_id, runner in self.runners.items():
            if not isinstance(tool_id, str) or not _SAFE_ID.fullmatch(tool_id):
                raise ExternalRunnerAttestationError("runner map contains invalid tool_id")
            if not isinstance(runner, ExternalToolRunner):
                raise ExternalRunnerAttestationError(
                    "arbitrary in-process external adapters are quarantined; ExternalToolRunner required"
                )
            runner.validate()

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

    def _verified_runner(
        self,
        contract: ExternalToolContract,
    ) -> ExternalToolRunner | None:
        runner = self.runners.get(contract.tool_id)
        if runner is None:
            return None
        if runner.adapter_sha256 != contract.adapter_sha256:
            raise ExternalRunnerAttestationError(
                "external runner adapter identity does not match verified Candidate contract"
            )
        key = (contract.scope, contract.tool_id)
        if key not in self._runner_verified:
            if self.verifier is None or not self.verifier.verify(runner.attestation):
                raise ExternalRunnerAttestationError(
                    "external runner isolation attestation is not independently trusted"
                )
            runner.attestation.profile.validate_required()
            self._runner_verified.add(key)
        return runner

    def _verify_behavior_binding(
        self,
        contract: ExternalToolContract,
        runner: ExternalToolRunner,
    ) -> None:
        key = (contract.scope, contract.tool_id)
        if key in self._behavior_verified:
            return
        if not contract.binding_challenges:
            raise ExternalRunnerAttestationError(
                "external tool contract lacks behavior-binding challenges and is quarantined"
            )
        for challenge_input, expected_sha256 in contract.binding_challenges:
            try:
                output = runner.invoke(_detached_json(challenge_input))
            except Exception as exc:
                raise ExternalRunnerAttestationError(
                    "external runner failed behavior-binding challenge"
                ) from exc
            if not _matches_domain(contract.output_domain, output):
                raise ExternalRunnerAttestationError(
                    f"external runner binding challenge returned invalid {contract.output_domain} output"
                )
            if _json_size(output) > contract.max_output_bytes:
                raise ExternalRunnerAttestationError(
                    "external runner binding challenge output exceeds contract byte limit"
                )
            if _json_sha256(output) != expected_sha256:
                raise ExternalRunnerAttestationError(
                    "external runner behavior does not match verified binding challenges"
                )
        self._behavior_verified.add(key)

    def available(self, scope: str) -> tuple[ExternalToolContract, ...]:
        if not isinstance(scope, str) or not scope.strip():
            raise ExternalRunnerAttestationError("scope must be a non-empty string")
        selected: dict[str, ExternalToolContract] = {}
        for candidate in self._candidate_values():
            contract = ExternalToolContract.from_candidate(candidate)
            if contract.scope != scope:
                continue
            try:
                verified = candidate_verified_for_scope(self.root, candidate, scope)
            except CandidateIntegrityError as exc:
                raise ExternalRunnerAttestationError(str(exc)) from exc
            if not verified:
                continue
            runner = self._verified_runner(contract)
            if runner is None:
                continue
            self._verify_behavior_binding(contract, runner)
            if contract.tool_id in selected:
                raise ExternalRunnerAttestationError("duplicate verified external tool_id")
            selected[contract.tool_id] = contract
        return tuple(selected[key] for key in sorted(selected))

    def descriptors(self, scope: str) -> tuple[dict[str, Any], ...]:
        descriptors: list[dict[str, Any]] = []
        for contract in self.available(scope):
            runner = self.runners[contract.tool_id]
            value = contract.descriptor()
            value.update(
                {
                    "runner_isolation_attested": True,
                    "runner_id": runner.attestation.runner_id,
                    "runner_sha256": runner.runner_sha256,
                    "runner_verifier_id": runner.attestation.verifier_id,
                    "runner_attestation_sha256": runner.attestation.statement_sha256,
                    "runner_isolation_profile": runner.attestation.profile.to_dict(),
                }
            )
            descriptors.append(value)
        return tuple(descriptors)

    def apply(self, *, scope: str, tool_id: str, value: Any) -> Any:
        contract = {item.tool_id: item for item in self.available(scope)}.get(tool_id)
        if contract is None:
            raise ExternalRunnerAttestationError(
                f"external tool is not verified and runner-attested for scope: {scope}:{tool_id}"
            )
        if not _matches_domain(contract.input_domain, value):
            raise ExternalRunnerAttestationError(
                f"external tool expected {contract.input_domain} input"
            )
        if _json_size(value) > contract.max_input_bytes:
            raise ExternalRunnerAttestationError("external tool input exceeds contract byte limit")
        key = (scope, tool_id)
        used = self._calls.get(key, 0)
        if used >= contract.max_calls:
            raise ExternalRunnerAttestationError("external tool call budget exhausted")
        self._calls[key] = used + 1
        runner = self.runners[tool_id]
        try:
            output = runner.invoke(_detached_json(value))
        except Exception as exc:
            raise ExternalRunnerAttestationError("external runner execution failed") from exc
        if not _matches_domain(contract.output_domain, output):
            raise ExternalRunnerAttestationError(
                f"external runner returned invalid {contract.output_domain} output"
            )
        if _json_size(output) > contract.max_output_bytes:
            raise ExternalRunnerAttestationError("external tool output exceeds contract byte limit")
        return _detached_json(output)
