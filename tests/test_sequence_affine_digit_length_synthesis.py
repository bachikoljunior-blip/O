from __future__ import annotations

from agi.acquired_programs import ProgramExample
from agi.heterogeneous_retention_campaign import _bounded_sequence_fold_affine_digit_length


def _digit_length(value: int) -> int:
    return len(str(value))


def test_sequence_affine_digit_length_fallback_recovers_retained_counterexample() -> None:
    examples = tuple(
        ProgramExample(value, _digit_length(2 * sum(value) + 1))
        for value in (
            [],
            [1, -1],
            [2, -3, 1],
            [1],
            [1, -1, 1],
            [1, 1],
            [-2, 0, 0],
            [-8, 0, 0],
            [-7, 0, 0],
        )
    )

    learned = _bounded_sequence_fold_affine_digit_length(
        examples=examples,
        max_nodes=9,
    )

    assert learned is not None
    assert learned.expression["op"] == "length_string"
    assert learned.descriptor()["max_steps"] <= 128
    for value in ([3], [-1], [4, -2, 1], [-5, 2], [8, 0, 0]):
        assert learned.apply(value) == _digit_length(2 * sum(value) + 1)


def test_sequence_affine_digit_length_fallback_keeps_nonmatching_behavior_fail_closed() -> None:
    examples = tuple(
        ProgramExample([value], output)
        for value, output in zip(
            range(-4, 5),
            (1, 2, 1, 2, 1, 2, 1, 2, 1),
            strict=True,
        )
    )

    learned = _bounded_sequence_fold_affine_digit_length(
        examples=examples,
        max_nodes=9,
    )

    assert learned is None
