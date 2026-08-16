from __future__ import annotations

import pytest

from agi.induction import Demonstration, InductionError
from agi.typed_composition import TypedCompositionalLearner, run_typed_cross_domain_campaign


def test_learned_macro_crosses_string_to_numeric_with_same_term_budget() -> None:
    base_demos = [
        Demonstration("Abc", "cbA#"),
        Demonstration("Model", "ledoM#"),
        Demonstration("Q7x", "x7Q#"),
    ]
    length_demos = [
        Demonstration("Abc", 4),
        Demonstration("Model", 6),
        Demonstration("Q7x", 4),
    ]

    raw = TypedCompositionalLearner()
    with pytest.raises(InductionError, match="no well-typed composition"):
        raw.learn("length", "string", length_demos, max_terms=2, allow_macros=False)

    learner = TypedCompositionalLearner()
    base = learner.learn("base", "string", base_demos, max_terms=2, allow_macros=False)
    learned = learner.learn("length", "string", length_demos, max_terms=2, allow_macros=True)

    assert base.output_domain == "string"
    assert learned.output_domain == "numeric"
    assert learned.expanded_primitives == (
        "string.reverse",
        "string.append_hash",
        "string.length",
    )
    assert "skill:base" in learned.terms
    assert learner.apply("length", "Transfer") == 9


def test_recursive_macro_reuse_expands_cross_domain_solvable_depth() -> None:
    learner = TypedCompositionalLearner()
    learner.learn(
        "base",
        "string",
        [
            Demonstration("Abc", "cbA#"),
            Demonstration("Model", "ledoM#"),
            Demonstration("Q7x", "x7Q#"),
        ],
        max_terms=2,
        allow_macros=False,
    )
    chars = learner.learn(
        "chars",
        "string",
        [
            Demonstration("Abc", list("cbA#")),
            Demonstration("Model", list("ledoM#")),
            Demonstration("Q7x", list("x7Q#")),
        ],
        max_terms=2,
        allow_macros=True,
    )
    assert chars.output_domain == "sequence"
    assert "skill:base" in chars.terms

    deep_demos = [
        Demonstration("Aba", list(dict.fromkeys("abA#"))),
        Demonstration("ModelM", list(dict.fromkeys("MledoM#"))),
        Demonstration("LevelX", list(dict.fromkeys("XleveL#"))),
    ]
    raw = TypedCompositionalLearner()
    with pytest.raises(InductionError, match="no well-typed composition"):
        raw.learn("deep", "string", deep_demos, max_terms=2, allow_macros=False)

    deep = learner.learn("deep", "string", deep_demos, max_terms=2, allow_macros=True)
    assert deep.output_domain == "sequence"
    assert len(deep.expanded_primitives) == 4
    assert "skill:chars" in deep.terms
    assert learner.apply("deep", "Mississippi") == list(dict.fromkeys("ippississiM#"))


def test_failed_typed_update_preserves_existing_skill() -> None:
    learner = TypedCompositionalLearner()
    first = learner.learn(
        "base",
        "string",
        [Demonstration("Ab", "bA#"), Demonstration("Cat", "taC#")],
        max_terms=2,
        allow_macros=False,
    )
    before = learner.skills

    with pytest.raises(InductionError):
        learner.learn(
            "base",
            "string",
            [Demonstration("A", {"impossible": 1}), Demonstration("B", {"impossible": 2})],
            max_terms=2,
        )

    assert learner.skills == before == (first,)
    assert learner.apply("base", "Retain") == "niateR#"


def test_typed_cross_domain_campaign_passes_with_regression_protection() -> None:
    report = run_typed_cross_domain_campaign("typed-cross-domain-seed")

    assert report["passed"] is True
    assert report["evidence_tier"] == "development"
    assert all(report["raw_budget_failures"].values())
    assert report["rejected_bad_update"] is True
    assert report["protected_retention"] is True
    assert len(report["task_results"]) == 6
    assert all(item["success"] for item in report["task_results"])
    assert report["regression_decision"]["adopt_candidate"] is True
    assert report["regression_decision"]["protected_score_drops"] == []
    assert report["regression_decision"]["target_repeats"] == {
        "text-to-chars": 2,
        "text-to-length": 2,
        "text-to-unique-chars": 2,
    }
    skills = {item["skill_id"]: item for item in report["skills"]}
    assert skills["text-to-length"]["output_domain"] == "numeric"
    assert skills["text-to-chars"]["output_domain"] == "sequence"
    assert "skill:text-to-chars" in skills["text-to-unique-chars"]["terms"]
    assert "not open-ended learning or AGI" in report["claim_boundary"]
