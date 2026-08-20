from __future__ import annotations

from pathlib import Path

import pytest

import continual.engine as engine_module
from agi.acquired_programs import execute_program, validate_program_descriptor
from agi.heterogeneous_retention_campaign import _digest
from agi.iterated_observed_schema_extrapolation import (
    _precommit_second_schema_schedule,
    run_iterated_observed_schema_extrapolation,
)
from agi.observed_schema_extrapolation import (
    _precommit_observed_schema_schedule,
    _target_from_program,
)


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
        "semantic_signature": _digest(
            {
                "input_domain": "numeric",
                "output_domain": "numeric",
                "support_examples": [
                    {"input": value, "output": execute_program(program, value)} for value in inputs
                ],
                "phase": "observed-schema-extrapolation-support-v1",
            }
        ),
    }


def _offset(target: dict) -> int:
    return int(target["program"]["expression"]["right"]["value"])


def test_second_schema_schedule_requires_newly_learned_program_lineage() -> None:
    original = [_target(1), _target(3)]
    first_history = "1" * 64
    first_schedule = _precommit_observed_schema_schedule(
        original,
        history_digest=first_history,
    )
    first_selected = next(
        item for item in first_schedule["target_schedule"] if _offset(item) == 5
    )
    learned_program = _affine_program(5)
    learned_target = _target_from_program(
        learned_program,
        tuple(item["input"] for item in first_selected["support_examples"]),
    )
    first_report = {
        "history_digest": first_history,
        "schedule_commitment": first_schedule["schedule_commitment"],
        "selected_semantic_signature": first_selected["semantic_signature"],
        "new_candidate_id": "candidate-first-schema",
        "digest": "2" * 64,
    }

    second = _precommit_second_schema_schedule(
        [*original, learned_target],
        first_learned_target=learned_target,
        first_report=first_report,
    )

    assert len(second["target_schedule"]) >= 2
    first_program_digest = _digest(learned_program)
    first_stage_signatures = {
        str(item["semantic_signature"]) for item in first_schedule["target_schedule"]
    }
    assert all(
        first_program_digest
        in {
            str(item["lineage"]["left_program_digest"]),
            str(item["lineage"]["right_program_digest"]),
        }
        for item in second["target_schedule"]
    )
    assert all(
        str(item["semantic_signature"]) not in first_stage_signatures
        for item in second["target_schedule"]
    )
    offsets = {_offset(item) for item in second["target_schedule"]}
    assert 7 in offsets
    assert len(offsets) >= 2


def test_iterated_observed_schema_extrapolation_learns_from_first_learned_program(
    runtime_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Runtime replay is mechanical learned-tool execution; ordinary CI must not require live provider
    # credentials merely to construct Engines used by the persisted Candidate invocations.
    monkeypatch.setattr(engine_module, "ModelClient", lambda root: object())

    report = run_iterated_observed_schema_extrapolation(
        runtime_repo,
        "iterated-observed-schema-end-to-end-test",
    )

    assert report["passed"] is True
    assert report["second_schedule_precommitted_before_support_checks"] is True
    assert report["second_proposals_require_first_learned_program_lineage"] is True
    assert report["selected_depends_on_first_learned_program"] is True
    assert report["control_depends_on_first_learned_program"] is True
    assert report["selected_failed_closed_before_learning"] is True
    assert report["control_remained_failed_closed"] is True
    assert report["new_candidate_negative_evidence_retained"] is True
    assert report["prior_candidate_state_unchanged"] is True
    assert report["prior_trial_state_unchanged"] is True
    assert report["all_seven_capabilities_rediscovered"] is True
    assert report["all_replays_avoided_caller_candidate_ids"] is True
    assert "independent production evidence" in report["claim_boundary"]
    assert "AGI" in report["claim_boundary"]
