from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class TaskMeasurement:
    task_id: str
    criterion: str
    domain: str
    repeat_index: int
    passed: bool
    score: float
    artifact_sha256: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TaskMeasurement":
        measurement = cls(
            task_id=str(value.get("task_id", "")),
            criterion=str(value.get("criterion", "")),
            domain=str(value.get("domain", "")),
            repeat_index=int(value.get("repeat_index", 0)),
            passed=bool(value.get("passed", False)),
            score=float(value.get("score", 0.0)),
            artifact_sha256=str(value.get("artifact_sha256", "")).lower(),
        )
        measurement.validate()
        return measurement

    def validate(self) -> None:
        if not self.task_id or not self.criterion or not self.domain:
            raise ValueError("task_id, criterion, and domain are required")
        if self.repeat_index < 0:
            raise ValueError("repeat_index must be non-negative")
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("score must be between 0 and 1")
        if len(self.artifact_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.artifact_sha256
        ):
            raise ValueError("artifact_sha256 must be lowercase SHA-256 hex")

    @property
    def key(self) -> tuple[str, int]:
        return self.task_id, self.repeat_index


@dataclass(frozen=True)
class RegressionPolicy:
    maximum_protected_score_drop: float = 0.0
    allowed_new_failures: int = 0
    minimum_mean_target_gain: float = 0.01
    minimum_target_repeats: int = 2
    require_same_artifact_provenance: bool = False

    def validate(self) -> None:
        if self.maximum_protected_score_drop < 0:
            raise ValueError("maximum_protected_score_drop must be non-negative")
        if self.allowed_new_failures < 0:
            raise ValueError("allowed_new_failures must be non-negative")
        if self.minimum_mean_target_gain < 0:
            raise ValueError("minimum_mean_target_gain must be non-negative")
        if self.minimum_target_repeats < 1:
            raise ValueError("minimum_target_repeats must be positive")


STRICT_REGRESSION_POLICY = RegressionPolicy()


def _normalise(
    values: Iterable[TaskMeasurement | Mapping[str, Any]],
) -> dict[tuple[str, int], TaskMeasurement]:
    measurements: dict[tuple[str, int], TaskMeasurement] = {}
    for raw in values:
        value = raw if isinstance(raw, TaskMeasurement) else TaskMeasurement.from_mapping(raw)
        if value.key in measurements:
            raise ValueError(
                f"duplicate task measurement: {value.task_id} repeat {value.repeat_index}"
            )
        measurements[value.key] = value
    return measurements


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compare_snapshots(
    baseline: Sequence[TaskMeasurement | Mapping[str, Any]],
    candidate: Sequence[TaskMeasurement | Mapping[str, Any]],
    *,
    target_task_ids: Sequence[str],
    policy: RegressionPolicy = STRICT_REGRESSION_POLICY,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Decide whether a learned Candidate may advance without capability loss.

    Every baseline task is protected. Target tasks must be present in both
    snapshots, improve by the configured mean amount, and include enough
    independent repeats. Missing measurements fail closed.
    """

    policy.validate()
    baseline_by_key = _normalise(baseline)
    candidate_by_key = _normalise(candidate)
    targets = {str(task_id) for task_id in target_task_ids if str(task_id)}
    if not targets:
        raise ValueError("at least one target_task_id is required")

    reasons: list[str] = []
    missing_candidate_keys = sorted(set(baseline_by_key) - set(candidate_by_key))
    if missing_candidate_keys:
        reasons.append(f"missing_protected_measurements {len(missing_candidate_keys)}")

    comparable_keys = sorted(set(baseline_by_key) & set(candidate_by_key))
    new_failures: list[dict[str, Any]] = []
    protected_drops: list[dict[str, Any]] = []
    changed_provenance: list[dict[str, Any]] = []
    target_deltas: list[float] = []
    target_repeat_keys: set[tuple[str, int]] = set()

    for key in comparable_keys:
        before = baseline_by_key[key]
        after = candidate_by_key[key]
        if (
            before.task_id != after.task_id
            or before.repeat_index != after.repeat_index
            or before.criterion != after.criterion
            or before.domain != after.domain
        ):
            reasons.append(f"incomparable_measurement {before.task_id}:{before.repeat_index}")
            continue

        delta = after.score - before.score
        if before.passed and not after.passed:
            new_failures.append(
                {
                    "task_id": before.task_id,
                    "repeat_index": before.repeat_index,
                    "baseline_score": before.score,
                    "candidate_score": after.score,
                }
            )
        if delta < -policy.maximum_protected_score_drop:
            protected_drops.append(
                {
                    "task_id": before.task_id,
                    "repeat_index": before.repeat_index,
                    "delta": round(delta, 12),
                }
            )
        if (
            policy.require_same_artifact_provenance
            and before.artifact_sha256 != after.artifact_sha256
        ):
            changed_provenance.append(
                {
                    "task_id": before.task_id,
                    "repeat_index": before.repeat_index,
                }
            )
        if before.task_id in targets:
            target_deltas.append(delta)
            target_repeat_keys.add(key)

    missing_target_ids = sorted(
        task_id
        for task_id in targets
        if not any(key[0] == task_id for key in target_repeat_keys)
    )
    if missing_target_ids:
        reasons.append("missing_target_tasks " + ",".join(missing_target_ids))

    repeats_by_target = {
        task_id: len({repeat for observed, repeat in target_repeat_keys if observed == task_id})
        for task_id in sorted(targets)
    }
    insufficient_repeats = {
        task_id: count
        for task_id, count in repeats_by_target.items()
        if count < policy.minimum_target_repeats
    }
    if insufficient_repeats:
        reasons.append(
            "insufficient_target_repeats "
            + ",".join(
                f"{task_id}:{count}/{policy.minimum_target_repeats}"
                for task_id, count in insufficient_repeats.items()
            )
        )

    mean_target_gain = (
        sum(target_deltas) / len(target_deltas) if target_deltas else 0.0
    )
    if mean_target_gain + 1e-12 < policy.minimum_mean_target_gain:
        reasons.append(
            "mean_target_gain "
            f"{mean_target_gain:.12f}/{policy.minimum_mean_target_gain:.12f}"
        )
    if len(new_failures) > policy.allowed_new_failures:
        reasons.append(
            f"new_failures {len(new_failures)}/{policy.allowed_new_failures}"
        )
    if protected_drops:
        reasons.append(f"protected_score_drops {len(protected_drops)}")
    if changed_provenance:
        reasons.append(f"changed_artifact_provenance {len(changed_provenance)}")

    baseline_payload = [asdict(value) for _, value in sorted(baseline_by_key.items())]
    candidate_payload = [asdict(value) for _, value in sorted(candidate_by_key.items())]
    evidence_payload = {
        "candidate_id": candidate_id,
        "baseline": baseline_payload,
        "candidate": candidate_payload,
        "target_task_ids": sorted(targets),
        "policy": asdict(policy),
    }
    return {
        "adopt_candidate": not reasons,
        "candidate_id": candidate_id,
        "reasons": reasons,
        "baseline_measurement_count": len(baseline_by_key),
        "candidate_measurement_count": len(candidate_by_key),
        "comparable_measurement_count": len(comparable_keys),
        "missing_protected_measurements": [
            {"task_id": task_id, "repeat_index": repeat}
            for task_id, repeat in missing_candidate_keys
        ],
        "new_failures": new_failures,
        "protected_score_drops": protected_drops,
        "changed_artifact_provenance": changed_provenance,
        "target_repeats": repeats_by_target,
        "mean_target_gain": round(mean_target_gain, 12),
        "regression_evidence_sha256": _digest(evidence_payload),
        "policy": asdict(policy),
    }


def _load_measurements(path: Path) -> list[Mapping[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, Mapping):
        value = value.get("measurements", value.get("results"))
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise ValueError(f"{path} must contain a measurement array")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agi.regression",
        description="Compare a Candidate against a protected capability baseline.",
    )
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--target-task", action="append", required=True)
    parser.add_argument("--candidate-id")
    parser.add_argument("--maximum-protected-score-drop", type=float, default=0.0)
    parser.add_argument("--allowed-new-failures", type=int, default=0)
    parser.add_argument("--minimum-mean-target-gain", type=float, default=0.01)
    parser.add_argument("--minimum-target-repeats", type=int, default=2)
    parser.add_argument("--output", type=Path)
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    policy = RegressionPolicy(
        maximum_protected_score_drop=args.maximum_protected_score_drop,
        allowed_new_failures=args.allowed_new_failures,
        minimum_mean_target_gain=args.minimum_mean_target_gain,
        minimum_target_repeats=args.minimum_target_repeats,
    )
    result = compare_snapshots(
        _load_measurements(args.baseline),
        _load_measurements(args.candidate),
        target_task_ids=args.target_task,
        policy=policy,
        candidate_id=args.candidate_id,
    )
    encoded = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if result["adopt_candidate"] else 2


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
