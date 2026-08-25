from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Mapping

from agi.checkpoint_inheritance_experiment import (
    ARMS,
    checkpoint_measurement_readiness_digest,
    checkpoint_protocol_digest,
    validate_checkpoint_experiment,
    validate_checkpoint_measurement_readiness,
)
from continual.behavioral_outcome import verify_behavioral_outcome
from continual.work_session import verified_work_invocation


RUN_ID = "run-work-mode-handoff-v2"
MECHANISM = "bounded_stage_checkpoint_inheritance"
TASK_IDS = ("research-unit-a", "research-unit-b", "research-unit-c")
RESULT_PATH = "agi/CHECKPOINT_INHERITANCE_MEASUREMENT_RESULT.json"
SOURCE_PATHS = {
    "ledger": ".continual/runs/run-work-mode-handoff-v2/behavioral-outcomes/ledger.json",
    "task_a": ".continual/runs/run-work-mode-handoff-v2/artifacts/unit-research-a-record-outcomes-result.json",
    "task_b": ".continual/runs/run-work-mode-handoff-v2/artifacts/unit-research-b-record-outcomes-result.json",
    "task_c": ".continual/runs/run-work-mode-handoff-v2/artifacts/unit-research-c-record-outcomes-result.json",
    "final_evaluation": ".continual/runs/run-work-mode-handoff-v2/artifacts/unit-research-c-final-task-evaluate-result.json",
}
NEGATIVE_SCOPE = {
    "tested_candidate": MECHANISM,
    "tested_configuration": "three frozen tasks; width-1 single_lineage versus width-3 sibling_checkpoint; one invocation per attempt; exact-canonical-json; 4096-byte cap; current_chatgpt_work_session; chatgpt-work-model-unverified; sandbox; no effects or main writes",
    "tested_conditions": "the exact twelve immutable Work attempts and their precommitted requests, deterministic judgments, replay checks, and measured request-response intervals",
    "evidence_against_original_method": False,
    "evidence_against_scientist_agent_family": False,
    "evidence_against_untested_mechanisms": False,
    "adaptation_or_ablation_loss_established": False,
}
CLAIM_BOUNDARY = {
    "agi_claim_supported": False,
    "independent_production_evidence_observed": False,
    "scientist_agent_family_inference_supported": False,
    "original_method_inference_supported": False,
    "untested_mechanism_inference_supported": False,
    "adaptation_or_ablation_loss_established": False,
    "global_activation_authorized": False,
    "implementation_authorized": False,
    "user_level_objective_met": False,
}


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def checkpoint_measurement_result_digest(value: Mapping[str, Any]) -> str:
    frozen = deepcopy(dict(value))
    frozen.pop("result_digest", None)
    return _canonical_digest(frozen)


def _git_blob_sha(raw: bytes) -> str:
    return hashlib.sha1(f"blob {len(raw)}\0".encode("ascii") + raw).hexdigest()


def _load_bound_source(root: Path, binding: Mapping[str, Any], expected_path: str) -> dict[str, Any]:
    if not isinstance(binding, Mapping) or set(binding) != {"path", "sha256", "git_blob_sha"}:
        raise ValueError(f"source binding is malformed for {expected_path}")
    if binding.get("path") != expected_path:
        raise ValueError(f"unexpected source path for {expected_path}")
    path = root / expected_path
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding.get("sha256"):
        raise ValueError(f"source SHA-256 mismatch for {expected_path}")
    if _git_blob_sha(raw) != binding.get("git_blob_sha"):
        raise ValueError(f"source Git blob mismatch for {expected_path}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"source must be a JSON object: {expected_path}")
    return value


def _seconds(start: str, end: str) -> float:
    started = datetime.fromisoformat(start.replace("Z", "+00:00"))
    finished = datetime.fromisoformat(end.replace("Z", "+00:00"))
    return round((finished - started).total_seconds(), 6)


def validate_checkpoint_measurement_reconciliation(
    root: Path,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify the existing twelve attempts without preparing or executing a duplicate."""

    root = root.resolve()
    if value.get("schema_version") != 1:
        raise ValueError("measurement result schema_version must be 1")
    if value.get("record_type") != "checkpoint_measurement_reconciliation":
        raise ValueError("unexpected measurement result record_type")
    if value.get("status") != "MEASURED_RECONCILED":
        raise ValueError("measurement result must be MEASURED_RECONCILED")
    if value.get("run_id") != RUN_ID or value.get("mechanism") != MECHANISM:
        raise ValueError("measurement result identity mismatch")
    if value.get("result_digest") != checkpoint_measurement_result_digest(value):
        raise ValueError("measurement result digest mismatch")

    experiment = validate_checkpoint_experiment(
        json.loads((root / "agi/CHECKPOINT_INHERITANCE_EXPERIMENT.json").read_text(encoding="utf-8"))
    )
    readiness = validate_checkpoint_measurement_readiness(
        json.loads((root / "agi/CHECKPOINT_INHERITANCE_MEASUREMENT_V2.json").read_text(encoding="utf-8")),
        experiment,
    )
    protocol_digest = checkpoint_protocol_digest(experiment)
    readiness_digest = checkpoint_measurement_readiness_digest(readiness)
    if value.get("protocol_digest") != protocol_digest or value.get("readiness_digest") != readiness_digest:
        raise ValueError("frozen protocol/readiness digest mismatch")

    sources = value.get("source_records")
    if not isinstance(sources, Mapping) or set(sources) != set(SOURCE_PATHS):
        raise ValueError("measurement source record set mismatch")
    loaded = {
        name: _load_bound_source(root, sources[name], path)
        for name, path in SOURCE_PATHS.items()
    }
    ledger = loaded["ledger"]
    if (
        ledger.get("run_id") != RUN_ID
        or ledger.get("internal_observation_count") != 13
        or len(ledger.get("receipts", [])) != 13
        or ledger.get("ledger_digest") != value.get("ledger", {}).get("ledger_digest")
    ):
        raise ValueError("behavioral ledger binding mismatch")

    observations: list[dict[str, Any]] = []
    for source_name, field in (("task_a", "observations"), ("task_b", "observations"), ("task_c", "task_c_observations")):
        artifact = loaded[source_name]
        if artifact.get("protocol_digest") != protocol_digest or artifact.get("readiness_digest") != readiness_digest:
            raise ValueError(f"artifact protocol binding mismatch: {source_name}")
        items = artifact.get(field)
        if not isinstance(items, list) or len(items) != 4:
            raise ValueError(f"artifact must contain four attempts: {source_name}")
        observations.extend(deepcopy(items))
    if len(observations) != 12:
        raise ValueError("exactly twelve measurement attempts are required")

    expected_cells = {
        (task_id, arm, attempt_index)
        for task_id in TASK_IDS
        for arm, attempt_count in ARMS.items()
        for attempt_index in range(1, attempt_count + 1)
    }
    seen_cells: set[tuple[str, str, int]] = set()
    seen_outcomes: set[str] = set()
    executor_models: set[tuple[str, str]] = set()
    per_task: dict[str, dict[str, dict[str, float]]] = {task_id: {} for task_id in TASK_IDS}
    by_cell: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for observation in observations:
        outcome_id = observation.get("outcome_id")
        if not isinstance(outcome_id, str) or outcome_id in seen_outcomes:
            raise ValueError("measurement outcome identities must be unique")
        seen_outcomes.add(outcome_id)
        verified = verify_behavioral_outcome(root, run_id=RUN_ID, outcome_id=outcome_id)
        request = verified["request"]
        receipt = verified["receipt"]
        binding = request.get("task", {}).get("input", {}).get("checkpoint_measurement")
        if not isinstance(binding, Mapping):
            raise ValueError("measurement request lacks checkpoint binding")
        task_id = binding.get("task_id")
        arm = binding.get("arm")
        attempt_index = binding.get("attempt_index")
        cell = (task_id, arm, attempt_index)
        if cell not in expected_cells or cell in seen_cells:
            raise ValueError("unexpected or duplicate measurement cell")
        seen_cells.add(cell)
        expected_candidate = f"{task_id}-{arm}-{attempt_index}"
        if binding != {
            "protocol_digest": protocol_digest,
            "readiness_digest": readiness_digest,
            "task_id": task_id,
            "arm": arm,
            "candidate_id": expected_candidate,
            "attempt_index": attempt_index,
            "attempt_count": ARMS[arm],
            "max_model_invocations": 1,
        }:
            raise ValueError("measurement checkpoint binding mismatch")
        if request.get("budget") != {"max_response_bytes": 4096}:
            raise ValueError("measurement response budget mismatch")
        executor_models.add((request.get("executor_binding"), request.get("model_identity")))
        if receipt.get("receipt_digest") != observation.get("receipt_digest"):
            raise ValueError("artifact receipt digest mismatch")
        invocation_id = observation.get("work_invocation_id")
        if receipt.get("work_invocation", {}).get("invocation_id") != invocation_id:
            raise ValueError("artifact Work invocation mismatch")
        work = verified_work_invocation(root, invocation_id)
        work_request = work["request"]
        work_response = work["response"]
        unit = work_request.get("payload", {}).get("execution_unit")
        if (
            work_request.get("component") != "execute"
            or work_request.get("executor_binding") != request.get("executor_binding")
            or work_request.get("model_identity") != request.get("model_identity")
            or not isinstance(unit, Mapping)
            or unit.get("checkpoint_measurement") != binding
            or unit.get("production_effects_allowed") is not False
            or unit.get("main_write_allowed") is not False
        ):
            raise ValueError("bound Work request identity or safety mismatch")
        if receipt.get("work_invocation") != {
            "invocation_id": invocation_id,
            "request_digest": work_request["request_digest"],
            "response_digest": work_response["response_digest"],
            "output_digest": work_response["output_digest"],
        }:
            raise ValueError("receipt Work digest binding mismatch")
        if work["output"].get("result", {}).get("behavioral_answer") != verified["response"].get("answer"):
            raise ValueError("Work answer replay mismatch")
        judgment = receipt.get("judgment", {})
        if judgment.get("passed") is not True or judgment.get("score") != 1.0:
            raise ValueError("all measurement judgments must pass exactly")
        if any(
            observation.get(field) != expected
            for field, expected in (
                ("candidate_id", expected_candidate),
                ("model_invocations", 1),
                ("passed", True),
                ("score", 1.0),
                ("replay_verified", True),
                ("protected_regressions_passed", True),
                ("external_effects", []),
            )
        ):
            raise ValueError("artifact attempt safety or replay field mismatch")
        timestamps = receipt.get("timestamps", {})
        wall_time = _seconds(timestamps.get("work_request_created_at"), timestamps.get("work_response_received_at"))
        if wall_time != observation.get("wall_time_seconds"):
            raise ValueError("artifact wall-time measurement mismatch")
        behavioral_response = verified["response"]
        if behavioral_response.get("work_invocation_id") != invocation_id:
            raise ValueError("behavioral response Work binding mismatch")
        by_cell.setdefault((task_id, arm), []).append(observation)

    if seen_cells != expected_cells or len(executor_models) != 1:
        raise ValueError("measurement cells must share one executor/model binding")
    if executor_models != {("current_chatgpt_work_session", "chatgpt-work-model-unverified")}:
        raise ValueError("unexpected executor/model binding")

    for task_id in TASK_IDS:
        single = by_cell[(task_id, "single_lineage")]
        siblings = by_cell[(task_id, "sibling_checkpoint")]
        per_task[task_id] = {
            "single_lineage": {
                "model_invocations": 1,
                "wall_time_seconds": single[0]["wall_time_seconds"],
                "retained_score": 1.0,
            },
            "sibling_checkpoint": {
                "model_invocations": 3,
                "parallel_wall_time_seconds": max(item["wall_time_seconds"] for item in siblings),
                "retained_score": 1.0,
            },
        }
    medians = {
        "single_lineage": {
            "model_invocations": 1.0,
            "wall_time_seconds": median(per_task[task]["single_lineage"]["wall_time_seconds"] for task in TASK_IDS),
        },
        "sibling_checkpoint": {
            "model_invocations": 3.0,
            "wall_time_seconds": median(per_task[task]["sibling_checkpoint"]["parallel_wall_time_seconds"] for task in TASK_IDS),
        },
    }
    if value.get("metrics") != {"per_task": per_task, "medians": medians}:
        raise ValueError("reconciled measurement metrics mismatch")

    outcome_map = value.get("measurement_outcomes")
    expected_outcome_map = {
        task_id: {
            arm: [
                item["outcome_id"]
                for item in sorted(by_cell[(task_id, arm)], key=lambda item: item["candidate_id"])
            ]
            for arm in sorted(ARMS)
        }
        for task_id in TASK_IDS
    }
    if outcome_map != expected_outcome_map:
        raise ValueError("measurement outcome map mismatch")
    ledger_ids = {item.get("outcome_id") for item in ledger["receipts"]}
    if not seen_outcomes < ledger_ids or len(ledger_ids - seen_outcomes) != 1:
        raise ValueError("ledger must retain exactly one unrelated outcome beside the twelve measurements")
    if value.get("ledger") != {
        "ledger_digest": ledger["ledger_digest"],
        "receipt_count": 13,
        "measurement_receipt_count": 12,
        "unrelated_receipt_count": 1,
    }:
        raise ValueError("measurement ledger summary mismatch")

    controls = sorted(
        observation["outcome_id"] for observation in observations if observation["arm"] == "single_lineage"
    )
    if value.get("positive_controls") != {
        "policy": "reuse_provenance_equivalent_controls_without_duplicate_reproduction",
        "outcome_ids": controls,
        "duplicate_reproduction_required": False,
    }:
        raise ValueError("positive-control reuse policy mismatch")
    if value.get("decision") != {
        "verdict": "REJECT_MECHANISM",
        "reason": "sibling_checkpoint improved median wall time by 0.827209 seconds but worsened median model invocations from 1.0 to 3.0, violating the frozen non-worsening rule",
        "advantage_rule": "At least one median metric improves and neither worsens; exact replay and safety must pass.",
        "implementation_authorized": False,
        "global_activation_authorized": False,
    }:
        raise ValueError("measurement decision mismatch")
    if value.get("negative_evidence_scope") != NEGATIVE_SCOPE:
        raise ValueError("negative-evidence scope widened or changed")
    if value.get("claim_boundary") != CLAIM_BOUNDARY:
        raise ValueError("measurement claim boundary mismatch")
    if value.get("execution_policy") != {
        "existing_attempts_reused": True,
        "new_attempts_executed": 0,
        "duplicate_positive_control_executed": False,
        "rerun_forbidden_while_sources_validate": True,
    }:
        raise ValueError("measurement reuse policy mismatch")

    final = loaded["final_evaluation"]
    if (
        final.get("mechanism_verdict") != "REJECT_MECHANISM"
        or final.get("unit_completion_verdict") != "PASS"
        or final.get("user_level_verdict") != "FAIL"
        or final.get("negative_evidence_scope") != NEGATIVE_SCOPE
        or final.get("positive_control_policy") != "The three completed single_lineage attempts are the matched controls for this exact O measurement. No duplicate scientist-agent positive-control reproduction is required because no scientist-agent-family or original-method claim is evaluated."
    ):
        raise ValueError("final evaluation does not preserve the exact bounded verdict")
    return deepcopy(dict(value))


def load_checkpoint_measurement_reconciliation(root: Path) -> dict[str, Any]:
    value = json.loads((root / RESULT_PATH).read_text(encoding="utf-8"))
    return validate_checkpoint_measurement_reconciliation(root, value)
