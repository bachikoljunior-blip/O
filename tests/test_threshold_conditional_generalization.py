from __future__ import annotations

import pytest

from agi.acquired_programs import AcquiredProgramError, ProgramExample, synthesize_program


def test_shifted_threshold_requires_counterexample_refinement_without_new_operator() -> None:
    demonstrations = [
        ProgramExample(-5, 2),
        ProgramExample(0, 2),
        ProgramExample(2, 2),
        ProgramExample(4, 4),
        ProgramExample(9, 9),
    ]

    with pytest.raises(AcquiredProgramError, match="no bounded pure typed program"):
        synthesize_program(
            input_domain="numeric",
            output_domain="numeric",
            examples=demonstrations,
            max_nodes=7,
            allow_conditionals=False,
        )

    first = synthesize_program(
        input_domain="numeric",
        output_domain="numeric",
        examples=demonstrations,
        max_nodes=7,
        allow_conditionals=True,
    )
    assert first.apply(1) == 1
    assert first.apply(1) != 2

    first_counterexample = ProgramExample(1, 2)
    second = synthesize_program(
        input_domain="numeric",
        output_domain="numeric",
        examples=[*demonstrations, first_counterexample],
        max_nodes=7,
        allow_conditionals=True,
    )
    assert second.apply(3) == 2
    assert second.apply(3) != 3

    second_counterexample = ProgramExample(3, 3)
    third = synthesize_program(
        input_domain="numeric",
        output_domain="numeric",
        examples=[*demonstrations, first_counterexample, second_counterexample],
        max_nodes=7,
        allow_conditionals=True,
    )
    assert third.apply(2.5) == 2
    assert third.apply(2.5) != 2.5

    third_counterexample = ProgramExample(2.5, 2.5)
    refined = synthesize_program(
        input_domain="numeric",
        output_domain="numeric",
        examples=[
            *demonstrations,
            first_counterexample,
            second_counterexample,
            third_counterexample,
        ],
        max_nodes=7,
        allow_conditionals=True,
    )
    heldout = [(-100, 2), (-1, 2), (2.25, 2.25), (5, 5), (15, 15), (100, 100)]
    assert [refined.apply(value) for value, _ in heldout] == [expected for _, expected in heldout]
    assert refined.expression == {
        "op": "if_nonnegative",
        "condition": {
            "op": "add",
            "left": {"op": "input"},
            "right": {"op": "const", "domain": "numeric", "value": -2},
        },
        "then": {"op": "input"},
        "else": {"op": "const", "domain": "numeric", "value": 2},
    }
