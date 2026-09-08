from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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
        score = value.get("score")
        if isinstance(score, bool):
            raise ValueError("score must be numeric")
        try:
            parsed_score = float(score)
        except (TypeError, ValueError) as exc:
            raise ValueError("score must be numeric") from exc
        repeat = value.get("repeat_index")
        if isinstance(repeat, bool):
            raise ValueError("repeat_index must be an integer")
        try:
            parsed_repeat = int(repeat)
        except (TypeError, ValueError) as exc:
            raise ValueError("repeat_index must be an integer") from exc
        if parsed_repeat != repeat:
            raise ValueError("repeat_index must be an integer")
        passed = value.get("passed")
        if not isinstance(passed, bool):
            raise ValueError("passed must be boolean")
        measurement = cls(
            task_id=str(value.get("task_id", "")),
            criterion=str(value.get("criterion", "")),
            domain=str(value.get("domain", "")),
            repeat_index=parsed_repeat,
            passed=passed,
            score=parsed_score,
            artifact_sha256=str(value.get("artifact_sha256", "")),
        )
        measurement.validate()
        return measurement

    def validate(self) -> None:
        if not self.task_id or not self.criterion or not self.domain:
            raise ValueError("task_id, criterion, and domain are required")
        if self.repeat_index < 0:
            raise ValueError("repeat_index must be non-negative")
        if not math.isfinite(self.score) or not 0.0 <= self.score <= 1.0:
            raise ValueError("score must be finite and between 0 and 1")
        if not _SHA256_RE.fullmatch(self.artifact_sha256):
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

    def validate(self) -> None:
        if not math.isfinite(self.maximum_protected_score_drop) or self.maximum_protected_score_drop < 0:
            raise ValueError("maximum_protected_score_drop must be finite and non-negative")
        if self.allowed_new_failures < 0:
            raise ValueError("allowed_new_failures must be non-negative")
        if not math.isfinite(self.minimum_mean_target_gain) or self.minimum_mean_target_gain < 0:
            raise ValueError("minimum_mean_target_gain must be finite and non-negative")
        if self.minimum_target_repeats < 1:
            raise ValueError("minimum_target_repeats must be positive")


STRICT_REGRESSION_POLICY = RegressionPolicy()


def _normalise(
    values: Iterable[TaskMeasurement | Mapping[str, Any]],
) -> dict[tuple[str, int], TaskMeasurement]:
    measurements: dict[tuple[str, int], TaskMeasurement] = {}
    for raw in values:
        value = raw if isinstance(raw, TaskMeasurement) else TaskMeasurement.from_mapping(raw)
        value.validate()
        if value.key in measurements:
            raise ValueError(
                f"duplicate task measurement: {value.task_id} repeat {value.repeat_index}"
            )
        measurements[value.key] = value
    if not measurements:
        raise ValueError("measurement set must not be empty")
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
    scope: str,
    policy: RegressionPolicy = STRICT_REGRESSION_POLICY,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Fail closed before activating a learned Candidate for one explicit scope.

    Every baseline measurement is protected. A candidate can become active only
    for the supplied scope when it preserves every protected baseline, creates no
    disallowed new failures, and shows repeated positive gain on every named
    target task. The complete comparison, including negative evidence, is kept in
    the returned decision so later successful runs cannot erase prior failures.
    """

    policy.validate()
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("scope must be a non-empty string")
    baseline_by_key = _normalise(baseline)
    candidate_by_key = _normalise(candidate)
    targets = {str(task_id) for task_id in target_task_ids if str(task_id)}
    if not targets:
        raise ValueError("at least one target_task_id is required")

    reasons: list[str] = []
    negative_evidence: list[dict[str, Any]] = []
    missing_candidate_keys = sorted(set(baseline_by_key) - set(candidate_by_key))
    if missing_candidate_keys:
        reasons.append(f"missing_protected_measurements {len(missing_candidate_keys)}")
        negative_evidence.extend(
            {
                "type": "missing_protected_measurement",
                "task_id": task_id,
                "repeat_index": repeat,
            }
            for task_id, repeat in missing_candidate_keys
        )

    comparable_keys = sorted(set(baseline_by_key) & set(candidate_by_key))
    new_failures: list[dict[str, Any]] = []
    protected_drops: list[dict[str, Any]] = []
    target_deltas: dict[str, list[float]] = {task_id: [] for task_id in sorted(targets)}

    for key in comparable_keys:
        before = baseline_by_key[key]
        after = candidate_by_key[key]
        if before.criterion != after.criterion or before.domain != after.domain:
            item = {
                "type": "incomparable_measurement",
                "task_id": before.task_id,
                "repeat_index": before.repeat_index,
                "baseline_criterion": before.criterion,
                "candidate_criterion": after.criterion,
                "baseline_domain": before.domain,
                "candidate_domain": after.domain,
            }
            negative_evidence.append(item)
            reasons.append(f"incomparable_measurement {before.task_id}:{before.repeat_index}")
            continue

        delta = after.score - before.score
        if before.passed and not after.passed:
            item = {
                "task_id": before.task_id,
                "repeat_index": before.repeat_index,
                "baseline_score": before.score,
                "candidate_score": after.score,
            }
            new_failures.append(item)
            negative_evidence.append({"type": "new_failure", **item})
        if delta < -policy.maximum_protected_score_drop - 1e-12:
            item = {
                "task_id": before.task_id,
                "repeat_index": before.repeat_index,
                "delta": round(delta, 12),
            }
            protected_drops.append(item)
            negative_evidence.append({"type": "protected_score_drop", **item})
        if before.task_id in targets:
            target_deltas[before.task_id].append(delta)

    target_repeats = {task_id: len(deltas) for task_id, deltas in target_deltas.items()}
    missing_targets = sorted(task_id for task_id, count in target_repeats.items() if count == 0)
    if missing_targets:
        reasons.append("missing_target_tasks " + ",".join(missing_targets))
        negative_evidence.extend(
            {"type": "missing_target_task", "task_id": task_id} for task_id in missing_targets
        )

    insufficient_repeats = {
        task_id: count
        for task_id, count in target_repeats.items()
        if count < policy.minimum_target_repeats
    }
    if insufficient_repeats:
        reasons.append(
            "insufficient_target_repeats "
            + ",".join(
                f"{task_id}:{count}/{policy.minimum_target_repeats}"
                for task_id, count in sorted(insufficient_repeats.items())
            )
        )
        negative_evidence.extend(
            {
                "type": "insufficient_target_repeats",
                "task_id": task_id,
                "observed": count,
                "required": policy.minimum_target_repeats,
            }
            for task_id, count in sorted(insufficient_repeats.items())
        )

    mean_target_gain_by_task = {
        task_id: (sum(deltas) / len(deltas) if deltas else 0.0)
        for task_id, deltas in target_deltas.items()
    }
    weak_targets = {
        task_id: gain
        for task_id, gain in mean_target_gain_by_task.items()
        if gain + 1e-12 < policy.minimum_mean_target_gain
    }
    if weak_targets:
        reasons.append(
            "insufficient_target_gain "
            + ",".join(
                f"{task_id}:{gain:.12f}/{policy.minimum_mean_target_gain:.12f}"
                for task_id, gain in sorted(weak_targets.items())
            )
        )
        negative_evidence.extend(
            {
                "type": "insufficient_target_gain",
                "task_id": task_id,
                "observed": round(gain, 12),
                "required": policy.minimum_mean_target_gain,
            }
            for task_id, gain in sorted(weak_targets.items())
        )

    if len(new_failures) > policy.allowed_new_failures:
        reasons.append(f"new_failures {len(new_failures)}/{policy.allowed_new_failures}")
    if protected_drops:
        reasons.append(f"protected_score_drops {len(protected_drops)}")

    adopted = not reasons
    baseline_payload = [asdict(value) for _, value in sorted(baseline_by_key.items())]
    candidate_payload = [asdict(value) for _, value in sorted(candidate_by_key.items())]
    evidence_payload = {
        "candidate_id": candidate_id,
        "scope": scope,
        "baseline": baseline_payload,
        "candidate": candidate_payload,
        "target_task_ids": sorted(targets),
        "policy": asdict(policy),
    }
    return {
        "adopt_candidate": adopted,
        "decision": "ACTIVE_FOR_SCOPE" if adopted else "REMAIN_CANDIDATE",
        "scope_state": "VERIFIED_FOR_SCOPE" if adopted else "REMAIN_CANDIDATE",
        "candidate_id": candidate_id,
        "scope": scope,
        "reasons": reasons,
        "negative_evidence": negative_evidence,
        "baseline_measurement_count": len(baseline_by_key),
        "candidate_measurement_count": len(candidate_by_key),
        "comparable_measurement_count": len(comparable_keys),
        "missing_protected_measurements": [
            {"task_id": task_id, "repeat_index": repeat}
            for task_id, repeat in missing_candidate_keys
        ],
        "new_failures": new_failures,
        "protected_score_drops": protected_drops,
        "target_repeats": target_repeats,
        "mean_target_gain_by_task": {
            task_id: round(gain, 12) for task_id, gain in mean_target_gain_by_task.items()
        },
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
    parser.add_argument("--scope", required=True)
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
        scope=args.scope,
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
