from __future__ import annotations

import pytest

from agi.acquired_programs import AcquiredProgramError, ProgramExample, synthesize_program


def test_existing_arithmetic_and_conditional_kernels_learn_unseen_shifted_threshold_without_new_operator() -> None:
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

    learned = synthesize_program(
        input_domain="numeric",
        output_domain="numeric",
        examples=demonstrations,
        max_nodes=7,
        allow_conditionals=True,
    )
    heldout = [(-100, 2), (-1, 2), (1, 2), (3, 3), (15, 15), (100, 100)]
    assert [learned.apply(value) for value, _ in heldout] == [expected for _, expected in heldout]
    assert learned.expression == {
        "op": "if_nonnegative",
        "condition": {
            "op": "add",
            "left": {"op": "input"},
            "right": {"op": "const", "domain": "numeric", "value": -2},
        },
        "then": {"op": "input"},
        "else": {"op": "const", "domain": "numeric", "value": 2},
    }
