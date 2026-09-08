from __future__ import annotations

import copy

import pytest

from agi.recursive_evidence_frontier_growth import (
    RecursiveEvidenceFrontierGrowthError,
    _recursive_history_commitment,
)


def _iterated_report(**overrides: object) -> dict:
    report = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "evidence-frontier-iterated-expansion-v1",
        "seed": "determinism-test:iterated",
        "first_candidate_id": "hetero-evidence-frontier-aaaa-1111",
        "second_candidate_id": "hetero-evidence-frontier-bbbb-2222",
        "first_expansion_candidate_id": "hetero-evidence-frontier-expanded-cccc-3333",
        "second_expansion_candidate_id": "hetero-evidence-frontier-r2-dddd-4444",
        "second_expansion_semantic_signature": "5" * 64,
        # Per-process fields that the v1 commitment leaked through and v2 must ignore.
        "digest": "6" * 64,
        "first_selected_behavior_replay": {"fresh_engine_run_ids": ["run-aaaaaaaaaaaa"]},
        "transactional_commit": {"trial_snapshot": {"recorded_at": "2026-08-23T00:00:00Z"}},
    }
    report.update(overrides)
    return report


def _learned_target() -> dict:
    return {
        "semantic_signature": "7" * 64,
        "program": {
            "input_domain": "string",
            "output_domain": "numeric",
            "expression": {"op": "length_string", "arg": {"op": "input"}},
            "effects": [],
            "max_steps": 64,
            "max_output_length": 1024,
        },
    }


def test_commitment_ignores_per_process_report_entropy() -> None:
    base = _iterated_report()
    noisy = _iterated_report(
        digest="f" * 64,
        first_selected_behavior_replay={"fresh_engine_run_ids": ["run-zzzzzzzzzzzz"]},
        transactional_commit={"trial_snapshot": {"recorded_at": "2026-08-23T23:59:59Z"}},
    )

    first = _recursive_history_commitment(iterated_report=base, learned_target=_learned_target())
    second = _recursive_history_commitment(iterated_report=noisy, learned_target=_learned_target())

    assert first == second
    assert len(first) == 64


def test_commitment_binds_every_stable_semantic_field() -> None:
    base_commitment = _recursive_history_commitment(
        iterated_report=_iterated_report(), learned_target=_learned_target()
    )

    for field, changed in (
        ("seed", "determinism-test-changed:iterated"),
        ("first_candidate_id", "hetero-evidence-frontier-aaaa-9999"),
        ("second_candidate_id", "hetero-evidence-frontier-bbbb-9999"),
        ("first_expansion_candidate_id", "hetero-evidence-frontier-expanded-cccc-9999"),
        ("second_expansion_candidate_id", "hetero-evidence-frontier-r2-dddd-9999"),
        ("second_expansion_semantic_signature", "8" * 64),
    ):
        changed_commitment = _recursive_history_commitment(
            iterated_report=_iterated_report(**{field: changed}),
            learned_target=_learned_target(),
        )
        assert changed_commitment != base_commitment, field

    changed_target = _learned_target()
    changed_target["program"] = copy.deepcopy(changed_target["program"])
    changed_target["program"]["expression"] = {
        "op": "neg",
        "arg": {"op": "length_string", "arg": {"op": "input"}},
    }
    assert (
        _recursive_history_commitment(
            iterated_report=_iterated_report(), learned_target=changed_target
        )
        != base_commitment
    )


def test_commitment_fails_closed_without_stable_semantics() -> None:
    for missing in (
        "seed",
        "campaign_kind",
        "first_candidate_id",
        "second_candidate_id",
        "first_expansion_candidate_id",
        "second_expansion_candidate_id",
        "second_expansion_semantic_signature",
    ):
        report = _iterated_report()
        del report[missing]
        with pytest.raises(
            RecursiveEvidenceFrontierGrowthError,
            match="stable recursive-growth commitments",
        ):
            _recursive_history_commitment(
                iterated_report=report, learned_target=_learned_target()
            )

    with pytest.raises(
        RecursiveEvidenceFrontierGrowthError,
        match="stable recursive-growth commitments",
    ):
        _recursive_history_commitment(
            iterated_report=_iterated_report(),
            learned_target={"semantic_signature": "7" * 64},
        )
