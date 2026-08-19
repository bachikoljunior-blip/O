from __future__ import annotations

import pytest

import agi.heterogeneous_retention_campaign as campaign
from agi.acquired_programs import AcquiredProgramError, ProgramExample


def test_sequence_affine_to_string_fallback_retains_observed_behavior(monkeypatch) -> None:
    examples = (
        ProgramExample([], "1"),
        ProgramExample([1, -1], "1"),
        ProgramExample([2, -3, 1], "1"),
        ProgramExample([1], "-2"),
        ProgramExample([2, 0, 0], "-5"),
        ProgramExample([3, 0, 0], "-8"),
    )

    def fail_primary(**_kwargs):
        raise AcquiredProgramError("forced primary-search frontier miss")

    monkeypatch.setattr(campaign, "synthesize_program", fail_primary)
    learned = campaign._synthesize_task_program(
        {
            "name": "sequence-affine-string-regression",
            "input_domain": "sequence",
            "output_domain": "string",
            "examples": examples,
            "probe": [4, 0, 0],
            "expected": "-11",
            "max_nodes": 8,
        }
    )

    assert [learned.apply(item.input) for item in examples] == [item.output for item in examples]
    assert learned.apply([4, 0, 0]) == "-11"
    assert learned.descriptor()["input_domain"] == "sequence"
    assert learned.descriptor()["output_domain"] == "string"
    assert learned.descriptor()["effects"] == ()


def test_sequence_affine_fallback_does_not_expand_unrelated_domains(monkeypatch) -> None:
    def fail_primary(**_kwargs):
        raise AcquiredProgramError("forced primary-search frontier miss")

    monkeypatch.setattr(campaign, "synthesize_program", fail_primary)
    with pytest.raises(AcquiredProgramError, match="forced primary-search frontier miss"):
        campaign._synthesize_task_program(
            {
                "name": "numeric-control",
                "input_domain": "numeric",
                "output_domain": "numeric",
                "examples": (
                    ProgramExample(1, 2),
                    ProgramExample(2, 3),
                    ProgramExample(3, 4),
                ),
                "probe": 4,
                "expected": 5,
                "max_nodes": 8,
            }
        )
