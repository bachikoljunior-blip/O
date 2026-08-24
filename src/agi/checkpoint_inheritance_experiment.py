from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from statistics import median
from typing import Any, Mapping

from continual.behavioral_outcome import (
    behavioral_child_binding,
    prepare_behavioral_outcome,
)
from continual.work_session import verified_work_invocation


ARMS = {"single_lineage": 1, "sibling_checkpoint": 3}
VERDICTS = {"INSUFFICIENT_EVIDENCE", "ADOPT_SCOPED_CANDIDATE", "REJECT_MECHANISM"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_INVOCATION_ID = re.compile(r"^invoke-[0-9a-f]{24}$")
_ATTEMPT_FIELDS = {
    "candidate_id",
    "score",
    "model_invocations",
    "wall_time_seconds",
    "retained",
    "capability_retained",
    "replay_digest",
    "replay_verified",
    "protected_regressions_passed",
    "external_effects",
}
_NATIVE_FIELDS = {
    "invocation_id",
    "run_id",
    "request_digest",
    "response_digest",
    "executor_binding",
    "model_identity",
    "model_verified",
}
_READINESS_TASK_IDS = {"research-unit-a", "research-unit-b", "research-unit-c"}
_READINESS_CLAIM_BOUNDARY = {
    "agi_claim_supported": False,
    "readiness_is_behavioral_evidence": False,
    "scientist_agent_family_inference_supported": False,
    "untested_mechanism_inference_supported": False,
    "adaptation_loss_inference_supported": False,
}


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def checkpoint_protocol_digest(value: Mapping[str, Any]) -> str:
    """Return the frozen protocol digest, excluding observations and decision."""

    frozen = {
        field: value.get(field)
        for field in (
            "schema_version",
            "mechanism",
            "frozen_before_measurement",
            "matched_tasks",
            "arms",
            "scorer",
            "safety",
            "claim_boundary",
        )
    }
    encoded = json.dumps(
        frozen,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def checkpoint_measurement_readiness_digest(value: Mapping[str, Any]) -> str:
    """Return the immutable identity of a measurement-readiness contract."""

    frozen = {
        field: value.get(field)
        for field in (
            "schema_version",
            "plan_id",
            "mechanism",
            "base_experiment",
            "frozen_before_preparation",
            "executor_model_policy",
            "budget",
            "tasks",
            "arms",
            "scorer_binding",
            "safety",
            "claim_boundary",
            "negative_evidence_scope",
        )
    }
    return _canonical_digest(frozen)


def _contains_key(value: Any, forbidden: set[str]) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key) in forbidden or _contains_key(child, forbidden)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_key(child, forbidden) for child in value)
    return False


def validate_checkpoint_measurement_readiness(
    value: Mapping[str, Any],
    experiment: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the versioned, no-observation checkpoint measurement plan."""

    base = validate_checkpoint_experiment(experiment)
    if base["status"] != "HARNESS_READY" or base["observations"] != []:
        raise ValueError("measurement readiness requires the unmeasured HARNESS_READY baseline")
    if base["decision"].get("implementation_authorized") is not False:
        raise ValueError("baseline implementation must remain unauthorized")
    if value.get("schema_version") != 1:
        raise ValueError("readiness schema_version must be 1")
    if value.get("status") != "READY_NO_OBSERVATIONS":
        raise ValueError("readiness status must be READY_NO_OBSERVATIONS")
    if value.get("plan_id") != "bounded-stage-checkpoint-measurement-v2":
        raise ValueError("unexpected readiness plan_id")
    if value.get("mechanism") != base["mechanism"]:
        raise ValueError("readiness mechanism must match the baseline")
    if value.get("frozen_before_preparation") is not True:
        raise ValueError("readiness must be frozen before preparation")

    base_binding = value.get("base_experiment")
    if not isinstance(base_binding, Mapping) or set(base_binding) != {
        "path",
        "protocol_digest",
    }:
        raise ValueError("base_experiment binding is malformed")
    if base_binding.get("path") != "agi/CHECKPOINT_INHERITANCE_EXPERIMENT.json":
        raise ValueError("unexpected base experiment path")
    if base_binding.get("protocol_digest") != checkpoint_protocol_digest(base):
        raise ValueError("base experiment protocol digest mismatch")

    if value.get("executor_model_policy") != {
        "same_executor_binding_across_all_cells": True,
        "same_model_identity_across_all_cells": True,
        "model_identity_may_remain_platform_unverified": True,
    }:
        raise ValueError("executor/model policy must remain exact and fail-closed")
    budget = value.get("budget")
    if not isinstance(budget, Mapping) or budget != {
        "max_model_invocations_per_attempt": 1,
        "max_response_bytes": 4096,
    }:
        raise ValueError("readiness budget is invalid")

    tasks = value.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 3:
        raise ValueError("readiness requires exactly three tasks")
    seen_tasks: set[str] = set()
    for index, item in enumerate(tasks):
        if not isinstance(item, Mapping) or set(item) != {
            "task_id",
            "public_task",
            "rubric_binding",
        }:
            raise ValueError("readiness task schema is invalid")
        task_id = _nonempty(item.get("task_id"), f"tasks[{index}].task_id")
        if task_id not in _READINESS_TASK_IDS or task_id in seen_tasks:
            raise ValueError("readiness task ids must exactly match the baseline")
        seen_tasks.add(task_id)
        public = item.get("public_task")
        if not isinstance(public, Mapping) or set(public) != {
            "task_id",
            "instruction",
            "input",
            "answer_format",
            "response_pointer",
        }:
            raise ValueError("public task schema is invalid")
        if public.get("task_id") != task_id:
            raise ValueError("public task identity mismatch")
        _nonempty(public.get("instruction"), f"tasks[{index}].instruction")
        if public.get("answer_format") != "canonical_json":
            raise ValueError("public task answer format must be canonical_json")
        if public.get("response_pointer") != ["result", "behavioral_answer"]:
            raise ValueError("public task response pointer is invalid")
        if _contains_key(public, {"expected_answer", "labels", "answer_key"}):
            raise ValueError("judgment-only values cannot appear in public tasks")
        rubric = item.get("rubric_binding")
        if not isinstance(rubric, Mapping) or set(rubric) != {
            "judge_kind",
            "judge_version",
            "rubric_digest",
            "success_threshold",
            "supplied_at_preparation",
        }:
            raise ValueError("rubric binding schema is invalid")
        if rubric.get("judge_kind") != "exact_canonical_json":
            raise ValueError("readiness requires the exact canonical judge")
        if rubric.get("judge_version") != "exact-canonical-json-v1":
            raise ValueError("readiness judge version is invalid")
        if rubric.get("success_threshold") != 1.0:
            raise ValueError("readiness success threshold must be 1.0")
        if rubric.get("supplied_at_preparation") != "judgment_only_out_of_child_payload":
            raise ValueError("private rubric must be supplied only at preparation")
        if not _SHA256.fullmatch(str(rubric.get("rubric_digest", ""))):
            raise ValueError("rubric binding requires a lowercase SHA-256")
    if seen_tasks != _READINESS_TASK_IDS:
        raise ValueError("readiness task ids must exactly match the baseline")

    arms = value.get("arms")
    if not isinstance(arms, Mapping) or set(arms) != set(ARMS):
        raise ValueError("readiness arms must exactly match the baseline")
    for arm, width in ARMS.items():
        binding = arms.get(arm)
        base_arm = base["arms"][arm]
        if not isinstance(binding, Mapping) or binding != {
            "attempt_count": width,
            "checkpoint_inheritance": base_arm["checkpoint_inheritance"],
            "main_write_allowed": False,
        }:
            raise ValueError(f"readiness arm binding is invalid for {arm}")

    if value.get("scorer_binding") != {
        "implementation_ref": "src/continual/behavioral_outcome.py",
        "prepare_function": "prepare_behavioral_outcome",
        "record_function": "record_behavioral_outcome_from_work_invocation",
        "judge_kind": "exact_canonical_json",
        "judge_version": "exact-canonical-json-v1",
        "expected_values_in_child_payload": False,
    }:
        raise ValueError("scorer binding is invalid")
    safety = value.get("safety")
    if not isinstance(safety, Mapping) or safety != {
        "sandbox_required": True,
        "main_writers_per_arm": 0,
        "non_idempotent_external_effects_allowed": False,
        "protected_regressions_must_pass": True,
        "exact_replay_required": True,
        "synthetic_preparation_is_observation": False,
    }:
        raise ValueError("readiness safety boundary must remain fail-closed")
    if value.get("observations") != []:
        raise ValueError("measurement readiness cannot contain observations")
    if value.get("implementation_authorized") is not False:
        raise ValueError("measurement readiness cannot authorize implementation")
    if value.get("claim_boundary") != _READINESS_CLAIM_BOUNDARY:
        raise ValueError("readiness claim boundary must remain exact")
    scope = value.get("negative_evidence_scope")
    if not isinstance(scope, Mapping) or scope != {
        "tested_candidate": "bounded_stage_checkpoint_inheritance",
        "tested_configuration": "measurement-readiness-v1 over the frozen width-1 versus width-3 O harness",
        "tested_conditions": "static preparation and validation only; no behavioral attempt executed",
        "evidence_against_original_method": False,
        "evidence_against_scientist_agent_family": False,
        "evidence_against_untested_mechanisms": False,
        "adaptation_or_ablation_loss_established": False,
    }:
        raise ValueError("negative evidence scope must remain exact")
    return deepcopy(dict(value))


def prepare_checkpoint_measurement_requests(
    root: Path,
    *,
    run_id: str,
    state: Mapping[str, Any],
    experiment: Mapping[str, Any],
    readiness: Mapping[str, Any],
    private_rubrics: Mapping[str, Mapping[str, Any]],
    executor_binding: str,
    model_identity: str,
    now: str | None = None,
) -> dict[str, Any]:
    """Prepare all exact task-arm attempts without recording an observation."""

    plan = validate_checkpoint_measurement_readiness(readiness, experiment)
    protocol_digest = checkpoint_protocol_digest(experiment)
    readiness_digest = checkpoint_measurement_readiness_digest(plan)
    if set(private_rubrics) != _READINESS_TASK_IDS:
        raise ValueError("private rubrics must exactly match readiness tasks")
    tasks = {item["task_id"]: item for item in plan["tasks"]}
    cells: list[dict[str, Any]] = []
    for task_id in sorted(tasks):
        task_definition = tasks[task_id]
        rubric = deepcopy(dict(private_rubrics[task_id]))
        if _canonical_digest(rubric) != task_definition["rubric_binding"]["rubric_digest"]:
            raise ValueError(f"private rubric digest mismatch for {task_id}")
        for arm in sorted(ARMS):
            attempt_count = ARMS[arm]
            attempts: list[dict[str, Any]] = []
            for attempt_index in range(1, attempt_count + 1):
                candidate_id = f"{task_id}-{arm}-{attempt_index}"
                binding = {
                    "protocol_digest": protocol_digest,
                    "readiness_digest": readiness_digest,
                    "task_id": task_id,
                    "arm": arm,
                    "candidate_id": candidate_id,
                    "attempt_index": attempt_index,
                    "attempt_count": attempt_count,
                    "max_model_invocations": 1,
                }
                public_task = deepcopy(dict(task_definition["public_task"]))
                public_task["input"] = {
                    "checkpoint_measurement": binding,
                    "task_input": public_task["input"],
                }
                request = prepare_behavioral_outcome(
                    root,
                    run_id=run_id,
                    state=state,
                    task=public_task,
                    rubric=rubric,
                    executor_binding=executor_binding,
                    model_identity=model_identity,
                    now=now,
                    max_response_bytes=plan["budget"]["max_response_bytes"],
                )
                child_unit = {
                    "goal": public_task["instruction"],
                    "scope": "agi/bounded-stage-checkpoint-inheritance/behavioral-attempt-v1",
                    "checkpoint_measurement": binding,
                    "behavioral_outcome": behavioral_child_binding(request),
                    "task": public_task,
                    "production_effects_allowed": False,
                    "main_write_allowed": False,
                }
                if _contains_key(child_unit, {"expected_answer", "labels", "answer_key"}):
                    raise ValueError("judgment-only values leaked into child execution unit")
                attempts.append(
                    {
                        "candidate_id": candidate_id,
                        "outcome_id": request["outcome_id"],
                        "request_digest": request["request_digest"],
                        "child_execution_unit": child_unit,
                    }
                )
            cells.append({"task_id": task_id, "arm": arm, "attempts": attempts})
    return {
        "schema_version": 1,
        "record_type": "checkpoint_measurement_preparation",
        "run_id": run_id,
        "protocol_digest": protocol_digest,
        "readiness_digest": readiness_digest,
        "executor_binding": executor_binding,
        "model_identity": model_identity,
        "cells": cells,
        "prepared_attempt_count": sum(len(cell["attempts"]) for cell in cells),
        "observation_count": 0,
        "implementation_authorized": False,
        "claim_boundary": deepcopy(_READINESS_CLAIM_BOUNDARY),
    }


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
            if set(attempt) != _ATTEMPT_FIELDS | {"native_invocations"}:
                raise ValueError("attempt fields must exactly match the frozen evidence schema")
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
            native_invocations = attempt.get("native_invocations")
            if (
                not isinstance(native_invocations, list)
                or len(native_invocations) != invocations
            ):
                raise ValueError("model_invocations must equal exact native Work records")
            for native_index, native in enumerate(native_invocations):
                if not isinstance(native, Mapping) or set(native) != _NATIVE_FIELDS:
                    raise ValueError("every model invocation must bind one native Work record")
                if not _INVOCATION_ID.fullmatch(str(native.get("invocation_id", ""))):
                    raise ValueError("native invocation_id is invalid")
                for field in ("run_id", "executor_binding", "model_identity"):
                    _nonempty(
                        native.get(field),
                        f"{prefix}.native_invocations[{native_index}].{field}",
                    )
                for field in ("request_digest", "response_digest"):
                    if not _SHA256.fullmatch(str(native.get(field, ""))):
                        raise ValueError(f"native {field} must be lowercase SHA-256")
                if not isinstance(native.get("model_verified"), bool):
                    raise ValueError("native model_verified must be boolean")
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


def verify_checkpoint_experiment_provenance(
    root: Path,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind every measured attempt to a verified immutable native Work record."""

    validated = validate_checkpoint_experiment(value)
    protocol_digest = checkpoint_protocol_digest(validated)
    seen_invocations: set[str] = set()
    shared_executor_model: tuple[str, str, bool] | None = None
    for observation in validated["observations"]:
        task_id = observation["task_id"]
        arm = observation["arm"]
        for attempt in observation["attempts"]:
            native_invocations = attempt["native_invocations"]
            receipt_contract = {
                "protocol_digest": protocol_digest,
                "task_id": task_id,
                "arm": arm,
                "candidate_id": attempt["candidate_id"],
            }
            expected_receipt = {
                **receipt_contract,
                **{field: attempt[field] for field in sorted(_ATTEMPT_FIELDS)},
            }
            for index, native in enumerate(native_invocations, start=1):
                invocation_id = native["invocation_id"]
                if invocation_id in seen_invocations:
                    raise ValueError("native Work invocations cannot be reused across attempts")
                seen_invocations.add(invocation_id)
                record = verified_work_invocation(root, invocation_id)
                request = record["request"]
                response = record["response"]
                if request.get("component") != "execute":
                    raise ValueError("checkpoint attempts must be native Execute invocations")
                exact_native = {
                    "invocation_id": invocation_id,
                    "run_id": request.get("run_id"),
                    "request_digest": request.get("request_digest"),
                    "response_digest": response.get("response_digest"),
                    "executor_binding": request.get("executor_binding"),
                    "model_identity": request.get("model_identity"),
                    "model_verified": response.get("model_verified"),
                }
                if dict(native) != exact_native:
                    raise ValueError("native invocation binding does not match the journal")
                observed_executor_model = (
                    str(native["executor_binding"]),
                    str(native["model_identity"]),
                    bool(native["model_verified"]),
                )
                if shared_executor_model is None:
                    shared_executor_model = observed_executor_model
                elif observed_executor_model != shared_executor_model:
                    raise ValueError(
                        "all checkpoint attempts must use one executor/model binding"
                    )
                expected_step = {
                    **receipt_contract,
                    "invocation_index": index,
                    "invocation_count": len(native_invocations),
                }
                payload = request.get("payload")
                if not isinstance(payload, Mapping) or payload.get(
                    "checkpoint_experiment_attempt"
                ) != expected_step:
                    raise ValueError("native request was not frozen for this exact attempt step")
                output = record["output"]
                result = output.get("result") if isinstance(output, Mapping) else None
                if not isinstance(result, Mapping) or result.get(
                    "checkpoint_experiment_step"
                ) != expected_step:
                    raise ValueError("native response does not bind the recorded attempt step")
                is_final = index == len(native_invocations)
                if (
                    is_final
                    and result.get("checkpoint_experiment_attempt") != expected_receipt
                ):
                    raise ValueError("final native response does not bind the attempt receipt")
                if not is_final and "checkpoint_experiment_attempt" in result:
                    raise ValueError("only the final native response may bind the attempt receipt")
    return validated


def evaluate_checkpoint_experiment(
    value: Mapping[str, Any],
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    validated = validate_checkpoint_experiment(value)
    observations = validated["observations"]
    if len(observations) != len(validated["matched_tasks"]) * len(ARMS):
        return {
            "verdict": "INSUFFICIENT_EVIDENCE",
            "implementation_authorized": False,
            "reason": "Both arms must be recorded for all three matched tasks.",
        }
    if root is None:
        raise ValueError("complete measurements require native Work provenance verification")
    validated = verify_checkpoint_experiment_provenance(root, validated)

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
