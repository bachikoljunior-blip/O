from __future__ import annotations

import json
from pathlib import Path

from agi.acquired_programs import ProgramExample
from agi.heterogeneous_retention_campaign import (
    _bounded_sequence_fold_affine_to_chars,
    _bounded_sequence_fold_affine_to_string,
    run_heterogeneous_retention_campaign,
)


def test_sequence_affine_fallback_accounts_for_fold_runtime_steps() -> None:
    examples = (
        ProgramExample([], "2"),
        ProgramExample([1], "5"),
        ProgramExample([1, -1], "2"),
        ProgramExample([1] * 20, "62"),
        ProgramExample([-1] * 12, "-34"),
    )

    learned = _bounded_sequence_fold_affine_to_string(
        examples=examples,
        max_nodes=8,
    )

    assert learned is not None
    descriptor = learned.descriptor()
    assert descriptor["max_steps"] == 28
    assert descriptor["max_steps"] <= 128
    assert all(learned.apply(item.input) == item.output for item in examples)


def test_sequence_affine_fallback_composes_abs_before_affine() -> None:
    examples = (
        ProgramExample([], "2"),
        ProgramExample([1], "5"),
        ProgramExample([-1], "5"),
        ProgramExample([1, -1], "2"),
        ProgramExample([-2, 1], "5"),
        ProgramExample([2, -5], "11"),
    )

    learned = _bounded_sequence_fold_affine_to_string(
        examples=examples,
        max_nodes=8,
    )

    assert learned is not None
    assert all(learned.apply(item.input) == item.output for item in examples)


def test_sequence_affine_fallback_composes_reverse_after_to_string() -> None:
    examples = (
        ProgramExample([], "2"),
        ProgramExample([1], "5"),
        ProgramExample([-1], "1-"),
        ProgramExample([4], "41"),
        ProgramExample([-2], "4-"),
        ProgramExample([1, -1], "2"),
    )

    learned = _bounded_sequence_fold_affine_to_string(
        examples=examples,
        max_nodes=8,
    )

    assert learned is not None
    assert learned.expression.get("op") == "reverse"
    assert all(learned.apply(item.input) == item.output for item in examples)


def test_sequence_affine_char_fallback_composes_reverse_before_chars() -> None:
    examples = (
        ProgramExample([], ["1"]),
        ProgramExample([1, -1], ["1"]),
        ProgramExample([2, -3, 1], ["1"]),
        ProgramExample([-8, 0, 0], ["3", "2", "-"]),
        ProgramExample([-7, 0, 0], ["0", "2", "-"]),
        ProgramExample([4], ["3", "1"]),
    )

    learned = _bounded_sequence_fold_affine_to_chars(
        examples=examples,
        max_nodes=9,
    )

    assert learned is not None
    assert learned.expression.get("op") == "chars"
    assert learned.expression["arg"].get("op") == "reverse"
    assert all(learned.apply(item.input) == item.output for item in examples)


def test_heterogeneous_capabilities_accumulate_without_forgetting(
    runtime_repo: Path,
) -> None:
    report = run_heterogeneous_retention_campaign(
        runtime_repo,
        "heterogeneous-retention-seed",
    )

    assert report["passed"] is True
    assert report["capability_count"] == 4
    assert report["input_domains"] == ["numeric", "string", "sequence", "object"]
    assert report["output_domains"] == ["numeric", "string", "numeric", "boolean"]
    assert len(set(report["candidate_ids"])) == 4
    assert len(set(report["scopes"])) == 4
    assert len(set(report["fresh_engine_runs"])) == 4
    assert report["fresh_engine_count"] == 4
    assert report["cumulative_replay_invocation_count"] == 10
    assert report["all_negative_evidence_retained"] is True
    assert report["candidate_trial_state_unchanged_after_later_learning"] is True
    assert report["all_fresh_engine_replays_matched"] is True
    assert report["live_model_invocation_required"] is False
    assert all(count > 0 for count in report["candidate_trial_file_counts"].values())

    evidence_dir = runtime_repo / ".continual" / "evidence" / "heterogeneous-retention"
    files = list(evidence_dir.glob("heterogeneous-retention-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
