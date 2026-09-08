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


def test_repeated_target_gain_without_regression_activates_only_named_scope() -> None:
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
        scope="symbolic-transfer",
        candidate_id="candidate-a",
    )

    assert result["adopt_candidate"] is True
    assert result["decision"] == "ACTIVE_FOR_SCOPE"
    assert result["scope_state"] == "VERIFIED_FOR_SCOPE"
    assert result["scope"] == "symbolic-transfer"
    assert result["reasons"] == []
    assert result["negative_evidence"] == []
    assert result["mean_target_gain_by_task"] == {"target": pytest.approx(0.20)}
    assert result["target_repeats"] == {"target": 2}
    assert len(result["regression_evidence_sha256"]) == 64


def test_new_failure_on_old_capability_blocks_adoption_and_is_retained() -> None:
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
        scope="trial-scope",
    )

    assert result["adopt_candidate"] is False
    assert result["decision"] == "REMAIN_CANDIDATE"
    assert result["new_failures"][0]["task_id"] == "protected"
    assert any(item["type"] == "new_failure" for item in result["negative_evidence"])
    assert any(item["type"] == "protected_score_drop" for item in result["negative_evidence"])


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
        scope="trial-scope",
    )

    assert result["adopt_candidate"] is False
    assert result["missing_protected_measurements"] == [
        {"task_id": "protected", "repeat_index": 0}
    ]
    assert {item["type"] for item in result["negative_evidence"]} >= {
        "missing_protected_measurement"
    }


def test_each_target_must_improve_not_just_pooled_mean() -> None:
    baseline = [
        _measurement("target-a", 0, 0.20),
        _measurement("target-a", 1, 0.20),
        _measurement("target-b", 0, 0.80),
        _measurement("target-b", 1, 0.80),
    ]
    candidate = [
        _measurement("target-a", 0, 0.80),
        _measurement("target-a", 1, 0.80),
        _measurement("target-b", 0, 0.80),
        _measurement("target-b", 1, 0.80),
    ]

    result = compare_snapshots(
        baseline,
        candidate,
        target_task_ids=["target-a", "target-b"],
        scope="multi-target",
    )

    assert result["adopt_candidate"] is False
    weak = [item for item in result["negative_evidence"] if item["type"] == "insufficient_target_gain"]
    assert [item["task_id"] for item in weak] == ["target-b"]


def test_one_lucky_target_trial_is_not_enough() -> None:
    result = compare_snapshots(
        [_measurement("target", 0, 0.20)],
        [_measurement("target", 0, 0.90)],
        target_task_ids=["target"],
        scope="trial-scope",
        policy=RegressionPolicy(minimum_target_repeats=2),
    )

    assert result["adopt_candidate"] is False
    assert any(
        reason.startswith("insufficient_target_repeats") for reason in result["reasons"]
    )


def test_metadata_mismatch_is_not_comparable() -> None:
    baseline = [
        _measurement("target", 0, 0.20),
        _measurement("target", 1, 0.20),
    ]
    candidate = [
        _measurement("target", 0, 0.80, domain="different"),
        _measurement("target", 1, 0.80),
    ]

    result = compare_snapshots(
        baseline,
        candidate,
        target_task_ids=["target"],
        scope="trial-scope",
    )

    assert result["adopt_candidate"] is False
    assert any(item["type"] == "incomparable_measurement" for item in result["negative_evidence"])


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
            scope="trial-scope",
        )


def test_non_finite_scores_fail_closed_before_digesting() -> None:
    with pytest.raises(ValueError, match="score must be finite"):
        compare_snapshots(
            [
                _measurement("target", 0, float("nan")),
                _measurement("target", 1, 0.4),
            ],
            [
                _measurement("target", 0, 0.8),
                _measurement("target", 1, 0.8),
            ],
            target_task_ids=["target"],
            scope="trial-scope",
        )
