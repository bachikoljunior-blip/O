from __future__ import annotations

import copy
from pathlib import Path

import pytest

import continual.engine as engine_module
from agi.observed_schema_extrapolation import (
    ObservedSchemaExtrapolationError,
    _digest,
    _precommit_observed_schema_schedule,
    _reconstruct_observed_targets,
    _shape,
)
from agi.acquired_programs import execute_program, validate_program_descriptor
from agi.recursive_evidence_frontier_growth import run_recursive_evidence_frontier_growth


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


def test_observed_schema_binding_diagnostic_core_has_no_literal_variation_and_fails_closed(
    runtime_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Executable negative evidence for observed-schema extrapolation on the current chain.

    Reconstruction rebinds to persisted iterated evidence without caller Candidate IDs, but the
    deterministically reconstructible learned programs (both controls and both expansion targets)
    have pairwise-distinct AST shapes, so there is no observed same-shape literal delta to continue
    and the precommitted schedule must fail closed. The recursive member is excluded from these
    assertions because the recursive campaign selects its target among bounded-search "unsupported"
    schedule entries, and that support verdict varies across processes with identical seeds
    (hash-seed dependence; observed live as sub- versus mul-wrap targets), which would make any
    assertion over the full campaign outcome a lottery. Fail-closed outcome demonstrated end to end
    by CI run 32607076987 and local reproductions on heads 2376ed2 and a097ab1.
    """
    # Runtime calls in the prerequisite chain are exact mechanical learned-tool invocations.
    # Ordinary CI must not require live-model credentials merely to construct the Engine for them.
    monkeypatch.setattr(engine_module, "ModelClient", lambda root: object())

    seed = "observed-schema-end-to-end-binding-test"
    recursive_seed = f"{seed}:recursive"
    recursive = run_recursive_evidence_frontier_growth(runtime_repo, recursive_seed)
    assert recursive["passed"] is True
    assert recursive["all_five_capabilities_rediscovered"] is True

    observed = _reconstruct_observed_targets(
        runtime_repo,
        recursive_seed=recursive_seed,
        recursive_report=recursive,
    )
    deterministic_core = observed["observed_targets"][:4]
    assert len(deterministic_core) == 4
    shape_digests = {
        _digest(_shape(target["program"]["expression"])) for target in deterministic_core
    }
    assert len(shape_digests) == 4

    history_digest = _digest(
        {
            "observed_program_digests": [_digest(item["program"]) for item in deterministic_core],
            "phase": "observed-schema-binding-diagnostic-v1",
        }
    )
    with pytest.raises(
        ObservedSchemaExtrapolationError,
        match="fewer than two novel extrapolated behaviors",
    ):
        _precommit_observed_schema_schedule(deterministic_core, history_digest=history_digest)
