from __future__ import annotations

import pytest

from agi.induction import Demonstration, InductionError
from agi.typed_composition import TypedCompositionalLearner, run_typed_cross_domain_campaign


def _base(value: str) -> str:
    return value[::-1] + "#"


def _chars(value: str) -> list[str]:
    return list(_base(value))


def _unique(value: str) -> list[str]:
    return list(dict.fromkeys(_base(value)))


def test_learned_macro_crosses_string_to_sequence_with_same_term_budget() -> None:
    base_demos = [
        Demonstration("Abc", _base("Abc")),
        Demonstration("Model", _base("Model")),
        Demonstration("Q7x", _base("Q7x")),
    ]
    chars_demos = [
        Demonstration("Abc", _chars("Abc")),
        Demonstration("Model", _chars("Model")),
        Demonstration("Q7x", _chars("Q7x")),
    ]

    raw = TypedCompositionalLearner()
    with pytest.raises(InductionError, match="no well-typed composition"):
        raw.learn("chars", "string", chars_demos, max_terms=2, allow_macros=False)

    learner = TypedCompositionalLearner()
    base = learner.learn("base", "string", base_demos, max_terms=2, allow_macros=False)
    learned = learner.learn("chars", "string", chars_demos, max_terms=2, allow_macros=True)

    assert base.output_domain == "string"
    assert learned.output_domain == "sequence"
    assert learned.expanded_primitives == (
        "string.reverse",
        "string.append_hash",
        "string.to_chars",
    )
    assert "skill:base" in learned.terms
    assert learner.apply("chars", "Transfer") == _chars("Transfer")


def test_recursive_macro_reuse_reaches_numeric_output_at_primitive_depth_five() -> None:
    learner = TypedCompositionalLearner()
    learner.learn(
        "base",
        "string",
        [
            Demonstration("Abc", _base("Abc")),
            Demonstration("ModelM", _base("ModelM")),
            Demonstration("LevelX", _base("LevelX")),
        ],
        max_terms=2,
        allow_macros=False,
    )
    learner.learn(
        "chars",
        "string",
        [
            Demonstration("Abc", _chars("Abc")),
            Demonstration("ModelM", _chars("ModelM")),
            Demonstration("LevelX", _chars("LevelX")),
        ],
        max_terms=2,
        allow_macros=True,
    )
    unique = learner.learn(
        "unique",
        "string",
        [
            Demonstration("Aba", _unique("Aba")),
            Demonstration("ModelM", _unique("ModelM")),
            Demonstration("LevelX", _unique("LevelX")),
            Demonstration("Mississippi", _unique("Mississippi")),
        ],
        max_terms=2,
        allow_macros=True,
    )
    assert len(unique.expanded_primitives) == 4
    assert "skill:chars" in unique.terms

    count_demos = [
        Demonstration("Aba", len(_unique("Aba"))),
        Demonstration("ModelM", len(_unique("ModelM"))),
        Demonstration("LevelX", len(_unique("LevelX"))),
        Demonstration("Mississippi", len(_unique("Mississippi"))),
    ]
    raw = TypedCompositionalLearner()
    with pytest.raises(InductionError, match="no well-typed composition"):
        raw.learn("count", "string", count_demos, max_terms=2, allow_macros=False)

    count = learner.learn("count", "string", count_demos, max_terms=2, allow_macros=True)
    assert count.output_domain == "numeric"
    assert len(count.expanded_primitives) == 5
    assert count.expanded_primitives[-1] == "sequence.length"
    assert "skill:unique" in count.terms
    assert learner.apply("count", "Bookkeeper") == len(_unique("Bookkeeper"))


def test_failed_typed_update_preserves_existing_skill() -> None:
    learner = TypedCompositionalLearner()
    first = learner.learn(
        "base",
        "string",
        [Demonstration("Ab", _base("Ab")), Demonstration("Cat", _base("Cat"))],
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
    assert learner.apply("base", "Retain") == _base("Retain")


def test_typed_cross_domain_campaign_passes_with_regression_protection() -> None:
    report = run_typed_cross_domain_campaign("typed-cross-domain-seed")

    assert report["passed"] is True
    assert report["evidence_tier"] == "development"
    assert all(report["raw_budget_failures"].values())
    assert report["rejected_bad_update"] is True
    assert report["protected_retention"] is True
    assert len(report["task_results"]) == 8
    assert all(item["success"] for item in report["task_results"])
    assert report["regression_decision"]["adopt_candidate"] is True
    assert report["regression_decision"]["protected_score_drops"] == []
    assert report["regression_decision"]["target_repeats"] == {
        "text-to-chars": 2,
        "text-to-unique-chars": 2,
        "text-to-unique-count": 2,
        "text-to-unique-string": 2,
    }
    skills = {item["skill_id"]: item for item in report["skills"]}
    assert skills["text-to-chars"]["output_domain"] == "sequence"
    assert skills["text-to-unique-count"]["output_domain"] == "numeric"
    assert skills["text-to-unique-string"]["output_domain"] == "string"
    assert "skill:text-to-unique-chars" in skills["text-to-unique-count"]["terms"]
    assert "skill:text-to-unique-chars" in skills["text-to-unique-string"]["terms"]
    assert "not open-ended learning or AGI" in report["claim_boundary"]
