from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from agi.checkpoint_inheritance_experiment import (
    checkpoint_measurement_readiness_digest,
    checkpoint_protocol_digest,
    evaluate_checkpoint_experiment,
    load_checkpoint_experiment,
    prepare_checkpoint_measurement_requests,
    validate_checkpoint_experiment,
    validate_checkpoint_measurement_readiness,
    verify_checkpoint_experiment_provenance,
)
from continual.work_session import WorkModelClient, WorkModelPending, submit_work_response


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_PATH = ROOT / "agi" / "CHECKPOINT_INHERITANCE_EXPERIMENT.json"
READINESS_PATH = ROOT / "agi" / "CHECKPOINT_INHERITANCE_MEASUREMENT_V2.json"


def _experiment() -> dict:
    return json.loads(EXPERIMENT_PATH.read_text(encoding="utf-8"))


def _readiness() -> dict:
    return json.loads(READINESS_PATH.read_text(encoding="utf-8"))


def _private_rubrics() -> dict:
    return {
        "research-unit-a": {
            "judge_kind": "exact_canonical_json",
            "judge_version": "exact-canonical-json-v1",
            "expected_answer": {
                "selected_hypothesis_id": "latency-cache",
                "experiment": "measure cached vs uncached median latency",
            },
            "success_threshold": 1.0,
        },
        "research-unit-b": {
            "judge_kind": "exact_canonical_json",
            "judge_version": "exact-canonical-json-v1",
            "expected_answer": {
                "selected_repair_id": "safe-minimal",
                "reason_code": "remove_regression_surface",
            },
            "success_threshold": 1.0,
        },
        "research-unit-c": {
            "judge_kind": "exact_canonical_json",
            "judge_version": "exact-canonical-json-v1",
            "expected_answer": {
                "retained_trace_id": "trace-a",
                "reason_code": "highest_safe_replay_stable_score_then_id",
            },
            "success_threshold": 1.0,
        },
    }


def _state() -> dict:
    return {
        "status": "running",
        "owner_kind": "work_recovery_automation",
        "execution_id": "work-recovery-test",
        "lease_generation": 8,
        "fence_token": "opaque-test-fence",
        "heartbeat_at": "2026-08-24T15:00:00Z",
        "stale_after_seconds": 900,
        "user_input_inbox": {"highest_acknowledged_revision": 20},
    }


def _attempt(candidate_id: str, *, score: float, invocations: int, seconds: float, retained: bool) -> dict:
    return {
        "candidate_id": candidate_id,
        "score": score,
        "model_invocations": invocations,
        "wall_time_seconds": seconds,
        "retained": retained,
        "capability_retained": retained,
        "replay_digest": f"sha256:{candidate_id}",
        "replay_verified": True,
        "protected_regressions_passed": True,
        "external_effects": [],
    }


def _native_attempt(
    root: Path,
    value: dict,
    *,
    task_id: str,
    arm: str,
    attempt: dict,
    model_identity: str = "work-model-under-test",
) -> dict:
    if not (root / "prompts").exists():
        shutil.copytree(ROOT / "prompts", root / "prompts")
    receipt_contract = {
        "protocol_digest": checkpoint_protocol_digest(value),
        "task_id": task_id,
        "arm": arm,
        "candidate_id": attempt["candidate_id"],
    }
    native_invocations = []
    for index in range(1, attempt["model_invocations"] + 1):
        step_contract = {
            **receipt_contract,
            "invocation_index": index,
            "invocation_count": attempt["model_invocations"],
        }
        client = WorkModelClient(
            root,
            run_id=f"run-{attempt['candidate_id']}-{index}",
            executor_binding="checkpoint-test-session",
            model_identity=model_identity,
        )
        payload = {
            "checkpoint_experiment_attempt": step_contract,
            "execution_unit": {
                "goal": "execute one frozen checkpoint experiment attempt step",
                "scope": "test-only checkpoint provenance fixture",
            },
        }
        with pytest.raises(WorkModelPending) as pending:
            client.call("execute", payload, prompt_path="prompts/execute.md")
        result = {"checkpoint_experiment_step": step_contract}
        if index == attempt["model_invocations"]:
            result["checkpoint_experiment_attempt"] = {**receipt_contract, **attempt}
        response = submit_work_response(
            root,
            pending.value.invocation_id,
            {
                "result": result,
                "local_learn": {"decision": "NO_CHANGE", "candidates": []},
                "fragment": {
                    "component": "execute",
                    "observations": ["frozen test receipt step recorded"],
                },
            },
            executor_binding="checkpoint-test-session",
            model_identity=model_identity,
        )
        request = json.loads(
            (
                root
                / ".continual"
                / "work-model"
                / "invocations"
                / pending.value.invocation_id
                / "request.json"
            ).read_text(encoding="utf-8")
        )
        native_invocations.append(
            {
                "invocation_id": pending.value.invocation_id,
                "run_id": request["run_id"],
                "request_digest": request["request_digest"],
                "response_digest": response["response_digest"],
                "executor_binding": request["executor_binding"],
                "model_identity": request["model_identity"],
                "model_verified": response["model_verified"],
            }
        )
    return {
        **attempt,
        "native_invocations": native_invocations,
    }


def _complete_measurement(
    root: Path,
    *,
    treatment_invocations: int = 1,
    treatment_seconds: float = 4.0,
    treatment_model_identity: str = "work-model-under-test",
) -> dict:
    value = _experiment()
    observations = []
    for task in value["matched_tasks"]:
        task_id = task["task_id"]
        control = _attempt(
            f"{task_id}-control",
            score=0.8,
            invocations=4,
            seconds=8.0,
            retained=True,
        )
        observations.append(
            {
                "task_id": task_id,
                "arm": "single_lineage",
                "measurement_source": "recorded_native_run",
                "attempts": [
                    _native_attempt(
                        root,
                        value,
                        task_id=task_id,
                        arm="single_lineage",
                        attempt=control,
                    )
                ],
            }
        )
        treatments = [
            _attempt(
                f"{task_id}-treatment-{index}",
                score=1.0 if index == 0 else 0.5,
                invocations=treatment_invocations,
                seconds=treatment_seconds,
                retained=index == 0,
            )
            for index in range(3)
        ]
        observations.append(
            {
                "task_id": task_id,
                "arm": "sibling_checkpoint",
                "measurement_source": "recorded_native_run",
                "attempts": [
                    _native_attempt(
                        root,
                        value,
                        task_id=task_id,
                        arm="sibling_checkpoint",
                        attempt=attempt,
                        model_identity=treatment_model_identity,
                    )
                    for attempt in treatments
                ],
            }
        )
    value["status"] = "MEASURED"
    value["observations"] = observations
    value["decision"] = {
        "verdict": "ADOPT_SCOPED_CANDIDATE",
        "reason": "Fixture verdict is recomputed by the evaluator.",
        "implementation_authorized": False,
    }
    return value


def test_checked_in_harness_is_valid_and_unmeasured() -> None:
    value = load_checkpoint_experiment(EXPERIMENT_PATH)
    assert value["status"] == "HARNESS_READY"
    assert evaluate_checkpoint_experiment(value)["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert value["decision"]["implementation_authorized"] is False


def test_exactly_three_tasks_and_frozen_widths_are_required() -> None:
    value = _experiment()
    value["matched_tasks"].pop()
    with pytest.raises(ValueError, match="exactly three"):
        validate_checkpoint_experiment(value)
    value = _experiment()
    value["arms"]["sibling_checkpoint"]["width"] = 2
    with pytest.raises(ValueError, match="width 3"):
        validate_checkpoint_experiment(value)


def test_main_write_and_external_effects_fail_closed(tmp_path: Path) -> None:
    value = _experiment()
    value["arms"]["sibling_checkpoint"]["main_write_allowed"] = True
    with pytest.raises(ValueError, match="cannot write main"):
        validate_checkpoint_experiment(value)
    value = _complete_measurement(tmp_path)
    value["observations"][1]["attempts"][0]["external_effects"] = ["push"]
    with pytest.raises(ValueError, match="external effects"):
        validate_checkpoint_experiment(value)


def test_incomplete_measurement_cannot_claim_a_measured_verdict() -> None:
    value = _experiment()
    value["decision"]["verdict"] = "ADOPT_SCOPED_CANDIDATE"
    with pytest.raises(ValueError, match="every task-arm"):
        validate_checkpoint_experiment(value)


def test_non_worsening_advantage_rule_can_support_scoped_candidate_only(
    tmp_path: Path,
) -> None:
    result = evaluate_checkpoint_experiment(
        _complete_measurement(tmp_path),
        root=tmp_path,
    )
    assert result["verdict"] == "ADOPT_SCOPED_CANDIDATE"
    assert result["implementation_authorized"] is False
    assert result["medians"]["sibling_checkpoint"]["model_invocations"] == 3.0
    assert result["medians"]["single_lineage"]["model_invocations"] == 4.0


def test_higher_parallel_cost_rejects_mechanism(tmp_path: Path) -> None:
    result = evaluate_checkpoint_experiment(
        _complete_measurement(tmp_path, treatment_invocations=2),
        root=tmp_path,
    )
    assert result["verdict"] == "REJECT_MECHANISM"
    assert result["implementation_authorized"] is False


def test_claim_boundary_and_replay_cannot_be_relaxed(tmp_path: Path) -> None:
    value = deepcopy(_experiment())
    value["claim_boundary"]["harness_is_capability_evidence"] = True
    with pytest.raises(ValueError, match="claim boundary"):
        validate_checkpoint_experiment(value)
    value = _complete_measurement(tmp_path)
    value["observations"][0]["attempts"][0]["replay_verified"] = False
    with pytest.raises(ValueError, match="exact replay"):
        validate_checkpoint_experiment(value)


def test_complete_measurement_requires_verified_native_provenance(tmp_path: Path) -> None:
    value = _complete_measurement(tmp_path)
    with pytest.raises(ValueError, match="native Work provenance"):
        evaluate_checkpoint_experiment(value)
    verified = verify_checkpoint_experiment_provenance(tmp_path, value)
    assert verified["status"] == "MEASURED"

    value["observations"][0]["attempts"][0]["native_invocations"][0][
        "response_digest"
    ] = "0" * 64
    with pytest.raises(ValueError, match="binding does not match"):
        verify_checkpoint_experiment_provenance(tmp_path, value)


def test_measurement_rejects_cross_arm_model_binding_changes(tmp_path: Path) -> None:
    value = _complete_measurement(
        tmp_path,
        treatment_model_identity="different-work-model",
    )
    with pytest.raises(ValueError, match="one executor/model binding"):
        verify_checkpoint_experiment_provenance(tmp_path, value)


def test_checked_in_measurement_readiness_is_exact_and_unobserved() -> None:
    experiment = _experiment()
    readiness = validate_checkpoint_measurement_readiness(_readiness(), experiment)
    assert readiness["status"] == "READY_NO_OBSERVATIONS"
    assert readiness["observations"] == []
    assert readiness["implementation_authorized"] is False
    assert len(checkpoint_measurement_readiness_digest(readiness)) == 64
    assert readiness["base_experiment"]["protocol_digest"] == checkpoint_protocol_digest(
        experiment
    )
    assert readiness["negative_evidence_scope"]["evidence_against_scientist_agent_family"] is False


def test_prepare_all_cells_without_exposing_judgment_or_recording_observations(
    tmp_path: Path,
) -> None:
    prepared = prepare_checkpoint_measurement_requests(
        tmp_path,
        run_id="run-checkpoint-readiness-test",
        state=_state(),
        experiment=_experiment(),
        readiness=_readiness(),
        private_rubrics=_private_rubrics(),
        executor_binding="checkpoint-readiness-test-session",
        model_identity="work-model-unverified",
        now="2026-08-24T15:01:00Z",
    )
    assert len(prepared["cells"]) == 6
    assert prepared["prepared_attempt_count"] == 12
    assert prepared["observation_count"] == 0
    assert prepared["implementation_authorized"] is False
    assert not (
        tmp_path
        / ".continual"
        / "runs"
        / "run-checkpoint-readiness-test"
        / "behavioral-outcomes"
        / "ledger.json"
    ).exists()
    child_units = [
        attempt["child_execution_unit"]
        for cell in prepared["cells"]
        for attempt in cell["attempts"]
    ]
    encoded = json.dumps(child_units, ensure_ascii=False, sort_keys=True)
    assert "expected_answer" not in encoded
    assert "answer_key" not in encoded
    assert '"labels"' not in encoded
    assert all(unit["production_effects_allowed"] is False for unit in child_units)
    assert all(unit["main_write_allowed"] is False for unit in child_units)

    replay = prepare_checkpoint_measurement_requests(
        tmp_path,
        run_id="run-checkpoint-readiness-test",
        state=_state(),
        experiment=_experiment(),
        readiness=_readiness(),
        private_rubrics=_private_rubrics(),
        executor_binding="checkpoint-readiness-test-session",
        model_identity="work-model-unverified",
        now="2026-08-24T15:02:00Z",
    )
    assert replay == prepared


def test_measurement_readiness_rejects_task_rubric_and_protocol_tampering(
    tmp_path: Path,
) -> None:
    readiness = _readiness()
    readiness["tasks"][0]["public_task"]["input"]["answer_key"] = "leak"
    with pytest.raises(ValueError, match="judgment-only"):
        validate_checkpoint_measurement_readiness(readiness, _experiment())

    readiness = _readiness()
    readiness["base_experiment"]["protocol_digest"] = "0" * 64
    with pytest.raises(ValueError, match="protocol digest mismatch"):
        validate_checkpoint_measurement_readiness(readiness, _experiment())

    rubrics = _private_rubrics()
    rubrics["research-unit-a"]["expected_answer"]["selected_hypothesis_id"] = "general-intelligence"
    with pytest.raises(ValueError, match="private rubric digest mismatch"):
        prepare_checkpoint_measurement_requests(
            tmp_path,
            run_id="run-checkpoint-readiness-tamper",
            state=_state(),
            experiment=_experiment(),
            readiness=_readiness(),
            private_rubrics=rubrics,
            executor_binding="checkpoint-readiness-test-session",
            model_identity="work-model-unverified",
            now="2026-08-24T15:01:00Z",
        )


def test_measurement_readiness_cannot_weaken_safety_or_claim_scope() -> None:
    readiness = deepcopy(_readiness())
    readiness["safety"]["non_idempotent_external_effects_allowed"] = True
    with pytest.raises(ValueError, match="safety boundary"):
        validate_checkpoint_measurement_readiness(readiness, _experiment())

    readiness = deepcopy(_readiness())
    readiness["claim_boundary"]["scientist_agent_family_inference_supported"] = True
    with pytest.raises(ValueError, match="claim boundary"):
        validate_checkpoint_measurement_readiness(readiness, _experiment())

    readiness = deepcopy(_readiness())
    readiness["observations"] = [{"synthetic": True}]
    with pytest.raises(ValueError, match="cannot contain observations"):
        validate_checkpoint_measurement_readiness(readiness, _experiment())
