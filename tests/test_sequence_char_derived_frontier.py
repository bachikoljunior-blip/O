from __future__ import annotations

from agi.acquired_programs import ProgramExample, synthesize_program
from agi.crossdomain_failure_derived_target import (
    _candidate_expressions,
    _precommit_schedule,
    _target_from_expression,
)


def _chars(value: int) -> list[str]:
    return list(str(value))


def test_sequence_char_frontier_extends_only_after_legacy_derivation_exhausts() -> None:
    support_inputs = ([], [1, -1], [2, -3, 1], [1], [1, -1, 1], [1, 1], [-2, 0, 0])
    examples = tuple(
        ProgramExample(value, _chars(2 * sum(value) + 1))
        for value in support_inputs
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
    ).descriptor()

    history_digest = "0" * 64
    legacy = []
    for expression in _candidate_expressions(program, 0):
        try:
            target = _target_from_expression(program, expression, support_inputs)
        except (ValueError, TypeError):
            continue
        if int(target["program_nodes"]) <= 9:
            legacy.append(target)
    assert len({target["semantic_signature"] for target in legacy}) < 2

    schedule = _precommit_schedule(
        program,
        history_digest=history_digest,
        support_inputs=support_inputs,
    )
    assert len(schedule["target_schedule"]) >= 2
    assert all(int(target["program_nodes"]) <= 9 for target in schedule["target_schedule"])

    char_targets = [
        target
        for target in schedule["target_schedule"]
        if target["program"]["expression"].get("op") == "chars"
    ]
    assert len(char_targets) >= 2
    for target in char_targets:
        target_examples = tuple(
            ProgramExample(item["input"], item["output"])
            for item in target["support_examples"]
        )
        learned = synthesize_program(
            input_domain=str(target["input_domain"]),
            output_domain=str(target["output_domain"]),
            examples=target_examples,
            max_nodes=int(target["program_nodes"]),
            allow_sequence_folds=True,
            allow_conditionals=True,
            allow_structured_ops=True,
            allow_scalar_equality=True,
            allow_observed_constants=True,
            allow_object_construction=True,
        )
        assert all(learned.apply(item.input) == item.output for item in target_examples)
