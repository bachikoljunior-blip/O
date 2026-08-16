from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class Criterion:
    key: str
    description: str


CRITERIA: tuple[Criterion, ...] = (
    Criterion("breadth", "Succeeds across materially different task domains without task-specific retraining."),
    Criterion("transfer", "Transfers knowledge and skills to novel tasks and environments."),
    Criterion("autonomy", "Plans and completes long-horizon tasks while recovering from failures."),
    Criterion("continual_learning", "Improves from experience while preserving previously verified capabilities."),
    Criterion("self_improvement", "Proposes, tests, and safely adopts improvements to its own procedures."),
    Criterion("robustness", "Maintains performance under distribution shift, adversarial cases, and tool failures."),
)
CRITERION_KEYS = tuple(criterion.key for criterion in CRITERIA)
EVIDENCE_TIERS = {"self_report", "development", "production"}


@dataclass(frozen=True)
class EvidenceRecord:
    criterion: str
    task_id: str
    domain: str
    success: bool
    run_id: str
    artifact_ref: str
    evaluator: str
    tier: str = "development"
    independent: bool = False
    timestamp: str | None = None
    notes: str | None = None
    producer: str = ""
    attestor_id: str = ""
    attestation: str = ""
    provenance_ref: str = ""

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.criterion not in CRITERION_KEYS:
            errors.append(f"unknown criterion: {self.criterion}")
        for name in ("task_id", "domain", "run_id", "artifact_ref", "evaluator"):
            if not getattr(self, name).strip():
                errors.append(f"{name} must be non-empty")
        if self.tier not in EVIDENCE_TIERS:
            errors.append(f"unknown tier: {self.tier}")
        if self.independent:
            if not self.producer.strip():
                errors.append("independent evidence requires producer identity")
            if not self.attestor_id.strip():
                errors.append("independent evidence requires attestor_id")
            if not self.attestation.strip():
                errors.append("independent evidence requires attestation")
            if self.producer and self.producer == self.evaluator:
                errors.append("independent evidence producer and evaluator must differ")
            if self.attestor_id and self.attestor_id in {self.producer, self.evaluator}:
                errors.append("independent evidence attestor must be a third identity")
        return errors

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EvidenceRecord":
        return cls(
            criterion=str(value.get("criterion", "")),
            task_id=str(value.get("task_id", "")),
            domain=str(value.get("domain", "")),
            success=bool(value.get("success", False)),
            run_id=str(value.get("run_id", "")),
            artifact_ref=str(value.get("artifact_ref", "")),
            evaluator=str(value.get("evaluator", "")),
            tier=str(value.get("tier", "development")),
            independent=bool(value.get("independent", False)),
            timestamp=(str(value["timestamp"]) if value.get("timestamp") is not None else None),
            notes=(str(value["notes"]) if value.get("notes") is not None else None),
            producer=str(value.get("producer", "")),
            attestor_id=str(value.get("attestor_id", "")),
            attestation=str(value.get("attestation", "")),
            provenance_ref=str(value.get("provenance_ref", "")),
        )


@dataclass(frozen=True)
class EvaluationPolicy:
    """Conservative claim policy.

    Development benchmarks are useful for iteration but cannot alone establish AGI.
    A positive claim requires repeated, independently attested, production-tier evidence across
    every criterion, multiple tasks, multiple runs, and multiple domains.
    """

    min_successes_per_criterion: int = 3
    min_distinct_tasks_per_criterion: int = 3
    min_distinct_runs_per_criterion: int = 2
    min_distinct_domains_per_criterion: int = 2
    min_independent_successes_per_criterion: int = 1
    min_distinct_independent_attestors_per_criterion: int = 1
    require_production_tier: bool = True
    require_verified_independent_attestation: bool = True
    disallow_unresolved_production_failures: bool = True

    def validate(self) -> None:
        fields = (
            self.min_successes_per_criterion,
            self.min_distinct_tasks_per_criterion,
            self.min_distinct_runs_per_criterion,
            self.min_distinct_domains_per_criterion,
            self.min_independent_successes_per_criterion,
            self.min_distinct_independent_attestors_per_criterion,
        )
        if any(value < 1 for value in fields):
            raise ValueError("all minimum evidence thresholds must be at least 1")


def _attestation_payload(record: EvidenceRecord) -> bytes:
    value = {
        "criterion": record.criterion,
        "task_id": record.task_id,
        "domain": record.domain,
        "success": record.success,
        "run_id": record.run_id,
        "artifact_ref": record.artifact_ref,
        "evaluator": record.evaluator,
        "tier": record.tier,
        "producer": record.producer,
        "attestor_id": record.attestor_id,
        "timestamp": record.timestamp,
    }
    # Backward-compatible extension: legacy records with an empty provenance_ref retain the exact
    # pre-extension HMAC payload. Externally verified records opt in and bind the provenance chain
    # into the bridge attestation so it cannot be swapped after verification.
    if record.provenance_ref:
        value["provenance_ref"] = record.provenance_ref
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_evidence(record: EvidenceRecord, *, attestor_id: str, secret: str) -> EvidenceRecord:
    """Attach a deterministic HMAC attestation from an external trust identity.

    Production deployments should keep ``secret`` outside the repository (for example in an
    independent evaluation service or secret store). The repository persists only the signature.
    """
    if not attestor_id or not secret:
        raise ValueError("attestor_id and secret must be non-empty")
    prepared = replace(record, attestor_id=attestor_id, attestation="")
    signature = hmac.new(secret.encode(), _attestation_payload(prepared), hashlib.sha256).hexdigest()
    return replace(prepared, attestation=signature)


def verify_independent_attestation(
    record: EvidenceRecord,
    trusted_attestors: Mapping[str, str] | None,
) -> bool:
    if not record.independent or record.validate():
        return False
    if not trusted_attestors:
        return False
    secret = trusted_attestors.get(record.attestor_id)
    if not secret:
        return False
    expected = hmac.new(secret.encode(), _attestation_payload(replace(record, attestation="")), hashlib.sha256).hexdigest()
    return hmac.compare_digest(record.attestation, expected)


def _legacy_records(assertions: Mapping[str, bool]) -> list[EvidenceRecord]:
    return [
        EvidenceRecord(
            criterion=key,
            task_id=f"legacy-{key}",
            domain="unspecified",
            success=bool(assertions.get(key, False)),
            run_id="legacy-self-report",
            artifact_ref="none",
            evaluator="self-report",
            tier="self_report",
            independent=False,
            notes="Converted from legacy boolean assertion; not claim-grade evidence.",
        )
        for key in CRITERION_KEYS
    ]


def normalize_records(
    evidence: Mapping[str, bool] | Iterable[EvidenceRecord | Mapping[str, Any]],
) -> list[EvidenceRecord]:
    if isinstance(evidence, Mapping) and set(evidence).issubset(set(CRITERION_KEYS)):
        return _legacy_records({str(key): bool(value) for key, value in evidence.items()})
    records: list[EvidenceRecord] = []
    for value in evidence:  # type: ignore[union-attr]
        record = value if isinstance(value, EvidenceRecord) else EvidenceRecord.from_mapping(value)
        records.append(record)
    return records


def _criterion_result(
    key: str,
    records: Sequence[EvidenceRecord],
    policy: EvaluationPolicy,
    trusted_attestors: Mapping[str, str] | None,
) -> dict[str, Any]:
    relevant = [record for record in records if record.criterion == key]
    valid = [record for record in relevant if not record.validate()]
    successes = [record for record in valid if record.success]
    claim_successes = [record for record in successes if not policy.require_production_tier or record.tier == "production"]
    production_failures = [record for record in valid if not record.success and record.tier == "production"]

    verified_independent = [
        record
        for record in claim_successes
        if record.independent and (
            not policy.require_verified_independent_attestation
            or verify_independent_attestation(record, trusted_attestors)
        )
    ]
    # A repeated copy of one attested result cannot inflate the independent evidence count.
    unique_independent: dict[tuple[str, str, str, str], EvidenceRecord] = {}
    for record in verified_independent:
        key_tuple = (record.attestor_id, record.run_id, record.task_id, record.artifact_ref)
        unique_independent[key_tuple] = record
    independent = list(unique_independent.values())

    tasks = {record.task_id for record in claim_successes}
    runs = {record.run_id for record in claim_successes}
    domains = {record.domain for record in claim_successes}
    attestors = {record.attestor_id for record in independent}

    checks = {
        "success_count": len(claim_successes) >= policy.min_successes_per_criterion,
        "distinct_tasks": len(tasks) >= policy.min_distinct_tasks_per_criterion,
        "distinct_runs": len(runs) >= policy.min_distinct_runs_per_criterion,
        "distinct_domains": len(domains) >= policy.min_distinct_domains_per_criterion,
        "verified_independent_successes": len(independent) >= policy.min_independent_successes_per_criterion,
        "distinct_independent_attestors": len(attestors) >= policy.min_distinct_independent_attestors_per_criterion,
        "no_unresolved_production_failures": (
            not production_failures or not policy.disallow_unresolved_production_failures
        ),
    }
    passed = all(checks.values())
    return {
        "passed": passed,
        "checks": checks,
        "counts": {
            "all_records": len(relevant),
            "valid_records": len(valid),
            "claim_grade_successes": len(claim_successes),
            "verified_independent_successes": len(independent),
            "production_failures": len(production_failures),
            "distinct_tasks": len(tasks),
            "distinct_runs": len(runs),
            "distinct_domains": len(domains),
            "distinct_independent_attestors": len(attestors),
        },
        "record_refs": [
            {"run_id": record.run_id, "task_id": record.task_id, "artifact_ref": record.artifact_ref}
            for record in relevant
        ],
    }


def evaluate_evidence(
    evidence: Mapping[str, bool] | Iterable[EvidenceRecord | Mapping[str, Any]],
    policy: EvaluationPolicy | None = None,
    *,
    trusted_attestors: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Evaluate claim-grade evidence without trusting a bare independence assertion.

    Independent successes count only when an attestation verifies against a trust key supplied
    outside the evidence itself. Missing trust configuration therefore fails closed.
    """
    selected_policy = policy or EvaluationPolicy()
    selected_policy.validate()
    records = normalize_records(evidence)
    invalid = [
        {"record": asdict(record), "errors": record.validate()}
        for record in records
        if record.validate()
    ]
    results = {
        criterion.key: _criterion_result(criterion.key, records, selected_policy, trusted_attestors)
        for criterion in CRITERIA
    }
    missing = [key for key, result in results.items() if not result["passed"]]
    supported = not missing and not invalid
    return {
        "agi_claim_supported": supported,
        "criteria": results,
        "missing": missing,
        "invalid_records": invalid,
        "policy": asdict(selected_policy),
        "record_count": len(records),
        "trusted_attestor_ids": sorted(trusted_attestors or {}),
        "claim": (
            "AGI claim is supported by the supplied evidence under this policy."
            if supported
            else "AGI claim is not supported; continue capability work and evidence collection."
        ),
    }
