from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from agi.context_method_comparison import (
    ROUTING_ARM_IDS,
    ROUTING_TASK_IDS,
    classify_routing_comparison,
    classify_scientist_comparison,
    context_method_comparison_protocol_digest,
    load_context_method_comparison,
    routing_comparison_receipt_digest,
    scientist_comparison_receipt_digest,
    validate_context_method_comparison,
    validate_routing_comparison_receipt,
    validate_scientist_comparison_receipt,
    verify_current_context_method_sources,
)


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "agi" / "CONTEXT_METHOD_COMPARISON.json"
ROUTING_EXPERIMENT_PATH = ROOT / "agi" / "CONTEXT_ROUTING_EXPERIMENT.json"


def _protocol() -> dict:
    return json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))


def _routing_receipt(protocol: dict) -> dict:
    routing = protocol["routing_comparison"]
    schedules = {
        item["task_id"]: item["arm_ids"] for item in routing["execution_order"]
    }
    profiles = {
        "current_context_kernel": {
            "unnecessary": 100,
            "elapsed": 100,
            "context": 300,
        },
        "recursive_skill_in_skill": {
            "unnecessary": 50,
            "elapsed": 110,
            "context": 220,
        },
        "eager_all_context": {
            "unnecessary": 240,
            "elapsed": 90,
            "context": 500,
        },
    }
    arm_results = []
    for arm_id in ROUTING_ARM_IDS:
        profile = profiles[arm_id]
        task_results = []
        for task_id in ROUTING_TASK_IDS:
            task_results.append(
                {
                    "task_id": task_id,
                    "order_index": schedules[task_id].index(arm_id),
                    "required_context_recall": 1.0,
                    "unnecessary_context_chars": profile["unnecessary"],
                    "unnecessary_context_ratio": profile["unnecessary"] / 1000,
                    "final_task_outcome": True,
                    "elapsed_ms": profile["elapsed"],
                    "materialized_context_chars": profile["context"],
                    "model_calls": 2,
                    "tool_calls": 3,
                    "observable_cost_usd": None,
                }
            )
        arm_results.append({"arm_id": arm_id, "task_results": task_results})
    receipt = {
        "schema_version": 1,
        "protocol_digest": protocol["protocol_digest"],
        "status": "COMPLETE",
        "execution_conditions": {
            "task_ids": deepcopy(routing["task_ids"]),
            "shared_budget": deepcopy(routing["shared_budget"]),
            "model_executor_class": routing["model_executor_class"],
            "tool_permissions": deepcopy(routing["tool_permissions"]),
            "execution_order": deepcopy(routing["execution_order"]),
        },
        "arm_results": arm_results,
        "labels_frozen_before_execution": True,
        "contamination_detected": False,
        "sealed_before_classification": True,
        "receipt_digest": "",
    }
    receipt["receipt_digest"] = routing_comparison_receipt_digest(receipt)
    return receipt


def _scientist_receipt(
    protocol: dict,
    *,
    control_status: str,
    evaluated_status: str,
    kind: str = "adaptation",
) -> dict:
    contract = protocol["scientist_positive_control"]

    def result(status: str, fidelity: str) -> dict:
        return {
            "status": status,
            "fidelity": fidelity,
            "rubric_dimensions": contract["rubric_dimensions"],
            "task_set_digest": contract["task_set_digest"],
            "budget_digest": contract["budget_digest"],
            "model_executor_class": contract["model_executor_class"],
            "result_digest": None if status == "NOT_RUN" else "1" * 64,
        }

    control = result(control_status, contract["required_fidelity"])
    evaluated = result(
        evaluated_status,
        "o_adapted" if kind == "adaptation" else "original_method_fidelity_preserved",
    )
    evaluated.update(
        {
            "matched_to_positive_control": True,
            "target_domain": None if kind == "adaptation" else "o-repository-development",
        }
    )
    receipt = {
        "schema_version": 1,
        "protocol_digest": protocol["protocol_digest"],
        "comparison_kind": kind,
        "positive_control": control,
        "evaluated_arm": evaluated,
        "sealed_before_classification": True,
        "contamination_detected": False,
        "receipt_digest": "",
    }
    receipt["receipt_digest"] = scientist_comparison_receipt_digest(receipt)
    return receipt


def test_checked_in_protocol_is_valid_frozen_and_unmeasured() -> None:
    protocol = load_context_method_comparison(PROTOCOL_PATH)

    assert protocol["status"] == "PRECOMMITTED"
    assert protocol["protocol_digest"] == context_method_comparison_protocol_digest(protocol)
    assert protocol["observations"] == []
    assert protocol["scientist_positive_control"]["status"] == "NOT_RUN"
    assert protocol["scientist_positive_control"]["receipt"] is None
    assert protocol["decision"]["routing_verdict"] == "INSUFFICIENT_EVIDENCE"
    assert protocol["decision"]["scientist_inference"] == "INSUFFICIENT_POSITIVE_CONTROL"
    assert protocol["decision"]["scoped_activation_authorized"] is False
    assert protocol["decision"]["global_activation_authorized"] is False
    assert protocol["user_level_verdict"] == "FAIL"
    assert all(value is False for value in protocol["claim_boundary"].values())


def test_existing_routing_harness_remains_zero_observation() -> None:
    experiment = json.loads(ROUTING_EXPERIMENT_PATH.read_text(encoding="utf-8"))

    assert experiment["status"] == "HARNESS_READY"
    assert experiment["observations"] == []
    assert experiment["decision"]["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_protocol_digest_binds_sources_tasks_budgets_metrics_and_positive_control() -> None:
    protocol = _protocol()
    original = context_method_comparison_protocol_digest(protocol)
    mutations = []
    source_changed = deepcopy(protocol)
    source_changed["source_artifacts"][0]["sha256"] = "0" * 64
    mutations.append(source_changed)
    tasks_changed = deepcopy(protocol)
    tasks_changed["routing_comparison"]["task_ids"][0] = "relabeled"
    mutations.append(tasks_changed)
    budget_changed = deepcopy(protocol)
    budget_changed["routing_comparison"]["shared_budget"]["max_nodes"] += 1
    mutations.append(budget_changed)
    metric_changed = deepcopy(protocol)
    metric_changed["routing_comparison"]["metrics"][0]["direction"] = "lower_is_better"
    mutations.append(metric_changed)
    control_changed = deepcopy(protocol)
    control_changed["scientist_positive_control"]["required_fidelity"] = "modified"
    mutations.append(control_changed)

    assert all(context_method_comparison_protocol_digest(item) != original for item in mutations)


def test_protocol_digest_tamper_fails_closed() -> None:
    protocol = _protocol()
    protocol["protocol_digest"] = "0" * 64

    with pytest.raises(ValueError, match="protocol_digest"):
        validate_context_method_comparison(protocol)


def test_unmatched_arm_tasks_and_budgets_fail_closed() -> None:
    protocol = _protocol()
    protocol["routing_comparison"]["arms"][1]["task_ids"] = list(ROUTING_TASK_IDS[:-1])
    with pytest.raises(ValueError, match="matched tasks"):
        validate_context_method_comparison(protocol)

    protocol = _protocol()
    protocol["routing_comparison"]["arms"][1]["budget"]["max_nodes"] += 1
    with pytest.raises(ValueError, match="matched budgets"):
        validate_context_method_comparison(protocol)


def test_source_artifact_change_is_bound_without_rebinding_historical_protocol() -> None:
    protocol = _protocol()
    protocol["source_artifacts"][0]["sha256"] = "0" * 64

    with pytest.raises(ValueError, match="protocol_digest"):
        validate_context_method_comparison(protocol)


def test_current_source_verification_is_an_explicit_prepublication_check() -> None:
    assert verify_current_context_method_sources(PROTOCOL_PATH)["status"] == "PRECOMMITTED"


def test_claim_inflation_and_checked_in_measurements_are_rejected() -> None:
    protocol = _protocol()
    protocol["claim_boundary"]["agi_claim_supported"] = True
    with pytest.raises(ValueError, match="claim boundary"):
        validate_context_method_comparison(protocol)

    protocol = _protocol()
    protocol["observations"] = [{"claimed": "measurement"}]
    with pytest.raises(ValueError, match="remain unmeasured"):
        validate_context_method_comparison(protocol)

    protocol = _protocol()
    protocol["decision"]["scoped_activation_authorized"] = True
    with pytest.raises(ValueError, match="fail-closed"):
        validate_context_method_comparison(protocol)


def test_valid_matched_routing_receipt_is_deterministically_classified() -> None:
    protocol = _protocol()
    receipt = _routing_receipt(protocol)

    assert validate_routing_comparison_receipt(protocol, receipt) == receipt
    result = classify_routing_comparison(protocol, receipt)
    assert result["classification"] == "SCOPED_ROUTING_ADVANTAGE"
    assert result["global_activation_authorized"] is False
    assert result["agi_claim_supported"] is False
    assert result["cost_observable"] is False


def test_routing_receipt_rejects_post_output_relabeling_and_order_changes() -> None:
    protocol = _protocol()
    receipt = _routing_receipt(protocol)
    receipt["arm_results"][1]["arm_id"] = "renamed_after_output"
    receipt["receipt_digest"] = routing_comparison_receipt_digest(receipt)
    with pytest.raises(ValueError, match="labels changed"):
        validate_routing_comparison_receipt(protocol, receipt)

    receipt = _routing_receipt(protocol)
    receipt["arm_results"][0]["task_results"][0]["order_index"] = 2
    receipt["receipt_digest"] = routing_comparison_receipt_digest(receipt)
    with pytest.raises(ValueError, match="order does not match"):
        validate_routing_comparison_receipt(protocol, receipt)


def test_routing_receipt_rejects_unmatched_conditions_and_digest_tamper() -> None:
    protocol = _protocol()
    receipt = _routing_receipt(protocol)
    receipt["execution_conditions"]["shared_budget"]["max_nodes"] += 1
    receipt["receipt_digest"] = routing_comparison_receipt_digest(receipt)
    with pytest.raises(ValueError, match="unmatched tasks"):
        validate_routing_comparison_receipt(protocol, receipt)

    receipt = _routing_receipt(protocol)
    receipt["receipt_digest"] = "0" * 64
    with pytest.raises(ValueError, match="digest mismatch"):
        validate_routing_comparison_receipt(protocol, receipt)


def test_missing_positive_control_stays_insufficient() -> None:
    result = classify_scientist_comparison(_protocol(), None)

    assert result["classification"] == "INSUFFICIENT_POSITIVE_CONTROL"
    assert result["evidence_against_original_method"] is False
    assert result["activation_authorized"] is False


def test_failed_positive_control_is_only_reproduction_failure() -> None:
    protocol = _protocol()
    receipt = _scientist_receipt(
        protocol,
        control_status="FAIL",
        evaluated_status="FAIL",
    )

    result = classify_scientist_comparison(protocol, receipt)
    assert result["classification"] == "BASELINE_REPRODUCTION_FAILURE"
    assert result["evidence_against_original_method"] is False


def test_positive_control_pass_plus_adapted_failure_is_adaptation_loss() -> None:
    protocol = _protocol()
    receipt = _scientist_receipt(
        protocol,
        control_status="PASS",
        evaluated_status="FAIL",
    )

    result = classify_scientist_comparison(protocol, receipt)
    assert result["classification"] == "ADAPTATION_OR_ABLATION_LOSS"
    assert result["evidence_against_original_method"] is False


def test_positive_control_pass_plus_matched_target_failure_is_narrow_only() -> None:
    protocol = _protocol()
    receipt = _scientist_receipt(
        protocol,
        control_status="PASS",
        evaluated_status="FAIL",
        kind="target_domain_transfer",
    )

    result = classify_scientist_comparison(protocol, receipt)
    assert result["classification"] == "NARROW_TARGET_TRANSFER_NEGATIVE"
    assert result["evidence_against_original_method"] is False
    assert result["agi_claim_supported"] is False


def test_scientist_receipt_rejects_unmatched_tasks_budget_and_fidelity() -> None:
    protocol = _protocol()
    receipt = _scientist_receipt(
        protocol,
        control_status="PASS",
        evaluated_status="FAIL",
    )
    receipt["evaluated_arm"]["task_set_digest"] = "2" * 64
    receipt["receipt_digest"] = scientist_comparison_receipt_digest(receipt)
    with pytest.raises(ValueError, match="unmatched task set"):
        validate_scientist_comparison_receipt(protocol, receipt)

    receipt = _scientist_receipt(
        protocol,
        control_status="PASS",
        evaluated_status="FAIL",
    )
    receipt["evaluated_arm"]["budget_digest"] = "2" * 64
    receipt["receipt_digest"] = scientist_comparison_receipt_digest(receipt)
    with pytest.raises(ValueError, match="unmatched budget"):
        validate_scientist_comparison_receipt(protocol, receipt)

    receipt = _scientist_receipt(
        protocol,
        control_status="PASS",
        evaluated_status="FAIL",
    )
    receipt["positive_control"]["fidelity"] = "modified"
    receipt["receipt_digest"] = scientist_comparison_receipt_digest(receipt)
    with pytest.raises(ValueError, match="not fidelity-preserving"):
        validate_scientist_comparison_receipt(protocol, receipt)


def test_scientist_receipt_rejects_contamination_and_digest_tamper() -> None:
    protocol = _protocol()
    receipt = _scientist_receipt(
        protocol,
        control_status="PASS",
        evaluated_status="PASS",
    )
    receipt["contamination_detected"] = True
    receipt["receipt_digest"] = scientist_comparison_receipt_digest(receipt)
    with pytest.raises(ValueError, match="contaminated"):
        validate_scientist_comparison_receipt(protocol, receipt)

    receipt = _scientist_receipt(
        protocol,
        control_status="PASS",
        evaluated_status="PASS",
    )
    receipt["receipt_digest"] = "0" * 64
    with pytest.raises(ValueError, match="digest mismatch"):
        validate_scientist_comparison_receipt(protocol, receipt)
