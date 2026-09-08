from __future__ import annotations

from agi.acquired_programs import ProgramExample, synthesize_program


def test_sequence_affine_string_synthesis_admits_derived_coefficient_six() -> None:
    examples = [
        ProgramExample([], "2"),
        ProgramExample([1, -1], "2"),
        ProgramExample([2, -3, 1], "2"),
        ProgramExample([1], "8"),
        ProgramExample([-3, 0, 0], "-16"),
        ProgramExample([-2, 0, 0], "-10"),
        ProgramExample([2, 0, 0], "14"),
    ]

    learned = synthesize_program(
        input_domain="sequence",
        output_domain="string",
        examples=examples,
        max_nodes=9,
        allow_sequence_folds=True,
        allow_conditionals=True,
    )

    assert learned.apply([3, -1]) == "14"
    assert learned.apply([-4, 1]) == "-16"
    assert learned.apply([5, -2, 1]) == "26"
