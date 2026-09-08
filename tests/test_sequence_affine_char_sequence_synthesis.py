from __future__ import annotations

import pytest

from agi.acquired_programs import AcquiredProgramError, ProgramExample, synthesize_program


def _chars(value: int) -> list[str]:
    return list(str(value))


def test_sequence_affine_char_sequence_is_derived_after_legacy_frontier() -> None:
    examples = tuple(
        ProgramExample(value, _chars(2 * sum(value) + 1))
        for value in ([], [1, -1], [2, -3, 1], [1], [1, -1, 1], [1, 1], [-2, 0, 0])
    )
    program = synthesize_program(
        input_domain="sequence",
        output_domain="sequence",
        examples=examples,
        max_nodes=8,
        allow_sequence_folds=True,
        allow_conditionals=True,
        allow_structured_ops=True,
        allow_scalar_equality=True,
        allow_observed_constants=True,
        allow_object_construction=True,
    )
    assert program.expression["op"] == "chars"
    for value in ([3], [-1], [4, -2, 1], [-5, 2]):
        assert program.apply(value) == _chars(2 * sum(value) + 1)


def test_sequence_affine_char_fallback_does_not_accept_noncanonical_sequence_output() -> None:
    examples = (
        ProgramExample([], ["01"]),
        ProgramExample([1], ["03"]),
        ProgramExample([2], ["05"]),
    )
    with pytest.raises(AcquiredProgramError):
        synthesize_program(
            input_domain="sequence",
            output_domain="sequence",
            examples=examples,
            max_nodes=8,
        )
