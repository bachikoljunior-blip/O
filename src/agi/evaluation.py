from __future__ import annotations

from dataclasses import asdict, dataclass
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

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.criterion not in CRITERION_KEYS:
            errors.append(f"unknown criterion: {self.criterion}")
        for name in ("task_id", "domain", "run_id", "artifact_ref", "evaluator"):
            if not getattr(self, name).strip():
                errors.append(f"{name} must be non-empty")
        if self.tier not in EVIDENCE_TIERS:
            errors.append(f"unknown tier: {self.tier}")
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
        )


@dataclass(frozen=True)
class EvaluationPolicy:
    """Conservative claim policy.

    Development benchmarks are useful for iteration but cannot alone establish AGI.
    A positive claim requires repeated, independent, production-tier evidence across
    every criterion, multiple tasks, multiple runs, and multiple domains.
    """

    min_successes_per_criterion: int = 3
    min_distinct_tasks_per_criterion: int = 3
    min_distinct_runs_per_criterion: int = 2
    min_distinct_domains_per_criterion: int = 2
    min_independent_successes_per_criterion: int = 1
    require_production_tier: bool = True
    disallow_unresolved_production_failures: bool = True

    def validate(self) -> None:
        fields = (
            self.min_successes_per_criterion,
            self.min_distinct_tasks_per_criterion,
            self.min_distinct_runs_per_criterion,
            self.min_distinct_domains_per_criterion,
            self.min_independent_successes_per_criterion,
        )
        if any(value < 1 for value in fields):
            raise ValueError("all minimum evidence thresholds must be at least 1")


def _legacy_records(assertions: Mapping[str, bool]) -> list[EvidenceRecord]:
    """Convert legacy booleans into explicitly weak self-report evidence.

    This preserves API compatibility while preventing a set of booleans from being
    mistaken for externally verified AGI evidence.
    """

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
) -> dict[str, Any]:
    relevant = [record for record in records if record.criterion == key]
    valid = [record for record in relevant if not record.validate()]
    successes = [record for record in valid if record.success]
    claim_successes = [
        record
        for record in successes
        if not policy.require_production_tier or record.tier == "production"
    ]
    production_failures = [
        record for record in valid if not record.success and record.tier == "production"
    ]
    independent = [record for record in claim_successes if record.independent]
    tasks = {record.task_id for record in claim_successes}
    runs = {record.run_id for record in claim_successes}
    domains = {record.domain for record in claim_successes}

    checks = {
        "success_count": len(claim_successes) >= policy.min_successes_per_criterion,
        "distinct_tasks": len(tasks) >= policy.min_distinct_tasks_per_criterion,
        "distinct_runs": len(runs) >= policy.min_distinct_runs_per_criterion,
        "distinct_domains": len(domains) >= policy.min_distinct_domains_per_criterion,
        "independent_successes": len(independent)
        >= policy.min_independent_successes_per_criterion,
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
            "independent_successes": len(independent),
            "production_failures": len(production_failures),
            "distinct_tasks": len(tasks),
            "distinct_runs": len(runs),
            "distinct_domains": len(domains),
        },
        "record_refs": [
            {"run_id": record.run_id, "task_id": record.task_id, "artifact_ref": record.artifact_ref}
            for record in relevant
        ],
    }


def evaluate_evidence(
    evidence: Mapping[str, bool] | Iterable[EvidenceRecord | Mapping[str, Any]],
    policy: EvaluationPolicy | None = None,
) -> dict[str, Any]:
    """Evaluate claim-grade evidence without weakening missing criteria.

    The function deliberately separates *development progress* from *AGI claim
    support*. A development suite can pass while the final claim remains false.
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
        criterion.key: _criterion_result(criterion.key, records, selected_policy)
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
        "claim": (
            "AGI claim is supported by the supplied evidence under this policy."
            if supported
            else "AGI claim is not supported; continue capability work and evidence collection."
        ),
    }
