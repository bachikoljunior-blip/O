from __future__ import annotations

import pytest

from agi.regression import RegressionPolicy, TaskMeasurement, compare_snapshots


def _measurement(
    task_id: str,
    repeat: int,
    score: float,
    *,
    passed: bool = True,
    criterion: str = "transfer",
    domain: str = "symbolic",
) -> TaskMeasurement:
    return TaskMeasurement(
        task_id=task_id,
        criterion=criterion,
        domain=domain,
        repeat_index=repeat,
        passed=passed,
        score=score,
        artifact_sha256=(f"{repeat + 1:02x}" * 32),
    )


def test_candidate_with_repeated_target_gain_and_no_regression_can_advance() -> None:
    baseline = [
        _measurement("target", 0, 0.60),
        _measurement("target", 1, 0.65),
        _measurement("protected", 0, 0.90, criterion="robustness"),
    ]
    candidate = [
        _measurement("target", 0, 0.80),
        _measurement("target", 1, 0.85),
        _measurement("protected", 0, 0.90, criterion="robustness"),
    ]

    result = compare_snapshots(
        baseline,
        candidate,
        target_task_ids=["target"],
        candidate_id="candidate-a",
    )

    assert result["adopt_candidate"] is True
    assert result["reasons"] == []
    assert result["mean_target_gain"] == pytest.approx(0.20)
    assert result["target_repeats"] == {"target": 2}
    assert len(result["regression_evidence_sha256"]) == 64


def test_new_failure_on_old_capability_blocks_adoption() -> None:
    baseline = [
        _measurement("target", 0, 0.50),
        _measurement("target", 1, 0.50),
        _measurement("protected", 0, 0.90),
    ]
    candidate = [
        _measurement("target", 0, 0.80),
        _measurement("target", 1, 0.80),
        _measurement("protected", 0, 0.10, passed=False),
    ]

    result = compare_snapshots(
        baseline,
        candidate,
        target_task_ids=["target"],
    )

    assert result["adopt_candidate"] is False
    assert result["new_failures"][0]["task_id"] == "protected"
    assert any(reason.startswith("new_failures") for reason in result["reasons"])
    assert any(reason.startswith("protected_score_drops") for reason in result["reasons"])


def test_missing_baseline_measurement_fails_closed() -> None:
    baseline = [
        _measurement("target", 0, 0.40),
        _measurement("target", 1, 0.40),
        _measurement("protected", 0, 0.80),
    ]
    candidate = [
        _measurement("target", 0, 0.70),
        _measurement("target", 1, 0.70),
    ]

    result = compare_snapshots(
        baseline,
        candidate,
        target_task_ids=["target"],
    )

    assert result["adopt_candidate"] is False
    assert result["missing_protected_measurements"] == [
        {"task_id": "protected", "repeat_index": 0}
    ]


def test_one_lucky_target_trial_is_not_enough() -> None:
    baseline = [_measurement("target", 0, 0.20)]
    candidate = [_measurement("target", 0, 0.90)]

    result = compare_snapshots(
        baseline,
        candidate,
        target_task_ids=["target"],
        policy=RegressionPolicy(minimum_target_repeats=2),
    )

    assert result["adopt_candidate"] is False
    assert any(
        reason.startswith("insufficient_target_repeats")
        for reason in result["reasons"]
    )


def test_no_measurable_target_gain_blocks_adoption() -> None:
    baseline = [
        _measurement("target", 0, 0.80),
        _measurement("target", 1, 0.80),
    ]
    candidate = [
        _measurement("target", 0, 0.80),
        _measurement("target", 1, 0.80),
    ]

    result = compare_snapshots(
        baseline,
        candidate,
        target_task_ids=["target"],
    )

    assert result["adopt_candidate"] is False
    assert any(reason.startswith("mean_target_gain") for reason in result["reasons"])


def test_duplicate_repeat_is_rejected_before_scoring() -> None:
    duplicate = _measurement("target", 0, 0.50)

    with pytest.raises(ValueError, match="duplicate task measurement"):
        compare_snapshots(
            [duplicate, duplicate],
            [
                _measurement("target", 0, 0.70),
                _measurement("target", 1, 0.70),
            ],
            target_task_ids=["target"],
        )
