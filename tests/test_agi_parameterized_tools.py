from __future__ import annotations

import pytest

from agi.induction import Demonstration, InductionError
from agi.parameterized_tools import ParameterizedToolLearner, run_parameterized_tool_campaign


def test_numeric_tool_learns_exact_affine_parameters_from_examples() -> None:
    learner = ParameterizedToolLearner()
    tool = learner.learn(
        "affine-test",
        "numeric",
        [
            Demonstration(-2, -11),
            Demonstration(3, 4),
            Demonstration(8, 19),
        ],
    )

    assert tool.family == "affine"
    assert tool.parameters == {"a": 3, "b": -5}
    assert learner.apply("affine-test", 21) == 58


def test_string_literal_is_acquired_instead_of_pre_enumerated() -> None:
    learner = ParameterizedToolLearner()
    tool = learner.learn(
        "reverse-with-learned-suffix",
        "string",
        [
            Demonstration("Abc", "cbA::novel"),
            Demonstration("Model", "ledoM::novel"),
            Demonstration("Q7", "7Q::novel"),
        ],
    )

    assert tool.family == "reverse_suffix"
    assert tool.parameters == {"suffix": "::novel"}
    assert learner.apply("reverse-with-learned-suffix", "Transfer") == "refsnarT::novel"


def test_failed_update_preserves_previous_parameterized_tool() -> None:
    learner = ParameterizedToolLearner()
    first = learner.learn(
        "numeric",
        "numeric",
        [Demonstration(0, 4), Demonstration(2, 10), Demonstration(5, 19)],
    )
    before = learner.tools

    with pytest.raises(InductionError):
        learner.learn(
            "numeric",
            "numeric",
            [Demonstration(1, 1), Demonstration(2, 2), Demonstration(3, 999)],
        )

    assert learner.tools == before == (first,)
    assert learner.apply("numeric", 9) == 31


def test_snapshot_roundtrip_keeps_tools_executable() -> None:
    learner = ParameterizedToolLearner()
    learner.learn(
        "sequence",
        "sequence",
        [
            Demonstration([1, 2, 3, 4, 5], [3, 4, 5, 1, 2]),
            Demonstration([8, 4, 7, 1, 9, 2], [7, 1, 9, 2, 8, 4]),
            Demonstration([3, 9, 1, 8, 2, 7, 5], [1, 8, 2, 7, 5, 3, 9]),
        ],
    )
    snapshot = learner.snapshot()
    restored = ParameterizedToolLearner.from_snapshot(snapshot)

    assert restored.snapshot() == snapshot
    assert restored.apply("sequence", [9, 8, 7, 6, 5, 4]) == [7, 6, 5, 4, 9, 8]


def test_seeded_campaign_varies_hidden_tool_families_and_parameters() -> None:
    reports = [
        run_parameterized_tool_campaign(seed)
        for seed in ("param-a", "param-b", "param-c", "param-d")
    ]

    signatures = {
        tuple(
            (tool["domain"], tool["family"], tuple(sorted(tool["parameters"].items())))
            for tool in report["acquired_tools"]
        )
        for report in reports
    }
    assert len(signatures) == 4
    assert len({tuple(sorted(report["hidden_program_commitments"].items())) for report in reports}) == 4


def test_parameterized_campaign_passes_heldout_regression_and_retention() -> None:
    report = run_parameterized_tool_campaign("campaign-seed")

    assert report["passed"] is True
    assert report["evidence_tier"] == "development"
    assert report["snapshot_reload_verified"] is True
    assert report["rejected_bad_update"] is True
    assert report["protected_retention"] is True
    assert len(report["task_results"]) == 6
    assert all(item["success"] for item in report["task_results"])
    assert report["regression_decision"]["adopt_candidate"] is True
    assert report["regression_decision"]["protected_score_drops"] == []
    assert report["regression_decision"]["target_repeats"] == {
        "numeric-tool": 2,
        "sequence-tool": 2,
        "string-tool": 2,
    }
    assert "not open-ended code synthesis or AGI" in report["claim_boundary"]
