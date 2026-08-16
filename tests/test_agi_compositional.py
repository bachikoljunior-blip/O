from __future__ import annotations

import copy

import pytest

from agi.compositional import CompositionalLearner, run_compositional_campaign
from agi.induction import Demonstration, InductionError


def test_two_step_numeric_composition_is_synthesized_from_primitives() -> None:
    learner = CompositionalLearner()
    skill = learner.learn(
        "novel",
        "numeric",
        [
            Demonstration(-2, -1),
            Demonstration(4, 11),
            Demonstration(9, 21),
        ],
        max_terms=2,
        allow_macros=False,
    )

    assert skill.expanded_primitives == ("times2", "plus3")
    assert learner.apply("novel", 100) == 203


def test_learned_skill_becomes_macro_for_deeper_behavior() -> None:
    learner = CompositionalLearner()
    base = learner.learn(
        "base",
        "string",
        [
            Demonstration("Abc", "cbA#"),
            Demonstration("qRs", "sRq#"),
            Demonstration("Model", "ledoM#"),
        ],
        max_terms=2,
        allow_macros=False,
    )
    assert base.expanded_primitives == ("reverse", "append_hash")

    derived = learner.learn(
        "derived",
        "string",
        [
            Demonstration("Ab", "bA#bA#"),
            Demonstration("Cat", "taC#taC#"),
            Demonstration("Q7x", "x7Q#x7Q#"),
        ],
        max_terms=2,
        allow_macros=True,
    )

    assert derived.expanded_primitives == ("reverse", "append_hash", "duplicate")
    assert "skill:base" in derived.terms
    assert learner.apply("derived", "Transfer") == "refsnarT#refsnarT#"


def test_deeper_behavior_is_unreachable_at_same_term_budget_without_macro() -> None:
    learner = CompositionalLearner()
    learner.learn(
        "base",
        "string",
        [Demonstration("Ab", "bA#"), Demonstration("Cat", "taC#")],
        max_terms=2,
        allow_macros=False,
    )
    demos = [
        Demonstration("Ab", "bA#bA#"),
        Demonstration("Cat", "taC#taC#"),
        Demonstration("Q7x", "x7Q#x7Q#"),
    ]

    with pytest.raises(InductionError, match="no composition"):
        learner.learn("without-macro", "string", demos, max_terms=2, allow_macros=False)

    with_macro = learner.learn("with-macro", "string", demos, max_terms=2, allow_macros=True)
    assert with_macro.expanded_primitives == ("reverse", "append_hash", "duplicate")


def test_failed_compositional_update_does_not_erase_prior_skill() -> None:
    learner = CompositionalLearner()
    first = learner.learn(
        "skill",
        "sequence",
        [
            Demonstration([3, 1, 3, 2], [2, 1, 3]),
            Demonstration([8, 4, 8, 5], [5, 4, 8]),
        ],
        max_terms=2,
        allow_macros=False,
    )
    before = learner.skills

    with pytest.raises(InductionError):
        learner.learn(
            "skill",
            "sequence",
            [
                Demonstration([1, 2], [999]),
                Demonstration([3, 4], [-999]),
            ],
            max_terms=2,
        )

    assert learner.skills == before == (first,)
    assert learner.apply("skill", [9, 2, 9, 1]) == [1, 2, 9]


def test_snapshot_tamper_is_rejected() -> None:
    learner = CompositionalLearner()
    learner.learn(
        "n",
        "numeric",
        [Demonstration(1, 5), Demonstration(2, 7), Demonstration(4, 11)],
        max_terms=2,
        allow_macros=False,
    )
    snapshot = learner.snapshot()
    tampered = copy.deepcopy(snapshot)
    tampered["skills"][0]["expanded_primitives"] = ["negate"]

    with pytest.raises(InductionError, match="digest mismatch"):
        CompositionalLearner.from_snapshot(tampered)


def test_compositional_campaign_passes_multiple_fresh_seeds_and_regression_gate() -> None:
    for seed in ("compose-a", "compose-b", "compose-c"):
        report = run_compositional_campaign(seed)
        assert report["passed"] is True
        assert report["evidence_tier"] == "development"
        assert report["snapshot_reload_verified"] is True
        assert report["rejected_bad_update"] is True
        assert len(report["task_results"]) == 8
        assert all(item["success"] for item in report["task_results"])
        assert report["regression_decision"]["adopt_candidate"] is True
        assert report["regression_decision"]["decision"] == "ACTIVE_FOR_SCOPE"
        assert report["regression_decision"]["protected_score_drops"] == []
        assert report["regression_decision"]["target_repeats"] == {
            "numeric-composed": 2,
            "sequence-composed": 2,
            "text-base": 2,
            "text-derived": 2,
        }
        derived = [item for item in report["skills"] if item["skill_id"] == "text-derived"]
        assert len(derived) == 1
        assert "skill:text-base" in derived[0]["terms"]
        assert "not AGI" in report["claim_boundary"]
