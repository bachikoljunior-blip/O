from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from statistics import median
from typing import Any, Mapping


ARMS = {"single_lineage": 1, "sibling_checkpoint": 3}
VERDICTS = {"INSUFFICIENT_EVIDENCE", "ADOPT_SCOPED_CANDIDATE", "REJECT_MECHANISM"}


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def validate_checkpoint_experiment(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a frozen, sandbox-only matched checkpoint-inheritance experiment."""

    if value.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    if value.get("status") not in {"HARNESS_READY", "MEASURED"}:
        raise ValueError("status must be HARNESS_READY or MEASURED")
    if value.get("mechanism") != "bounded_stage_checkpoint_inheritance":
        raise ValueError("unexpected mechanism")
    if value.get("frozen_before_measurement") is not True:
        raise ValueError("the protocol must be frozen before measurement")

    tasks = value.get("matched_tasks")
    if not isinstance(tasks, list) or len(tasks) != 3:
        raise ValueError("exactly three matched_tasks are required")
    task_ids: set[str] = set()
    for index, task in enumerate(tasks):
        if not isinstance(task, Mapping):
            raise ValueError(f"matched_tasks[{index}] must be an object")
        task_id = _nonempty(task.get("task_id"), f"matched_tasks[{index}].task_id")
        if task_id in task_ids:
            raise ValueError("matched task ids must be unique")
        task_ids.add(task_id)
        _nonempty(task.get("objective"), f"matched_tasks[{index}].objective")
        _nonempty(task.get("evaluator"), f"matched_tasks[{index}].evaluator")
        if task.get("production_effects_allowed") is not False:
            raise ValueError("matched tasks must forbid production effects")

    arms = value.get("arms")
    if not isinstance(arms, Mapping) or set(arms) != set(ARMS):
        raise ValueError("arms must contain the exact control and treatment")
    for arm, width in ARMS.items():
        definition = arms.get(arm)
        if not isinstance(definition, Mapping) or definition.get("width") != width:
            raise ValueError(f"{arm} must have width {width}")
        if definition.get("main_write_allowed") is not False:
            raise ValueError("experiment arms cannot write main")
    if arms["single_lineage"].get("checkpoint_inheritance") is not False:
        raise ValueError("the control cannot use checkpoint inheritance")
    if arms["sibling_checkpoint"].get("checkpoint_inheritance") is not True:
        raise ValueError("the treatment must use checkpoint inheritance")

    scorer = value.get("scorer")
    if not isinstance(scorer, Mapping) or scorer.get("deterministic") is not True:
        raise ValueError("a deterministic scorer is required")
    _nonempty(scorer.get("name"), "scorer.name")
    for field in ("primary_metric", "secondary_metric", "advantage_rule"):
        _nonempty(scorer.get(field), f"scorer.{field}")
    overhead = scorer.get("selection_overhead_seconds")
    if not isinstance(overhead, (int, float)) or isinstance(overhead, bool) or overhead < 0:
        raise ValueError("selection overhead must be non-negative")

    safety = value.get("safety")
    if not isinstance(safety, Mapping):
        raise ValueError("safety must be an object")
    required_safety = {
        "sandbox_required": True,
        "main_writers_per_arm": 0,
        "non_idempotent_external_effects_allowed": False,
        "protected_regressions_must_pass": True,
        "exact_replay_required": True,
    }
    if any(safety.get(field) != expected for field, expected in required_safety.items()):
        raise ValueError("safety boundary must remain fail-closed")

    observations = value.get("observations")
    if not isinstance(observations, list):
        raise ValueError("observations must be an array")
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for index, observation in enumerate(observations):
        if not isinstance(observation, Mapping):
            raise ValueError(f"observations[{index}] must be an object")
        task_id = _nonempty(observation.get("task_id"), f"observations[{index}].task_id")
        arm = observation.get("arm")
        if task_id not in task_ids or arm not in ARMS:
            raise ValueError("observation references an unknown task or arm")
        if observation.get("measurement_source") != "recorded_native_run":
            raise ValueError("only recorded native run observations are admissible")
        attempts = observation.get("attempts")
        if not isinstance(attempts, list) or len(attempts) != ARMS[arm]:
            raise ValueError(f"{arm} observations must contain exactly {ARMS[arm]} attempts")
        for attempt_index, attempt in enumerate(attempts):
            if not isinstance(attempt, Mapping):
                raise ValueError("attempts must be objects")
            prefix = f"observations[{index}].attempts[{attempt_index}]"
            _nonempty(attempt.get("candidate_id"), f"{prefix}.candidate_id")
            _nonempty(attempt.get("replay_digest"), f"{prefix}.replay_digest")
            score = attempt.get("score")
            if not isinstance(score, (int, float)) or isinstance(score, bool):
                raise ValueError("attempt scores must be numeric")
            invocations = attempt.get("model_invocations")
            if not isinstance(invocations, int) or isinstance(invocations, bool) or invocations < 1:
                raise ValueError("model_invocations must be a positive integer")
            wall_time = attempt.get("wall_time_seconds")
            if not isinstance(wall_time, (int, float)) or isinstance(wall_time, bool) or wall_time < 0:
                raise ValueError("wall_time_seconds must be non-negative")
            if attempt.get("external_effects") != []:
                raise ValueError("attempts cannot contain external effects")
            if attempt.get("replay_verified") is not True:
                raise ValueError("every attempt must pass exact replay")
            if attempt.get("protected_regressions_passed") is not True:
                raise ValueError("every attempt must pass protected regressions")
        if sum(attempt.get("retained") is True for attempt in attempts) != 1:
            raise ValueError("each observation must retain exactly one attempt")
        groups[(task_id, arm)].append(observation)
    if any(len(group) != 1 for group in groups.values()):
        raise ValueError("task-arm observations must be unique")

    decision = value.get("decision")
    if not isinstance(decision, Mapping) or decision.get("verdict") not in VERDICTS:
        raise ValueError("decision.verdict is invalid")
    _nonempty(decision.get("reason"), "decision.reason")
    complete = len(groups) == len(task_ids) * len(ARMS)
    if decision.get("verdict") != "INSUFFICIENT_EVIDENCE" and not complete:
        raise ValueError("a measured verdict requires every task-arm observation")
    if decision.get("implementation_authorized") is not False:
        raise ValueError("the experiment cannot directly authorize implementation")

    boundary = value.get("claim_boundary")
    if not isinstance(boundary, Mapping) or any(
        boundary.get(field) is not False
        for field in (
            "agi_claim_supported",
            "harness_is_capability_evidence",
            "synthetic_test_data_is_experiment_evidence",
        )
    ):
        raise ValueError("claim boundary must remain fail-closed")
    return deepcopy(dict(value))


def evaluate_checkpoint_experiment(value: Mapping[str, Any]) -> dict[str, Any]:
    validated = validate_checkpoint_experiment(value)
    observations = validated["observations"]
    if len(observations) != len(validated["matched_tasks"]) * len(ARMS):
        return {
            "verdict": "INSUFFICIENT_EVIDENCE",
            "implementation_authorized": False,
            "reason": "Both arms must be recorded for all three matched tasks.",
        }

    metrics: dict[str, dict[str, list[float]]] = {
        arm: {"model_invocations": [], "wall_time_seconds": []} for arm in ARMS
    }
    overhead = float(validated["scorer"]["selection_overhead_seconds"])
    for observation in observations:
        arm = observation["arm"]
        attempts = observation["attempts"]
        retained = next(attempt for attempt in attempts if attempt["retained"] is True)
        if retained.get("capability_retained") is not True:
            return {
                "verdict": "REJECT_MECHANISM",
                "implementation_authorized": False,
                "reason": "At least one arm failed to retain the bounded capability.",
            }
        metrics[arm]["model_invocations"].append(float(sum(a["model_invocations"] for a in attempts)))
        wall_time = sum(a["wall_time_seconds"] for a in attempts) if arm == "single_lineage" else max(
            a["wall_time_seconds"] for a in attempts
        ) + overhead
        metrics[arm]["wall_time_seconds"].append(float(wall_time))

    medians = {
        arm: {metric: median(values) for metric, values in arm_metrics.items()}
        for arm, arm_metrics in metrics.items()
    }
    control = medians["single_lineage"]
    treatment = medians["sibling_checkpoint"]
    improves = any(treatment[metric] < control[metric] for metric in control)
    worsens = any(treatment[metric] > control[metric] for metric in control)
    verdict = "ADOPT_SCOPED_CANDIDATE" if improves and not worsens else "REJECT_MECHANISM"
    return {
        "verdict": verdict,
        "implementation_authorized": False,
        "medians": medians,
        "reason": (
            "The treatment improved at least one frozen median without worsening the other."
            if verdict == "ADOPT_SCOPED_CANDIDATE"
            else "The treatment did not meet the frozen non-worsening advantage rule."
        ),
    }


def load_checkpoint_experiment(path: Path) -> dict[str, Any]:
    return validate_checkpoint_experiment(json.loads(path.read_text(encoding="utf-8")))
