from __future__ import annotations

from pathlib import Path
from textwrap import dedent


def write(path: str, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dedent(content).lstrip(), encoding="utf-8")


write(
    "src/agi/__init__.py",
    r'''
    """Evidence-driven AGI research and evaluation utilities.

    This package deliberately separates building increasingly general agents from
    claiming that AGI has been demonstrated.  A claim is only emitted when the
    configured evidence policy is satisfied by integrity-checked, objective
    records.
    """

    from .evaluation import CRITERIA, EvaluationPolicy, evaluate_evidence, evaluate_records
    from .evidence import EvidenceLedger, EvidenceRecord

    __all__ = [
        "CRITERIA",
        "EvaluationPolicy",
        "EvidenceLedger",
        "EvidenceRecord",
        "evaluate_evidence",
        "evaluate_records",
    ]
    __version__ = "0.2.0"
    ''',
)

write(
    "src/agi/evidence.py",
    r'''
    from __future__ import annotations

    import hashlib
    import json
    import os
    import re
    from dataclasses import asdict, dataclass, field
    from datetime import datetime, timezone
    from pathlib import Path
    from typing import Any, Iterable, Mapping


    CRITERION_KEYS = (
        "breadth",
        "transfer",
        "autonomy",
        "continual_learning",
        "self_improvement",
        "robustness",
    )

    OBJECTIVE_KINDS = frozenset(
        {
            "benchmark",
            "deterministic_test",
            "external_observation",
            "human_audit",
            "replay_verification",
        }
    )
    SELF_REPORT_VERIFIERS = frozenset(
        {
            "self",
            "model",
            "model-self-report",
            "agent-self-report",
            "unverified",
            "unknown",
        }
    )
    SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


    class EvidenceValidationError(ValueError):
        """Raised when an evidence record is malformed."""


    class LedgerIntegrityError(RuntimeError):
        """Raised when the append-only hash chain has been modified."""


    def utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()


    def canonical_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


    def sha256_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()


    def sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()


    def _text(value: Any, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise EvidenceValidationError(f"{field_name} must be a non-empty string")
        return value.strip()


    def _optional_float(value: Any, field_name: str) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EvidenceValidationError(f"{field_name} must be numeric or null")
        return float(value)


    @dataclass(frozen=True, slots=True)
    class EvidenceRecord:
        """One auditable observation relevant to an AGI criterion.

        `passed=True` is not enough by itself.  Objective evidence must also use an
        accepted verification kind, identify a non-self verifier, and point to an
        artifact with a SHA-256 digest.
        """

        evidence_id: str
        criterion: str
        task_id: str
        domain: str
        kind: str
        passed: bool
        verifier: str
        artifact: str
        artifact_sha256: str
        run_id: str
        created_at: str = field(default_factory=utc_now)
        held_out: bool = False
        steps: int = 0
        recovered_failures: int = 0
        perturbations: tuple[str, ...] = ()
        baseline_score: float | None = None
        candidate_score: float | None = None
        regression_score: float | None = None
        regression_floor: float | None = None
        metadata: dict[str, Any] = field(default_factory=dict)

        @classmethod
        def from_mapping(cls, value: Mapping[str, Any]) -> "EvidenceRecord":
            if not isinstance(value, Mapping):
                raise EvidenceValidationError("evidence record must be an object")
            criterion = _text(value.get("criterion"), "criterion")
            if criterion not in CRITERION_KEYS:
                raise EvidenceValidationError(f"unknown criterion: {criterion}")
            passed = value.get("passed")
            if not isinstance(passed, bool):
                raise EvidenceValidationError("passed must be a boolean")
            held_out = value.get("held_out", False)
            if not isinstance(held_out, bool):
                raise EvidenceValidationError("held_out must be a boolean")
            steps = value.get("steps", 0)
            recovered = value.get("recovered_failures", 0)
            if isinstance(steps, bool) or not isinstance(steps, int) or steps < 0:
                raise EvidenceValidationError("steps must be a non-negative integer")
            if isinstance(recovered, bool) or not isinstance(recovered, int) or recovered < 0:
                raise EvidenceValidationError(
                    "recovered_failures must be a non-negative integer"
                )
            raw_perturbations = value.get("perturbations", ())
            if not isinstance(raw_perturbations, (list, tuple)) or not all(
                isinstance(item, str) and item.strip() for item in raw_perturbations
            ):
                raise EvidenceValidationError(
                    "perturbations must be a list of non-empty strings"
                )
            metadata = value.get("metadata", {})
            if not isinstance(metadata, Mapping):
                raise EvidenceValidationError("metadata must be an object")
            return cls(
                evidence_id=_text(value.get("evidence_id"), "evidence_id"),
                criterion=criterion,
                task_id=_text(value.get("task_id"), "task_id"),
                domain=_text(value.get("domain"), "domain"),
                kind=_text(value.get("kind"), "kind"),
                passed=passed,
                verifier=_text(value.get("verifier"), "verifier"),
                artifact=_text(value.get("artifact"), "artifact"),
                artifact_sha256=_text(
                    value.get("artifact_sha256"), "artifact_sha256"
                ).lower(),
                run_id=_text(value.get("run_id"), "run_id"),
                created_at=_text(value.get("created_at", utc_now()), "created_at"),
                held_out=held_out,
                steps=steps,
                recovered_failures=recovered,
                perturbations=tuple(item.strip() for item in raw_perturbations),
                baseline_score=_optional_float(
                    value.get("baseline_score"), "baseline_score"
                ),
                candidate_score=_optional_float(
                    value.get("candidate_score"), "candidate_score"
                ),
                regression_score=_optional_float(
                    value.get("regression_score"), "regression_score"
                ),
                regression_floor=_optional_float(
                    value.get("regression_floor"), "regression_floor"
                ),
                metadata=dict(metadata),
            )

        def as_dict(self) -> dict[str, Any]:
            value = asdict(self)
            value["perturbations"] = list(self.perturbations)
            return value

        @property
        def is_objective(self) -> bool:
            verifier = self.verifier.strip().lower()
            return (
                self.kind in OBJECTIVE_KINDS
                and verifier not in SELF_REPORT_VERIFIERS
                and bool(self.artifact.strip())
                and bool(SHA256_RE.fullmatch(self.artifact_sha256))
            )

        @property
        def score_improved(self) -> bool:
            return (
                self.baseline_score is not None
                and self.candidate_score is not None
                and self.candidate_score > self.baseline_score
            )

        @property
        def regression_preserved(self) -> bool:
            return (
                self.regression_score is not None
                and self.regression_floor is not None
                and self.regression_score >= self.regression_floor
            )


    class EvidenceLedger:
        """Append-only JSONL ledger protected by a SHA-256 hash chain."""

        def __init__(self, path: Path):
            self.path = path

        def _read_entries(self) -> list[dict[str, Any]]:
            if not self.path.exists():
                return []
            entries: list[dict[str, Any]] = []
            previous = "0" * 64
            with self.path.open(encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise LedgerIntegrityError(
                            f"invalid JSON at ledger line {line_number}"
                        ) from exc
                    if not isinstance(entry, dict):
                        raise LedgerIntegrityError(
                            f"ledger line {line_number} is not an object"
                        )
                    claimed_hash = entry.get("entry_hash")
                    payload = {key: value for key, value in entry.items() if key != "entry_hash"}
                    if payload.get("previous_hash") != previous:
                        raise LedgerIntegrityError(
                            f"broken previous_hash at ledger line {line_number}"
                        )
                    actual_hash = sha256_bytes(canonical_json(payload).encode("utf-8"))
                    if claimed_hash != actual_hash:
                        raise LedgerIntegrityError(
                            f"entry hash mismatch at ledger line {line_number}"
                        )
                    if "record" not in payload:
                        raise LedgerIntegrityError(
                            f"missing record at ledger line {line_number}"
                        )
                    EvidenceRecord.from_mapping(payload["record"])
                    previous = actual_hash
                    entries.append(entry)
            return entries

        def load(self) -> list[EvidenceRecord]:
            return [
                EvidenceRecord.from_mapping(entry["record"])
                for entry in self._read_entries()
            ]

        def append(self, record: EvidenceRecord) -> str:
            entries = self._read_entries()
            previous = entries[-1]["entry_hash"] if entries else "0" * 64
            payload = {"record": record.as_dict(), "previous_hash": previous}
            entry_hash = sha256_bytes(canonical_json(payload).encode("utf-8"))
            entry = {**payload, "entry_hash": entry_hash}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(canonical_json(entry) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return entry_hash

        def extend(self, records: Iterable[EvidenceRecord]) -> list[str]:
            return [self.append(record) for record in records]

        def verify_artifacts(self, root: Path) -> list[str]:
            mismatches: list[str] = []
            for record in self.load():
                artifact = (root / record.artifact).resolve()
                try:
                    artifact.relative_to(root.resolve())
                except ValueError:
                    mismatches.append(record.evidence_id)
                    continue
                if not artifact.is_file() or sha256_file(artifact) != record.artifact_sha256:
                    mismatches.append(record.evidence_id)
            return mismatches
    ''',
)

write(
    "src/agi/evaluation.py",
    r'''
    from __future__ import annotations

    import hashlib
    from dataclasses import asdict, dataclass
    from typing import Any, Iterable, Mapping, Sequence

    from .evidence import CRITERION_KEYS, EvidenceRecord, EvidenceValidationError, canonical_json


    @dataclass(frozen=True, slots=True)
    class Criterion:
        key: str
        description: str


    CRITERIA = (
        Criterion(
            "breadth",
            "Verified success across materially different task domains without task-specific retraining.",
        ),
        Criterion(
            "transfer",
            "Verified transfer to held-out tasks and environments not used to construct the capability.",
        ),
        Criterion(
            "autonomy",
            "Long-horizon planning and execution with demonstrated recovery from real failures.",
        ),
        Criterion(
            "continual_learning",
            "Measured improvement from experience while preserving a protected regression set.",
        ),
        Criterion(
            "self_improvement",
            "A/B-tested improvement of the system's own procedures with rollback and regression evidence.",
        ),
        Criterion(
            "robustness",
            "Verified performance under distribution shift, perturbations, and tool failures.",
        ),
    )


    @dataclass(frozen=True, slots=True)
    class EvaluationPolicy:
        """Conservative minimum evidence gate, not a scientific definition of AGI."""

        breadth_tasks: int = 12
        breadth_domains: int = 6
        transfer_tasks: int = 6
        transfer_domains: int = 3
        autonomy_tasks: int = 3
        autonomy_min_steps: int = 10
        autonomy_recoveries: int = 1
        continual_cycles: int = 3
        self_improvement_trials: int = 3
        self_improvement_components: int = 2
        robustness_tasks: int = 6
        robustness_domains: int = 3
        robustness_perturbations: int = 4


    def _unique(records: Sequence[EvidenceRecord], attribute: str) -> set[str]:
        return {str(getattr(record, attribute)) for record in records}


    def _digest(records: Sequence[EvidenceRecord]) -> str:
        payload = [record.as_dict() for record in sorted(records, key=lambda item: item.evidence_id)]
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


    def evaluate_records(
        records: Iterable[EvidenceRecord],
        policy: EvaluationPolicy | None = None,
    ) -> dict[str, Any]:
        policy = policy or EvaluationPolicy()
        supplied = list(records)
        duplicate_ids = sorted(
            evidence_id
            for evidence_id in {record.evidence_id for record in supplied}
            if sum(record.evidence_id == evidence_id for record in supplied) > 1
        )
        objective = [record for record in supplied if record.is_objective]
        passing = [record for record in objective if record.passed]
        non_objective = [record.evidence_id for record in supplied if not record.is_objective]

        breadth = [record for record in passing if record.criterion == "breadth"]
        breadth_tasks = _unique(breadth, "task_id")
        breadth_domains = _unique(breadth, "domain")

        transfer = [
            record
            for record in passing
            if record.criterion == "transfer" and record.held_out
        ]
        transfer_tasks = _unique(transfer, "task_id")
        transfer_domains = _unique(transfer, "domain")

        autonomy = [
            record
            for record in passing
            if record.criterion == "autonomy"
            and record.steps >= policy.autonomy_min_steps
            and record.recovered_failures >= policy.autonomy_recoveries
            and bool(record.metadata.get("long_horizon"))
        ]
        autonomy_tasks = _unique(autonomy, "task_id")

        continual = [
            record
            for record in passing
            if record.criterion == "continual_learning"
            and record.score_improved
            and record.regression_preserved
            and bool(record.metadata.get("learning_cycle"))
        ]
        continual_cycles = _unique(continual, "run_id")

        self_improvement = [
            record
            for record in passing
            if record.criterion == "self_improvement"
            and record.score_improved
            and record.regression_preserved
            and bool(record.metadata.get("ab_tested"))
            and isinstance(record.metadata.get("self_modified_component"), str)
            and bool(record.metadata.get("self_modified_component"))
        ]
        self_trials = _unique(self_improvement, "task_id")
        self_components = {
            str(record.metadata["self_modified_component"])
            for record in self_improvement
        }

        robustness = [
            record
            for record in passing
            if record.criterion == "robustness" and record.perturbations
        ]
        robustness_tasks = _unique(robustness, "task_id")
        robustness_domains = _unique(robustness, "domain")
        perturbations = {
            perturbation
            for record in robustness
            for perturbation in record.perturbations
        }

        criteria = {
            "breadth": (
                len(breadth_tasks) >= policy.breadth_tasks
                and len(breadth_domains) >= policy.breadth_domains
            ),
            "transfer": (
                len(transfer_tasks) >= policy.transfer_tasks
                and len(transfer_domains) >= policy.transfer_domains
            ),
            "autonomy": len(autonomy_tasks) >= policy.autonomy_tasks,
            "continual_learning": len(continual_cycles) >= policy.continual_cycles,
            "self_improvement": (
                len(self_trials) >= policy.self_improvement_trials
                and len(self_components) >= policy.self_improvement_components
            ),
            "robustness": (
                len(robustness_tasks) >= policy.robustness_tasks
                and len(robustness_domains) >= policy.robustness_domains
                and len(perturbations) >= policy.robustness_perturbations
            ),
        }

        diagnostics = {
            "breadth": {
                "tasks": len(breadth_tasks),
                "domains": len(breadth_domains),
                "required_tasks": policy.breadth_tasks,
                "required_domains": policy.breadth_domains,
            },
            "transfer": {
                "held_out_tasks": len(transfer_tasks),
                "domains": len(transfer_domains),
                "required_tasks": policy.transfer_tasks,
                "required_domains": policy.transfer_domains,
            },
            "autonomy": {
                "qualified_tasks": len(autonomy_tasks),
                "required_tasks": policy.autonomy_tasks,
                "minimum_steps": policy.autonomy_min_steps,
                "minimum_recovered_failures": policy.autonomy_recoveries,
            },
            "continual_learning": {
                "qualified_cycles": len(continual_cycles),
                "required_cycles": policy.continual_cycles,
            },
            "self_improvement": {
                "qualified_trials": len(self_trials),
                "components": len(self_components),
                "required_trials": policy.self_improvement_trials,
                "required_components": policy.self_improvement_components,
            },
            "robustness": {
                "qualified_tasks": len(robustness_tasks),
                "domains": len(robustness_domains),
                "perturbations": len(perturbations),
                "required_tasks": policy.robustness_tasks,
                "required_domains": policy.robustness_domains,
                "required_perturbations": policy.robustness_perturbations,
            },
        }
        supported = all(criteria.values()) and not duplicate_ids
        return {
            "agi_claim_supported": supported,
            "criteria": criteria,
            "missing": [key for key in CRITERION_KEYS if not criteria[key]],
            "diagnostics": diagnostics,
            "policy": asdict(policy),
            "submitted_evidence": len(supplied),
            "objective_evidence": len(objective),
            "passing_objective_evidence": len(passing),
            "non_objective_evidence": sorted(non_objective),
            "duplicate_evidence_ids": duplicate_ids,
            "evidence_digest": _digest(supplied),
            "interpretation": (
                "Passing this gate supports a claim only under the declared policy; "
                "it is not universal scientific proof of AGI."
            ),
        }


    def evaluate_evidence(
        evidence: Mapping[str, Any],
        policy: EvaluationPolicy | None = None,
    ) -> dict[str, Any]:
        """Parse evidence mappings conservatively and evaluate objective records.

        Legacy boolean flags are intentionally treated as unverified self-report.
        """

        raw_records: list[Any] = []
        invalid_inputs: list[dict[str, str]] = []
        if not isinstance(evidence, Mapping):
            raise TypeError("evidence must be a mapping")
        if "records" in evidence:
            value = evidence["records"]
            if isinstance(value, list):
                raw_records.extend(value)
            else:
                invalid_inputs.append(
                    {"location": "records", "error": "records must be a list"}
                )
        else:
            for criterion, value in evidence.items():
                if isinstance(value, bool):
                    invalid_inputs.append(
                        {
                            "location": str(criterion),
                            "error": "boolean self-report is not objective evidence",
                        }
                    )
                elif isinstance(value, list):
                    raw_records.extend(value)
                elif isinstance(value, Mapping):
                    raw_records.append(value)
                else:
                    invalid_inputs.append(
                        {"location": str(criterion), "error": "unsupported evidence value"}
                    )

        parsed: list[EvidenceRecord] = []
        for index, raw in enumerate(raw_records):
            try:
                parsed.append(EvidenceRecord.from_mapping(raw))
            except EvidenceValidationError as exc:
                invalid_inputs.append(
                    {"location": f"records[{index}]", "error": str(exc)}
                )
        report = evaluate_records(parsed, policy=policy)
        report["invalid_inputs"] = invalid_inputs
        return report
    ''',
)

write(
    "src/agi/benchmark.py",
    r'''
    from __future__ import annotations

    import json
    from dataclasses import asdict, dataclass
    from pathlib import Path
    from typing import Any, Iterable, Mapping

    from .evidence import CRITERION_KEYS


    class BenchmarkManifestError(ValueError):
        """Raised when a benchmark manifest cannot support auditable evaluation."""


    @dataclass(frozen=True, slots=True)
    class BenchmarkTask:
        task_id: str
        criterion: str
        domain: str
        description: str
        verifier: str
        held_out: bool = False
        minimum_steps: int = 0
        perturbations: tuple[str, ...] = ()

        @classmethod
        def from_mapping(cls, value: Mapping[str, Any]) -> "BenchmarkTask":
            required = ("task_id", "criterion", "domain", "description", "verifier")
            for key in required:
                if not isinstance(value.get(key), str) or not value[key].strip():
                    raise BenchmarkManifestError(f"{key} must be a non-empty string")
            criterion = value["criterion"].strip()
            if criterion not in CRITERION_KEYS:
                raise BenchmarkManifestError(f"unknown criterion: {criterion}")
            held_out = value.get("held_out", False)
            if not isinstance(held_out, bool):
                raise BenchmarkManifestError("held_out must be a boolean")
            minimum_steps = value.get("minimum_steps", 0)
            if (
                isinstance(minimum_steps, bool)
                or not isinstance(minimum_steps, int)
                or minimum_steps < 0
            ):
                raise BenchmarkManifestError(
                    "minimum_steps must be a non-negative integer"
                )
            perturbations = value.get("perturbations", [])
            if not isinstance(perturbations, list) or not all(
                isinstance(item, str) and item.strip() for item in perturbations
            ):
                raise BenchmarkManifestError("perturbations must be a list of strings")
            return cls(
                task_id=value["task_id"].strip(),
                criterion=criterion,
                domain=value["domain"].strip(),
                description=value["description"].strip(),
                verifier=value["verifier"].strip(),
                held_out=held_out,
                minimum_steps=minimum_steps,
                perturbations=tuple(item.strip() for item in perturbations),
            )

        def as_dict(self) -> dict[str, Any]:
            value = asdict(self)
            value["perturbations"] = list(self.perturbations)
            return value


    class BenchmarkSuite:
        def __init__(self, tasks: Iterable[BenchmarkTask]):
            self.tasks = tuple(tasks)
            self.validate()

        def validate(self) -> None:
            if not self.tasks:
                raise BenchmarkManifestError("benchmark suite cannot be empty")
            ids = [task.task_id for task in self.tasks]
            if len(ids) != len(set(ids)):
                raise BenchmarkManifestError("benchmark task IDs must be unique")
            represented = {task.criterion for task in self.tasks}
            missing = sorted(set(CRITERION_KEYS) - represented)
            if missing:
                raise BenchmarkManifestError(
                    f"benchmark suite is missing criteria: {', '.join(missing)}"
                )
            transfer = [task for task in self.tasks if task.criterion == "transfer"]
            if not transfer or not all(task.held_out for task in transfer):
                raise BenchmarkManifestError("every transfer task must be held out")
            robustness = [task for task in self.tasks if task.criterion == "robustness"]
            if not robustness or not all(task.perturbations for task in robustness):
                raise BenchmarkManifestError(
                    "every robustness task must declare perturbations"
                )
            autonomy = [task for task in self.tasks if task.criterion == "autonomy"]
            if not autonomy or not all(task.minimum_steps >= 10 for task in autonomy):
                raise BenchmarkManifestError(
                    "autonomy tasks must require at least ten semantic steps"
                )

        def coverage(self) -> dict[str, Any]:
            return {
                criterion: {
                    "tasks": sum(task.criterion == criterion for task in self.tasks),
                    "domains": len(
                        {task.domain for task in self.tasks if task.criterion == criterion}
                    ),
                }
                for criterion in CRITERION_KEYS
            }

        def as_dict(self) -> dict[str, Any]:
            return {
                "schema_version": 1,
                "tasks": [task.as_dict() for task in self.tasks],
                "coverage": self.coverage(),
            }

        @classmethod
        def from_mapping(cls, value: Mapping[str, Any]) -> "BenchmarkSuite":
            tasks = value.get("tasks")
            if not isinstance(tasks, list):
                raise BenchmarkManifestError("manifest tasks must be a list")
            return cls(BenchmarkTask.from_mapping(task) for task in tasks)

        @classmethod
        def read(cls, path: Path) -> "BenchmarkSuite":
            with path.open(encoding="utf-8") as handle:
                value = json.load(handle)
            if not isinstance(value, Mapping):
                raise BenchmarkManifestError("manifest root must be an object")
            return cls.from_mapping(value)

        def write(self, path: Path) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self.as_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )


    def default_suite() -> BenchmarkSuite:
        domains = (
            "software-engineering",
            "quantitative-reasoning",
            "scientific-reasoning",
            "language",
            "planning",
            "tool-use",
        )
        tasks: list[BenchmarkTask] = []
        for index, domain in enumerate(domains, 1):
            tasks.append(
                BenchmarkTask(
                    task_id=f"breadth-{index:02d}",
                    criterion="breadth",
                    domain=domain,
                    description=(
                        "Complete a domain task from a frozen specification and verify "
                        "the artifact with a domain-appropriate external checker."
                    ),
                    verifier="independent-benchmark-harness",
                )
            )
            tasks.append(
                BenchmarkTask(
                    task_id=f"breadth-{index + 6:02d}",
                    criterion="breadth",
                    domain=domain,
                    description=(
                        "Complete a second materially different frozen task in this "
                        "domain without task-specific retraining."
                    ),
                    verifier="independent-benchmark-harness",
                )
            )
        for index, domain in enumerate(domains[:3], 1):
            for variant in range(2):
                tasks.append(
                    BenchmarkTask(
                        task_id=f"transfer-{index}-{variant}",
                        criterion="transfer",
                        domain=domain,
                        description=(
                            "Solve a sealed held-out task after learning only from a "
                            "different source task and environment."
                        ),
                        verifier="sealed-transfer-harness",
                        held_out=True,
                    )
                )
        for index, domain in enumerate(domains[:3], 1):
            tasks.append(
                BenchmarkTask(
                    task_id=f"autonomy-{index}",
                    criterion="autonomy",
                    domain=domain,
                    description=(
                        "Plan and execute a long-horizon task with injected recoverable "
                        "failures, persisted checkpoints, and external completion checks."
                    ),
                    verifier="long-horizon-replay-harness",
                    minimum_steps=10,
                    perturbations=("recoverable-tool-failure",),
                )
            )
        for index, domain in enumerate(domains[:3], 1):
            tasks.append(
                BenchmarkTask(
                    task_id=f"continual-{index}",
                    criterion="continual_learning",
                    domain=domain,
                    description=(
                        "Measure before/after learning performance and rerun a protected "
                        "regression set after the learning cycle."
                    ),
                    verifier="continual-learning-harness",
                )
            )
        for index, domain in enumerate(domains[:3], 1):
            tasks.append(
                BenchmarkTask(
                    task_id=f"self-improvement-{index}",
                    criterion="self_improvement",
                    domain=domain,
                    description=(
                        "A/B-test a system-proposed procedural change, verify improvement, "
                        "and retain rollback plus regression evidence."
                    ),
                    verifier="independent-ab-harness",
                )
            )
        perturbation_sets = (
            ("distribution-shift", "missing-tool"),
            ("adversarial-input", "transient-api-failure"),
            ("corrupted-observation", "latency-spike"),
            ("format-shift", "partial-state-loss"),
            ("noisy-feedback", "tool-schema-change"),
            ("resource-pressure", "dependency-failure"),
        )
        for index, (domain, perturbations) in enumerate(
            zip(domains, perturbation_sets, strict=True), 1
        ):
            tasks.append(
                BenchmarkTask(
                    task_id=f"robustness-{index}",
                    criterion="robustness",
                    domain=domain,
                    description=(
                        "Repeat a frozen capability task under declared perturbations and "
                        "verify both safety and task performance."
                    ),
                    verifier="perturbation-replay-harness",
                    perturbations=perturbations,
                )
            )
        return BenchmarkSuite(tasks)
    ''',
)
