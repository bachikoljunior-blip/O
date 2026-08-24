from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence


PROTOCOL_STATUS = "PRECOMMITTED"
ROUTING_ARM_IDS = (
    "current_context_kernel",
    "recursive_skill_in_skill",
    "eager_all_context",
)
ROUTING_TASK_IDS = (
    "precommit-routing-evaluation",
    "safe-effect-dispatch",
    "incident-recovery",
    "router-regression-suite",
    "truthful-evidence-claim",
    "shared-evidence-synthesis",
)
RUBRIC_DIMENSIONS = (
    "hypothesis_generation",
    "experiment_execution",
    "mechanical_evaluation",
    "result_inheritance",
    "cost_and_latency",
    "failure_containment",
)
ROUTING_METRICS = (
    {
        "metric_id": "required_context_recall",
        "direction": "higher_is_better",
        "unit": "ratio",
        "required": True,
        "nullable": False,
    },
    {
        "metric_id": "unnecessary_context_chars",
        "direction": "lower_is_better",
        "unit": "characters",
        "required": True,
        "nullable": False,
    },
    {
        "metric_id": "unnecessary_context_ratio",
        "direction": "lower_is_better",
        "unit": "ratio",
        "required": True,
        "nullable": False,
    },
    {
        "metric_id": "final_task_outcome",
        "direction": "pass_is_better",
        "unit": "boolean",
        "required": True,
        "nullable": False,
    },
    {
        "metric_id": "elapsed_ms",
        "direction": "lower_is_better",
        "unit": "milliseconds",
        "required": True,
        "nullable": False,
    },
    {
        "metric_id": "materialized_context_chars",
        "direction": "lower_is_better",
        "unit": "characters",
        "required": True,
        "nullable": False,
    },
    {
        "metric_id": "model_calls",
        "direction": "lower_is_better",
        "unit": "count",
        "required": True,
        "nullable": False,
    },
    {
        "metric_id": "tool_calls",
        "direction": "lower_is_better",
        "unit": "count",
        "required": True,
        "nullable": False,
    },
    {
        "metric_id": "observable_cost_usd",
        "direction": "lower_is_better",
        "unit": "usd",
        "required": True,
        "nullable": True,
    },
)

_PROTOCOL_FIELDS = {
    "schema_version",
    "protocol_digest",
    "status",
    "mechanism",
    "frozen_before_observation",
    "source_artifacts",
    "user_directives",
    "routing_comparison",
    "scientist_positive_control",
    "observations",
    "decision",
    "next_measurement_boundary",
    "user_level_verdict",
    "claim_boundary",
}
_SOURCE_FIELDS = {"source_id", "path", "sha256"}
_ARM_FIELDS = {
    "arm_id",
    "role",
    "implementation_ref",
    "task_ids",
    "budget",
    "model_executor_class",
    "tool_permissions",
}
_ROUTING_RESULT_FIELDS = {"arm_id", "task_results"}
_TASK_RESULT_FIELDS = {
    "task_id",
    "order_index",
    "required_context_recall",
    "unnecessary_context_chars",
    "unnecessary_context_ratio",
    "final_task_outcome",
    "elapsed_ms",
    "materialized_context_chars",
    "model_calls",
    "tool_calls",
    "observable_cost_usd",
}
_ROUTING_RECEIPT_FIELDS = {
    "schema_version",
    "protocol_digest",
    "status",
    "execution_conditions",
    "arm_results",
    "labels_frozen_before_execution",
    "contamination_detected",
    "sealed_before_classification",
    "receipt_digest",
}
_SCIENTIST_RECEIPT_FIELDS = {
    "schema_version",
    "protocol_digest",
    "comparison_kind",
    "positive_control",
    "evaluated_arm",
    "sealed_before_classification",
    "contamination_detected",
    "receipt_digest",
}
_EQUIVALENT_POSITIVE_CONTROL_FIELDS = {
    "schema_version",
    "evidence_id",
    "system",
    "source_artifact_id",
    "source_locator",
    "observed_at",
    "configuration_id",
    "status",
    "fidelity",
    "rubric_dimensions",
    "task_set_digest",
    "budget_digest",
    "model_executor_class",
    "result_digest",
    "equivalence_basis",
    "evidence_digest",
}
_SCIENTIST_RESULT_FIELDS = {
    "status",
    "fidelity",
    "rubric_dimensions",
    "task_set_digest",
    "budget_digest",
    "model_executor_class",
    "result_digest",
}
_EVALUATED_RESULT_FIELDS = _SCIENTIST_RESULT_FIELDS | {
    "matched_to_positive_control",
    "target_domain",
}
_HEX = set("0123456789abcdef")
_SECRET_FIELD_MARKERS = {
    "secret",
    "token",
    "password",
    "private_key",
    "credential",
}


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _HEX for character in value)
    ):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _finite_number(value: Any, label: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"{label} must be finite and at least {minimum}")
    return result


def _reject_secret_fields(value: Any, label: str = "protocol") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            if any(marker in normalized for marker in _SECRET_FIELD_MARKERS):
                raise ValueError(f"{label} contains a secret-bearing field: {key}")
            _reject_secret_fields(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_fields(item, f"{label}[{index}]")


def context_method_comparison_protocol_digest(value: Mapping[str, Any]) -> str:
    """Bind every pre-observation field while leaving outcomes in receipts."""

    frozen = {
        key: value.get(key)
        for key in (
            "schema_version",
            "status",
            "mechanism",
            "frozen_before_observation",
            "source_artifacts",
            "user_directives",
            "routing_comparison",
            "scientist_positive_control",
            "next_measurement_boundary",
            "user_level_verdict",
            "claim_boundary",
        )
    }
    return _canonical_digest(frozen)


def _validate_source_artifacts(
    value: Any,
    *,
    source_root: Path | None,
) -> dict[str, Mapping[str, Any]]:
    expected = {
        "context_kernel_architecture",
        "recursive_routing_experiment",
        "recursive_routing_rendezvous",
        "scientist_agent_baseline",
        "user_input_inbox_revision_19",
    }
    if not isinstance(value, list) or len(value) != len(expected):
        raise ValueError("source_artifacts must contain the five frozen sources")
    sources: dict[str, Mapping[str, Any]] = {}
    for index, source in enumerate(value):
        label = f"source_artifacts[{index}]"
        if not isinstance(source, Mapping) or set(source) != _SOURCE_FIELDS:
            raise ValueError(f"{label} has an unexpected schema")
        source_id = _nonempty(source.get("source_id"), f"{label}.source_id")
        if source_id in sources:
            raise ValueError("source artifact IDs must be unique")
        path = _nonempty(source.get("path"), f"{label}.path")
        digest = _sha256(source.get("sha256"), f"{label}.sha256")
        if path.startswith("/") or ".." in Path(path).parts:
            raise ValueError("source artifact paths must be repository-relative")
        if source_root is not None:
            candidate = source_root / path
            if not candidate.is_file():
                raise ValueError(f"source artifact is missing: {path}")
            actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
            if actual != digest:
                raise ValueError(f"source artifact digest changed: {path}")
        sources[source_id] = source
    if set(sources) != expected:
        raise ValueError("source_artifacts do not name the exact frozen sources")
    return sources


def _validate_routing_comparison(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("routing_comparison must be an object")
    expected_fields = {
        "task_ids",
        "shared_budget",
        "model_executor_class",
        "tool_permissions",
        "arms",
        "execution_order",
        "metrics",
        "decision_rule",
        "scorer",
    }
    if set(value) != expected_fields:
        raise ValueError("routing_comparison has an unexpected schema")
    task_ids = value.get("task_ids")
    if task_ids != list(ROUTING_TASK_IDS):
        raise ValueError("routing task IDs must equal the frozen held-out cases")
    budget = value.get("shared_budget")
    expected_budget = {
        "max_depth": 8,
        "max_nodes": 20,
        "max_selected_children": 4,
        "max_context_chars": 8000,
        "max_model_calls": 24,
        "max_tool_calls": 48,
    }
    if budget != expected_budget:
        raise ValueError("shared routing budget does not match the frozen contract")
    model_class = _nonempty(
        value.get("model_executor_class"),
        "routing_comparison.model_executor_class",
    )
    permissions = value.get("tool_permissions")
    if permissions != ["read_frozen_fixture", "write_ephemeral_fixture"]:
        raise ValueError("routing tool permissions must equal the frozen safe set")

    arms = value.get("arms")
    if not isinstance(arms, list) or len(arms) != len(ROUTING_ARM_IDS):
        raise ValueError("routing comparison must contain exactly three arms")
    expected_roles = {
        "current_context_kernel": "protected_control",
        "recursive_skill_in_skill": "candidate",
        "eager_all_context": "diagnostic_control",
    }
    for index, arm in enumerate(arms):
        label = f"routing_comparison.arms[{index}]"
        if not isinstance(arm, Mapping) or set(arm) != _ARM_FIELDS:
            raise ValueError(f"{label} has an unexpected schema")
        expected_id = ROUTING_ARM_IDS[index]
        if arm.get("arm_id") != expected_id:
            raise ValueError("routing arm IDs or labels changed after precommit")
        if arm.get("role") != expected_roles[expected_id]:
            raise ValueError("routing arm roles changed after precommit")
        _nonempty(arm.get("implementation_ref"), f"{label}.implementation_ref")
        if arm.get("task_ids") != task_ids:
            raise ValueError("all routing arms must use matched tasks")
        if arm.get("budget") != budget:
            raise ValueError("all routing arms must use matched budgets")
        if arm.get("model_executor_class") != model_class:
            raise ValueError("all routing arms must use a matched model/executor class")
        if arm.get("tool_permissions") != permissions:
            raise ValueError("all routing arms must use matched tool permissions")

    expected_orders = []
    arm_ids = list(ROUTING_ARM_IDS)
    for index, task_id in enumerate(ROUTING_TASK_IDS):
        offset = index % len(arm_ids)
        expected_orders.append(
            {"task_id": task_id, "arm_ids": arm_ids[offset:] + arm_ids[:offset]}
        )
    if value.get("execution_order") != expected_orders:
        raise ValueError("execution order must equal the frozen counterbalanced schedule")
    if value.get("metrics") != list(ROUTING_METRICS):
        raise ValueError("routing metrics must equal the frozen complete metric set")
    expected_rule = {
        "minimum_required_context_recall": 1.0,
        "require_no_final_outcome_regression": True,
        "require_lower_unnecessary_context_chars": True,
        "maximum_elapsed_ratio": 1.25,
        "maximum_model_call_ratio": 1.25,
        "maximum_tool_call_ratio": 1.25,
        "cost_policy": "compare_only_when_every_arm_reports_observable_cost",
        "global_activation_authorized": False,
    }
    if value.get("decision_rule") != expected_rule:
        raise ValueError("routing decision rule changed after precommit")
    if value.get("scorer") != "matched_context_routing_score_v1":
        raise ValueError("unexpected routing scorer")
    return deepcopy(dict(value))


def _validate_positive_control(
    value: Any,
    sources: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("scientist_positive_control must be an object")
    expected_fields = {
        "system",
        "source_artifact_id",
        "required_fidelity",
        "rubric_dimensions",
        "task_binding",
        "task_set_digest",
        "budget_binding",
        "budget_digest",
        "model_executor_class",
        "separate_receipt_required",
        "status",
        "receipt",
        "classification_matrix",
    }
    if set(value) != expected_fields:
        raise ValueError("scientist_positive_control has an unexpected schema")
    if value.get("system") != "The AI Scientist-v2":
        raise ValueError("unexpected scientist-agent positive-control system")
    if value.get("source_artifact_id") != "scientist_agent_baseline":
        raise ValueError("positive control must bind the frozen scientist baseline")
    if "scientist_agent_baseline" not in sources:
        raise ValueError("scientist baseline source binding is missing")
    if value.get("required_fidelity") != "unmodified_or_fidelity_preserving":
        raise ValueError("positive control must preserve demonstrated fidelity")
    if value.get("rubric_dimensions") != list(RUBRIC_DIMENSIONS):
        raise ValueError("positive-control rubric dimensions changed")
    task_binding = value.get("task_binding")
    budget_binding = value.get("budget_binding")
    if not isinstance(task_binding, Mapping) or not task_binding:
        raise ValueError("positive-control task binding must be non-empty")
    if not isinstance(budget_binding, Mapping) or not budget_binding:
        raise ValueError("positive-control budget binding must be non-empty")
    if value.get("task_set_digest") != _canonical_digest(task_binding):
        raise ValueError("positive-control task_set_digest does not bind its task")
    if value.get("budget_digest") != _canonical_digest(budget_binding):
        raise ValueError("positive-control budget_digest does not bind its budget")
    _nonempty(
        value.get("model_executor_class"),
        "scientist_positive_control.model_executor_class",
    )
    if value.get("separate_receipt_required") is not True:
        raise ValueError("the positive control must require a separate receipt")
    if value.get("status") != "NOT_RUN" or value.get("receipt") is not None:
        raise ValueError("the checked-in positive control must remain unmeasured")
    expected_matrix = {
        "missing_or_unrun_positive_control": "INSUFFICIENT_POSITIVE_CONTROL",
        "positive_control_failed": "BASELINE_REPRODUCTION_FAILURE",
        "positive_control_passed_adapted_failed": "ADAPTATION_OR_ABLATION_LOSS",
        "positive_control_passed_matched_target_failed": "NARROW_TARGET_TRANSFER_NEGATIVE",
        "evidence_against_original_method": False,
    }
    if value.get("classification_matrix") != expected_matrix:
        raise ValueError("positive-control classification matrix changed")
    return deepcopy(dict(value))


def validate_context_method_comparison(
    value: Mapping[str, Any],
    *,
    source_root: Path | None = None,
) -> dict[str, Any]:
    """Validate the immutable, zero-observation comparison precommit."""

    if set(value) != _PROTOCOL_FIELDS:
        raise ValueError("context-method comparison has an unexpected schema")
    if value.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    if value.get("status") != PROTOCOL_STATUS:
        raise ValueError("checked-in comparison status must remain PRECOMMITTED")
    if value.get("mechanism") != "context_method_comparison_positive_control_v1":
        raise ValueError("unexpected context-method comparison mechanism")
    if value.get("frozen_before_observation") is not True:
        raise ValueError("comparison must be frozen before any observation")
    _reject_secret_fields(value)

    sources = _validate_source_artifacts(
        value.get("source_artifacts"),
        source_root=source_root,
    )
    directives = value.get("user_directives")
    if directives != {
        "authoritative_inbox_revision": 19,
        "entry_ids": [
            "user-proposal-recursive-skill-context-retrieval-20260824-v16",
            "external-context-positive-control-before-adaptation-20260824-v17",
        ],
    }:
        raise ValueError("comparison must bind authoritative inbox revision 19")
    _validate_routing_comparison(value.get("routing_comparison"))
    _validate_positive_control(value.get("scientist_positive_control"), sources)

    if value.get("observations") != []:
        raise ValueError("the checked-in comparison must remain unmeasured")
    expected_decision = {
        "routing_verdict": "INSUFFICIENT_EVIDENCE",
        "scientist_inference": "INSUFFICIENT_POSITIVE_CONTROL",
        "scoped_activation_authorized": False,
        "global_activation_authorized": False,
        "reason": "The matched protocol is frozen, but neither routing outcomes nor a fidelity-preserving scientist-agent positive control have been observed.",
    }
    if value.get("decision") != expected_decision:
        raise ValueError("checked-in decision must remain fail-closed")
    boundary = value.get("next_measurement_boundary")
    expected_boundary = {
        "routing_executor": "fresh_unexposed_executor_required",
        "scientist_control": "separate_fidelity_preserving_receipt_required",
        "mutation_rule": "append_receipts_without_rewriting_protocol",
        "unavailable_evidence_action": "retain_insufficient_evidence_and_continue_independent_work",
    }
    if boundary != expected_boundary:
        raise ValueError("next measurement boundary changed")
    if value.get("user_level_verdict") != "FAIL":
        raise ValueError("user-level verdict must remain FAIL")
    claim_boundary = value.get("claim_boundary")
    required_false = {
        "agi_claim_supported",
        "user_goal_completed",
        "internal_measurement_is_independent_production_evidence",
        "routing_precommit_authorizes_activation",
        "adapted_failure_is_evidence_against_original_method",
    }
    if (
        not isinstance(claim_boundary, Mapping)
        or set(claim_boundary) != required_false
        or any(claim_boundary.get(field) is not False for field in required_false)
    ):
        raise ValueError("claim boundary must remain fail-closed")
    expected_digest = context_method_comparison_protocol_digest(value)
    if value.get("protocol_digest") != expected_digest:
        raise ValueError("protocol_digest does not bind the frozen comparison")
    return deepcopy(dict(value))


def load_context_method_comparison(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return validate_context_method_comparison(value)


def verify_current_context_method_sources(path: Path) -> dict[str, Any]:
    """Explicitly compare frozen source digests with current bytes before publication.

    Normal loading deliberately does not perform this check: a later legitimate inbox
    revision or source evolution must not rewrite or invalidate a historical precommit.
    """

    value = json.loads(path.read_text(encoding="utf-8"))
    return validate_context_method_comparison(value, source_root=path.parents[1])


def routing_comparison_receipt_digest(value: Mapping[str, Any]) -> str:
    return _canonical_digest({key: item for key, item in value.items() if key != "receipt_digest"})


def _validate_execution_conditions(
    protocol: Mapping[str, Any],
    conditions: Any,
) -> None:
    routing = protocol["routing_comparison"]
    expected = {
        "task_ids": routing["task_ids"],
        "shared_budget": routing["shared_budget"],
        "model_executor_class": routing["model_executor_class"],
        "tool_permissions": routing["tool_permissions"],
        "execution_order": routing["execution_order"],
    }
    if conditions != expected:
        raise ValueError("routing receipt uses unmatched tasks, budget, model, tools, or order")


def validate_routing_comparison_receipt(
    protocol: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a later matched routing receipt without mutating the protocol."""

    validated = validate_context_method_comparison(protocol)
    if set(receipt) != _ROUTING_RECEIPT_FIELDS:
        raise ValueError("routing receipt has an unexpected schema")
    if receipt.get("schema_version") != 1 or receipt.get("status") != "COMPLETE":
        raise ValueError("routing receipt must be a complete schema-version-1 receipt")
    if receipt.get("protocol_digest") != validated["protocol_digest"]:
        raise ValueError("routing receipt does not bind the frozen protocol")
    _validate_execution_conditions(validated, receipt.get("execution_conditions"))
    if receipt.get("labels_frozen_before_execution") is not True:
        raise ValueError("routing labels must be frozen before execution")
    if receipt.get("contamination_detected") is not False:
        raise ValueError("contaminated routing evidence is inadmissible")
    if receipt.get("sealed_before_classification") is not True:
        raise ValueError("routing receipt must be sealed before classification")

    results = receipt.get("arm_results")
    if not isinstance(results, list) or len(results) != len(ROUTING_ARM_IDS):
        raise ValueError("routing receipt must contain exactly three arm results")
    schedules = {
        row["task_id"]: row["arm_ids"]
        for row in validated["routing_comparison"]["execution_order"]
    }
    for arm_index, result in enumerate(results):
        label = f"arm_results[{arm_index}]"
        if not isinstance(result, Mapping) or set(result) != _ROUTING_RESULT_FIELDS:
            raise ValueError(f"{label} has an unexpected schema")
        arm_id = ROUTING_ARM_IDS[arm_index]
        if result.get("arm_id") != arm_id:
            raise ValueError("routing arm labels changed after output")
        task_results = result.get("task_results")
        if not isinstance(task_results, list) or len(task_results) != len(ROUTING_TASK_IDS):
            raise ValueError("every routing arm must report every frozen task")
        for task_index, task_result in enumerate(task_results):
            task_label = f"{label}.task_results[{task_index}]"
            if not isinstance(task_result, Mapping) or set(task_result) != _TASK_RESULT_FIELDS:
                raise ValueError(f"{task_label} has an unexpected schema")
            task_id = ROUTING_TASK_IDS[task_index]
            if task_result.get("task_id") != task_id:
                raise ValueError("routing task labels changed after output")
            expected_order = schedules[task_id].index(arm_id)
            if task_result.get("order_index") != expected_order:
                raise ValueError("routing result order does not match the precommit")
            recall = _finite_number(
                task_result.get("required_context_recall"),
                f"{task_label}.required_context_recall",
            )
            if recall > 1.0:
                raise ValueError("required_context_recall cannot exceed 1")
            ratio = _finite_number(
                task_result.get("unnecessary_context_ratio"),
                f"{task_label}.unnecessary_context_ratio",
            )
            if ratio > 1.0:
                raise ValueError("unnecessary_context_ratio cannot exceed 1")
            for field in (
                "unnecessary_context_chars",
                "elapsed_ms",
                "materialized_context_chars",
                "model_calls",
                "tool_calls",
            ):
                _nonnegative_int(task_result.get(field), f"{task_label}.{field}")
            if not isinstance(task_result.get("final_task_outcome"), bool):
                raise ValueError("final_task_outcome must be boolean")
            cost = task_result.get("observable_cost_usd")
            if cost is not None:
                _finite_number(cost, f"{task_label}.observable_cost_usd")
    if receipt.get("receipt_digest") != routing_comparison_receipt_digest(receipt):
        raise ValueError("routing receipt digest mismatch")
    return deepcopy(dict(receipt))


def classify_routing_comparison(
    protocol: Mapping[str, Any],
    receipt: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if receipt is None:
        return {
            "classification": "INSUFFICIENT_EVIDENCE",
            "scoped_activation_authorized": False,
            "global_activation_authorized": False,
            "agi_claim_supported": False,
        }
    validated = validate_routing_comparison_receipt(protocol, receipt)
    by_arm = {item["arm_id"]: item["task_results"] for item in validated["arm_results"]}
    control = by_arm["current_context_kernel"]
    candidate = by_arm["recursive_skill_in_skill"]
    rule = protocol["routing_comparison"]["decision_rule"]

    candidate_recall_pass = all(
        row["required_context_recall"] >= rule["minimum_required_context_recall"]
        for row in candidate
    )
    outcome_pass = sum(row["final_task_outcome"] for row in candidate) >= sum(
        row["final_task_outcome"] for row in control
    )
    unnecessary_pass = sum(row["unnecessary_context_chars"] for row in candidate) < sum(
        row["unnecessary_context_chars"] for row in control
    )

    def ratio(field: str) -> float:
        denominator = sum(row[field] for row in control)
        numerator = sum(row[field] for row in candidate)
        return 0.0 if denominator == 0 and numerator == 0 else math.inf if denominator == 0 else numerator / denominator

    resource_pass = (
        ratio("elapsed_ms") <= rule["maximum_elapsed_ratio"]
        and ratio("model_calls") <= rule["maximum_model_call_ratio"]
        and ratio("tool_calls") <= rule["maximum_tool_call_ratio"]
    )
    costs = [row["observable_cost_usd"] for row in control + candidate]
    cost_observable = all(cost is not None for cost in costs)
    cost_pass = not cost_observable or sum(row["observable_cost_usd"] for row in candidate) <= sum(
        row["observable_cost_usd"] for row in control
    )
    passed = candidate_recall_pass and outcome_pass and unnecessary_pass and resource_pass and cost_pass
    return {
        "classification": "SCOPED_ROUTING_ADVANTAGE" if passed else "NO_SCOPED_ROUTING_ADVANTAGE",
        "scoped_activation_authorized": passed,
        "global_activation_authorized": False,
        "agi_claim_supported": False,
        "cost_observable": cost_observable,
    }


def scientist_comparison_receipt_digest(value: Mapping[str, Any]) -> str:
    return _canonical_digest({key: item for key, item in value.items() if key != "receipt_digest"})


def _validate_scientist_result(
    value: Any,
    *,
    label: str,
    expected_fields: set[str],
    positive_control: Mapping[str, Any],
) -> None:
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        raise ValueError(f"{label} has an unexpected schema")
    if value.get("status") not in {"NOT_RUN", "PASS", "FAIL"}:
        raise ValueError(f"{label}.status is invalid")
    _nonempty(value.get("fidelity"), f"{label}.fidelity")
    if value.get("rubric_dimensions") != list(RUBRIC_DIMENSIONS):
        raise ValueError(f"{label} rubric dimensions do not match the precommit")
    if value.get("task_set_digest") != positive_control["task_set_digest"]:
        raise ValueError(f"{label} uses an unmatched task set")
    if value.get("budget_digest") != positive_control["budget_digest"]:
        raise ValueError(f"{label} uses an unmatched budget")
    if value.get("model_executor_class") != positive_control["model_executor_class"]:
        raise ValueError(f"{label} uses an unmatched model/executor class")
    result_digest = value.get("result_digest")
    if value.get("status") == "NOT_RUN":
        if result_digest is not None:
            raise ValueError(f"{label} cannot have a result digest before execution")
    else:
        _sha256(result_digest, f"{label}.result_digest")


def equivalent_positive_control_evidence_digest(value: Mapping[str, Any]) -> str:
    """Digest provenance for an already-established equivalent positive control."""

    return _canonical_digest(
        {key: item for key, item in value.items() if key != "evidence_digest"}
    )


def validate_equivalent_positive_control_evidence(
    protocol: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Admit a prior positive control only when its conditions match exactly.

    This is the revision-20 reuse path: it prevents a redundant reproduction,
    but it does not relax fidelity, task, budget, executor, result, or provenance
    requirements. The immutable revision-19 precommit remains unchanged.
    """

    validated = validate_context_method_comparison(protocol)
    if not isinstance(evidence, Mapping) or set(evidence) != _EQUIVALENT_POSITIVE_CONTROL_FIELDS:
        raise ValueError("equivalent positive-control evidence has an unexpected schema")
    _reject_secret_fields(evidence, "equivalent_positive_control")
    if evidence.get("schema_version") != 1:
        raise ValueError("equivalent positive-control schema_version must be 1")
    contract = validated["scientist_positive_control"]
    for field in (
        "evidence_id",
        "source_locator",
        "observed_at",
        "configuration_id",
    ):
        _nonempty(evidence.get(field), f"equivalent_positive_control.{field}")
    if evidence.get("system") != contract["system"]:
        raise ValueError("equivalent positive control names a different system")
    if evidence.get("source_artifact_id") != contract["source_artifact_id"]:
        raise ValueError("equivalent positive control does not bind the frozen baseline")
    if evidence.get("status") != "PASS":
        raise ValueError("equivalent positive control must be an established PASS")
    if evidence.get("fidelity") != contract["required_fidelity"]:
        raise ValueError("equivalent positive control is not fidelity-preserving")
    if evidence.get("rubric_dimensions") != contract["rubric_dimensions"]:
        raise ValueError("equivalent positive-control rubric does not match")
    for field in ("task_set_digest", "budget_digest", "model_executor_class"):
        if evidence.get(field) != contract[field]:
            raise ValueError(
                f"equivalent positive control uses an unmatched {field.replace('_', ' ')}"
            )
    _sha256(evidence.get("result_digest"), "equivalent_positive_control.result_digest")
    basis = evidence.get("equivalence_basis")
    if not isinstance(basis, list) or not basis or not all(
        isinstance(item, str) and item.strip() for item in basis
    ):
        raise ValueError("equivalent positive control must name its equivalence basis")
    if evidence.get("evidence_digest") != equivalent_positive_control_evidence_digest(
        evidence
    ):
        raise ValueError("equivalent positive-control evidence digest mismatch")
    return deepcopy(dict(evidence))


def validate_scientist_comparison_receipt(
    protocol: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    equivalent_positive_control: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    validated = validate_context_method_comparison(protocol)
    if set(receipt) != _SCIENTIST_RECEIPT_FIELDS:
        raise ValueError("scientist comparison receipt has an unexpected schema")
    if receipt.get("schema_version") != 1:
        raise ValueError("scientist comparison receipt schema_version must be 1")
    if receipt.get("protocol_digest") != validated["protocol_digest"]:
        raise ValueError("scientist receipt does not bind the frozen protocol")
    kind = receipt.get("comparison_kind")
    if kind not in {"adaptation", "target_domain_transfer"}:
        raise ValueError("unsupported scientist comparison kind")
    control_contract = validated["scientist_positive_control"]
    control = receipt.get("positive_control")
    evaluated = receipt.get("evaluated_arm")
    _validate_scientist_result(
        control,
        label="positive_control",
        expected_fields=_SCIENTIST_RESULT_FIELDS,
        positive_control=control_contract,
    )
    _validate_scientist_result(
        evaluated,
        label="evaluated_arm",
        expected_fields=_EVALUATED_RESULT_FIELDS,
        positive_control=control_contract,
    )
    if control["fidelity"] != control_contract["required_fidelity"]:
        raise ValueError("positive control is not fidelity-preserving")
    if equivalent_positive_control is not None:
        if control["status"] != "NOT_RUN":
            raise ValueError(
                "equivalent positive-control reuse is allowed only when no duplicate control was run"
            )
        validate_equivalent_positive_control_evidence(
            validated,
            equivalent_positive_control,
        )
    if evaluated.get("matched_to_positive_control") is not True:
        raise ValueError("evaluated arm must be matched to the positive control")
    target_domain = evaluated.get("target_domain")
    if kind == "adaptation":
        if evaluated["fidelity"] != "o_adapted" or target_domain is not None:
            raise ValueError("adaptation receipt has inconsistent arm identity")
    else:
        if (
            evaluated["fidelity"] != "original_method_fidelity_preserved"
            or not isinstance(target_domain, str)
            or not target_domain.strip()
        ):
            raise ValueError("target transfer must preserve fidelity and name its domain")
    if receipt.get("sealed_before_classification") is not True:
        raise ValueError("scientist receipt must be sealed before classification")
    if receipt.get("contamination_detected") is not False:
        raise ValueError("contaminated scientist evidence is inadmissible")
    if receipt.get("receipt_digest") != scientist_comparison_receipt_digest(receipt):
        raise ValueError("scientist comparison receipt digest mismatch")
    return deepcopy(dict(receipt))


def classify_scientist_comparison(
    protocol: Mapping[str, Any],
    receipt: Mapping[str, Any] | None,
    *,
    equivalent_positive_control: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify only the tested scope and reuse equivalent controls when proven."""

    validated_protocol = validate_context_method_comparison(protocol)
    reused_control: dict[str, Any] | None = None
    if equivalent_positive_control is not None:
        reused_control = validate_equivalent_positive_control_evidence(
            validated_protocol,
            equivalent_positive_control,
        )
    if receipt is None:
        classification = (
            "INSUFFICIENT_EVALUATED_ARM_EVIDENCE"
            if reused_control is not None
            else "INSUFFICIENT_POSITIVE_CONTROL"
        )
        control_basis = (
            "reused_equivalent_existing_evidence"
            if reused_control is not None
            else "none"
        )
        tested_scope = None
    else:
        validated = validate_scientist_comparison_receipt(
            validated_protocol,
            receipt,
            equivalent_positive_control=reused_control,
        )
        control_status = validated["positive_control"]["status"]
        control_basis = "receipt"
        if control_status == "NOT_RUN" and reused_control is not None:
            control_status = "PASS"
            control_basis = "reused_equivalent_existing_evidence"
        evaluated_status = validated["evaluated_arm"]["status"]
        if control_status == "NOT_RUN":
            classification = "INSUFFICIENT_POSITIVE_CONTROL"
        elif control_status == "FAIL":
            classification = "BASELINE_REPRODUCTION_FAILURE"
        elif evaluated_status == "NOT_RUN":
            classification = "INSUFFICIENT_EVALUATED_ARM_EVIDENCE"
        elif validated["comparison_kind"] == "adaptation":
            classification = (
                "ADAPTED_ARM_PASS_NO_ORIGINAL_METHOD_INFERENCE"
                if evaluated_status == "PASS"
                else "ADAPTATION_OR_ABLATION_LOSS"
            )
        else:
            classification = (
                "TARGET_TRANSFER_PASS_NARROW_SCOPE"
                if evaluated_status == "PASS"
                else "NARROW_TARGET_TRANSFER_NEGATIVE"
            )
        tested_scope = {
            "comparison_kind": validated["comparison_kind"],
            "system": validated_protocol["scientist_positive_control"]["system"],
            "evaluated_fidelity": validated["evaluated_arm"]["fidelity"],
            "target_domain": validated["evaluated_arm"]["target_domain"],
            "task_set_digest": validated["evaluated_arm"]["task_set_digest"],
            "budget_digest": validated["evaluated_arm"]["budget_digest"],
            "model_executor_class": validated["evaluated_arm"][
                "model_executor_class"
            ],
        }
    return {
        "classification": classification,
        "positive_control_basis": control_basis,
        "positive_control_evidence_digest": (
            reused_control["evidence_digest"] if reused_control is not None else None
        ),
        "tested_scope": tested_scope,
        "negative_evidence_scope": "tested_candidate_configuration_and_conditions_only",
        "evidence_against_original_method": False,
        "evidence_against_scientist_agent_family": False,
        "evidence_against_untested_mechanisms": False,
        "activation_authorized": False,
        "agi_claim_supported": False,
        "user_goal_completed": False,
    }
