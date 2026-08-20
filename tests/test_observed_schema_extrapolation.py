from __future__ import annotations

import copy

import pytest

from agi.observed_schema_extrapolation import (
    ObservedSchemaExtrapolationError,
    _precommit_observed_schema_schedule,
    _shape,
)
from agi.acquired_programs import execute_program, validate_program_descriptor


def _affine_program(offset: int) -> dict:
    program = {
        "input_domain": "numeric",
        "output_domain": "numeric",
        "expression": {
            "op": "add",
            "left": {
                "op": "mul",
                "left": {"op": "input"},
                "right": {"op": "const", "domain": "numeric", "value": 2},
            },
            "right": {"op": "const", "domain": "numeric", "value": offset},
        },
        "effects": [],
        "max_steps": 64,
        "max_output_length": 1024,
    }
    validate_program_descriptor(program)
    return program


def _target(offset: int) -> dict:
    program = _affine_program(offset)
    inputs = (-3, 0, 4, 8)
    return {
        "program": program,
        "input_domain": "numeric",
        "output_domain": "numeric",
        "program_nodes": 5,
        "support_examples": [
            {"input": value, "output": execute_program(program, value)} for value in inputs
        ],
    }


def _offset(program: dict) -> int:
    return int(program["expression"]["right"]["value"])


def test_observed_schema_schedule_extrapolates_only_observed_literal_delta() -> None:
    observed = [_target(1), _target(3)]
    schedule = _precommit_observed_schema_schedule(observed, history_digest="1" * 64)

    assert len(schedule["target_schedule"]) >= 2
    offsets = {_offset(item["program"]) for item in schedule["target_schedule"]}
    assert {-1, 5}.issubset(offsets)
    assert 1 not in offsets
    assert 3 not in offsets

    base_shape = _shape(observed[0]["program"]["expression"])
    for item in schedule["target_schedule"]:
        assert _shape(item["program"]["expression"]) == base_shape
        validate_program_descriptor(item["program"])
        lineage = item["lineage"]
        assert lineage["literal_extrapolations"]
        assert all(abs(int(step["observed_delta"])) == 2 for step in lineage["literal_extrapolations"])


def test_observed_schema_schedule_is_deterministic_and_does_not_mutate_sources() -> None:
    observed = [_target(-4), _target(2)]
    before = copy.deepcopy(observed)

    first = _precommit_observed_schema_schedule(observed, history_digest="a" * 64)
    second = _precommit_observed_schema_schedule(observed, history_digest="a" * 64)

    assert first == second
    assert first["schedule_commitment"] == second["schedule_commitment"]
    assert observed == before


def test_observed_schema_schedule_fails_closed_without_observed_parameter_variation() -> None:
    observed = [_target(2), copy.deepcopy(_target(2))]

    with pytest.raises(
        ObservedSchemaExtrapolationError,
        match="fewer than two novel extrapolated behaviors",
    ):
        _precommit_observed_schema_schedule(observed, history_digest="f" * 64)
