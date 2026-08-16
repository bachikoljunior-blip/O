from __future__ import annotations

import copy

import pytest

from agi.induction import (
    Demonstration,
    InductionError,
    ProgramInductionLearner,
    run_induction_campaign,
)


def test_numeric_program_is_induced_from_demonstrations_not_task_id() -> None:
    learner = ProgramInductionLearner()
    learned = learner.learn(
        "completely-novel-skill-name",
        "numeric",
        [
            Demonstration(-2, -1),
            Demonstration(3, 9),
            Demonstration(7, 17),
        ],
    )

    assert learned.program_id == "affine:2:3"
    assert learner.apply("completely-novel-skill-name", 101) == 205


def test_ambiguous_learning_fails_without_overwriting_existing_skill() -> None:
    learner = ProgramInductionLearner()
    first = learner.learn(
        "text",
        "string",
        [Demonstration("Ab", "bA"), Demonstration("CdE", "EdC")],
    )
    assert first.program_id == "reverse"

    with pytest.raises(InductionError, match="ambiguous"):
        learner.learn(
            "text",
            "string",
            [Demonstration("abc", "abc"), Demonstration("xyz", "xyz")],
        )

    assert learner.apply("text", "HeLLo") == "oLLeH"
    assert learner.skills == (first,)


def test_contradictory_learning_fails_without_erasing_old_skill() -> None:
    learner = ProgramInductionLearner()
    learner.learn(
        "number",
        "numeric",
        [Demonstration(0, 5), Demonstration(2, 9), Demonstration(5, 15)],
    )
    before = learner.skills

    with pytest.raises(InductionError, match="no hypothesis"):
        learner.learn(
            "number",
            "numeric",
            [Demonstration(1, 10), Demonstration(2, -8), Demonstration(3, 99)],
        )

    assert learner.skills == before
    assert learner.apply("number", 8) == 21


def test_snapshot_reload_preserves_multiple_interleaved_skills() -> None:
    learner = ProgramInductionLearner()
    learner.learn(
        "n",
        "numeric",
        [Demonstration(1, 1), Demonstration(2, 4), Demonstration(3, 7)],
    )
    learner.learn(
        "s",
        "string",
        [Demonstration("Ab", "BA"), Demonstration("cDe", "EDC")],
    )
    learner.learn(
        "q",
        "sequence",
        [
            Demonstration([3, 1, 3], [1, 3]),
            Demonstration([8, 2, 8, 4], [2, 4, 8]),
        ],
    )

    restored = ProgramInductionLearner.from_snapshot(learner.snapshot())

    assert restored.apply("n", 11) == 31
    assert restored.apply("s", "ZeBrA") == "ARBEZ"
    assert restored.apply("q", [9, 1, 9, 3, 1]) == [1, 3, 9]


def test_snapshot_tamper_fails_closed() -> None:
    learner = ProgramInductionLearner()
    learner.learn(
        "n",
        "numeric",
        [Demonstration(1, 5), Demonstration(2, 8), Demonstration(4, 14)],
    )
    snapshot = learner.snapshot()
    tampered = copy.deepcopy(snapshot)
    tampered["skills"][0]["program_id"] = "affine:0:0"

    with pytest.raises(InductionError, match="digest mismatch"):
        ProgramInductionLearner.from_snapshot(tampered)


def test_campaign_measures_fresh_queries_retention_and_regression_gate() -> None:
    for seed in ("campaign-a", "campaign-b", "campaign-c", "campaign-d"):
        report = run_induction_campaign(seed)
        payload = report.to_dict()

        assert payload["passed"] is True
        assert payload["evidence_tier"] == "development"
        assert len(report.learned_programs) == 3
        assert len(report.task_results) == 6
        assert all(result.success for result in report.task_results)
        assert report.rejected_corrupt_update is True
        assert report.snapshot_reload_verified is True
        assert report.regression_decision["adopt_candidate"] is True
        assert report.regression_decision["decision"] == "ACTIVE_FOR_SCOPE"
        assert report.regression_decision["scope"] == "development-heldout-program-induction"
        assert report.regression_decision["protected_score_drops"] == []
        assert report.regression_decision["new_failures"] == []
        assert report.regression_decision["target_repeats"] == {
            "numeric-rule": 2,
            "sequence-rule": 2,
            "string-rule": 2,
        }
        assert len(report.digest) == 64
        assert "not evidence of AGI" in payload["claim_boundary"]


def test_campaign_seed_changes_hidden_campaign_commitment() -> None:
    left = run_induction_campaign("left")
    right = run_induction_campaign("right")

    assert left.seed_commitment != right.seed_commitment
    assert left.digest != right.digest
