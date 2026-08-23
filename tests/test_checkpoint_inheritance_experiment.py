from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from agi.checkpoint_inheritance_experiment import (
    evaluate_checkpoint_experiment,
    load_checkpoint_experiment,
    validate_checkpoint_experiment,
)


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_PATH = ROOT / "agi" / "CHECKPOINT_INHERITANCE_EXPERIMENT.json"


def _experiment() -> dict:
    return json.loads(EXPERIMENT_PATH.read_text(encoding="utf-8"))


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


def _complete_measurement(*, treatment_invocations: int = 1, treatment_seconds: float = 4.0) -> dict:
    value = _experiment()
    observations = []
    for task in value["matched_tasks"]:
        task_id = task["task_id"]
        observations.append(
            {
                "task_id": task_id,
                "arm": "single_lineage",
                "measurement_source": "recorded_native_run",
                "attempts": [_attempt(f"{task_id}-control", score=0.8, invocations=4, seconds=8.0, retained=True)],
            }
        )
        observations.append(
            {
                "task_id": task_id,
                "arm": "sibling_checkpoint",
                "measurement_source": "recorded_native_run",
                "attempts": [
                    _attempt(
                        f"{task_id}-treatment-{index}",
                        score=1.0 if index == 0 else 0.5,
                        invocations=treatment_invocations,
                        seconds=treatment_seconds,
                        retained=index == 0,
                    )
                    for index in range(3)
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


def test_main_write_and_external_effects_fail_closed() -> None:
    value = _experiment()
    value["arms"]["sibling_checkpoint"]["main_write_allowed"] = True
    with pytest.raises(ValueError, match="cannot write main"):
        validate_checkpoint_experiment(value)
    value = _complete_measurement()
    value["observations"][1]["attempts"][0]["external_effects"] = ["push"]
    with pytest.raises(ValueError, match="external effects"):
        validate_checkpoint_experiment(value)


def test_incomplete_measurement_cannot_claim_a_measured_verdict() -> None:
    value = _experiment()
    value["decision"]["verdict"] = "ADOPT_SCOPED_CANDIDATE"
    with pytest.raises(ValueError, match="every task-arm"):
        validate_checkpoint_experiment(value)


def test_non_worsening_advantage_rule_can_support_scoped_candidate_only() -> None:
    result = evaluate_checkpoint_experiment(_complete_measurement())
    assert result["verdict"] == "ADOPT_SCOPED_CANDIDATE"
    assert result["implementation_authorized"] is False
    assert result["medians"]["sibling_checkpoint"]["model_invocations"] == 3.0
    assert result["medians"]["single_lineage"]["model_invocations"] == 4.0


def test_higher_parallel_cost_rejects_mechanism() -> None:
    result = evaluate_checkpoint_experiment(_complete_measurement(treatment_invocations=2))
    assert result["verdict"] == "REJECT_MECHANISM"
    assert result["implementation_authorized"] is False


def test_claim_boundary_and_replay_cannot_be_relaxed() -> None:
    value = deepcopy(_experiment())
    value["claim_boundary"]["harness_is_capability_evidence"] = True
    with pytest.raises(ValueError, match="claim boundary"):
        validate_checkpoint_experiment(value)
    value = _complete_measurement()
    value["observations"][0]["attempts"][0]["replay_verified"] = False
    with pytest.raises(ValueError, match="exact replay"):
        validate_checkpoint_experiment(value)
