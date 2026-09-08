from __future__ import annotations

from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.heterogeneous_retention_campaign import _digest, _task_specs
from agi.materialized_runtime_replay import (
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
    _validate_runtime_output,
)
from agi.multiround_disambiguation_bank import _target_answer
from agi.recursive_memory_skill_composition import run_recursive_memory_skill_composition
from agi.recursive_skill_postlearning_retention import (
    _candidate_spec,
    _trial_snapshots,
    run_recursive_skill_postlearning_retention,
)


class InterleavedCrossFamilyRetentionError(RuntimeError):
    pass


def run_interleaved_cross_family_retention(root: Path, seed: str) -> dict[str, Any]:
    """Falsify forgetting across recursive→heterogeneous→recursive learning stages.

    Stage one learns and regression-promotes a second-generation recursive memory-derived skill, then
    performs unrelated heterogeneous numeric/string/sequence/object learning while retaining the first
    recursive skill. Stage two performs a fresh second recursive-memory learning campaign. All stage-one
    Candidate trial ledgers are frozen before stage two. Afterwards the first recursive skill, all four
    heterogeneous capabilities, and the new recursive skill must all replay correctly in distinct fresh
    Engines while neither stage's durable regression evidence changes.

    This extends pairwise cross-family retention into a longer interleaved accumulation sequence. All
    tasks, runtime, and evaluators remain repository-authored internal development evidence and cannot
    satisfy the independent production AGI gate.
    """
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    first = run_recursive_skill_postlearning_retention(root, f"{seed}:first")
    if not first.get("passed"):
        raise InterleavedCrossFamilyRetentionError(
            "first recursive-to-heterogeneous retention stage failed"
        )

    first_recursive_candidate_id = str(first["recursive_candidate_id"])
    first_recursive_component_ids = [
        str(item) for item in first["recursive_component_candidate_ids"]
    ]
    first_heterogeneous_candidate_ids = [
        str(item) for item in first["later_heterogeneous_candidate_ids"]
    ]
    first_stage_ids = [
        *first_recursive_component_ids,
        first_recursive_candidate_id,
        *first_heterogeneous_candidate_ids,
    ]
    first_stage_trials_before_second_recursive = _trial_snapshots(
        root, first_stage_ids
    )

    task_specs = _task_specs()
    if len(task_specs) != len(first_heterogeneous_candidate_ids):
        raise InterleavedCrossFamilyRetentionError(
            "heterogeneous retained family no longer matches task specifications"
        )
    heterogeneous_specs = [
        _candidate_spec(root, candidate_id)
        for candidate_id in first_heterogeneous_candidate_ids
    ]

    second = run_recursive_memory_skill_composition(root, f"{seed}:second-recursive")
    if not second.get("passed") or not second.get(
        "second_generation_candidate_promoted"
    ):
        raise InterleavedCrossFamilyRetentionError(
            "second recursive memory skill did not reach verified promotion"
        )

    first_stage_trials_after_second_recursive = _trial_snapshots(root, first_stage_ids)
    first_stage_trials_unchanged_after_second_recursive = (
        first_stage_trials_after_second_recursive
        == first_stage_trials_before_second_recursive
    )
    if not first_stage_trials_unchanged_after_second_recursive:
        raise InterleavedCrossFamilyRetentionError(
            "second recursive learning rewrote earlier cross-family trial evidence"
        )

    second_recursive_candidate_id = str(second["second_generation_candidate_id"])
    second_recursive_component_ids = [
        str(item) for item in second["component_candidate_ids"]
    ]
    second_stage_ids = [
        *second_recursive_component_ids,
        second_recursive_candidate_id,
    ]
    second_stage_trials_before_interleaved_replay = _trial_snapshots(
        root, second_stage_ids
    )

    first_recursive_spec = _candidate_spec(root, first_recursive_candidate_id)
    second_recursive_spec = _candidate_spec(root, second_recursive_candidate_id)
    first_recursive_challenges = (
        {"fresh": 31},
        {"a": 31, "fresh": 32},
        {"b": 33, "fresh": 34},
    )
    second_recursive_challenges = (
        {"fresh": 41},
        {"a": False, "fresh": 42},
        {"c": 43, "fresh": 44},
    )

    fresh_engine_runs: list[str] = []
    replay_records: list[dict[str, Any]] = []

    for repeat_index in range(2):
        for case_index, value in enumerate(first_recursive_challenges):
            expected = not _target_answer(value)
            engine = _mechanical_engine(root)
            run_id = _new_runtime_run(engine)
            response = _invoke(
                engine,
                run_id,
                scope=first_recursive_spec.scope,
                tool_id=first_recursive_spec.tool_id,
                value=value,
            )
            result = _validate_runtime_output(
                response,
                expected=expected,
                candidate_id=first_recursive_candidate_id,
            )
            fresh_engine_runs.append(run_id)
            replay_records.append(
                {
                    "family": "first_recursive",
                    "repeat_index": repeat_index,
                    "case_index": case_index,
                    "input_sha256": _digest(value),
                    "expected_sha256": _digest(expected),
                    "output_sha256": _digest(result.get("output")),
                }
            )

        for case_index, (candidate_id, candidate_spec, task_spec) in enumerate(
            zip(
                first_heterogeneous_candidate_ids,
                heterogeneous_specs,
                task_specs,
                strict=True,
            )
        ):
            engine = _mechanical_engine(root)
            run_id = _new_runtime_run(engine)
            response = _invoke(
                engine,
                run_id,
                scope=candidate_spec.scope,
                tool_id=candidate_spec.tool_id,
                value=task_spec["probe"],
            )
            result = _validate_runtime_output(
                response,
                expected=task_spec["expected"],
                candidate_id=candidate_id,
            )
            fresh_engine_runs.append(run_id)
            replay_records.append(
                {
                    "family": "heterogeneous",
                    "repeat_index": repeat_index,
                    "case_index": case_index,
                    "candidate_id": candidate_id,
                    "input_sha256": _digest(task_spec["probe"]),
                    "expected_sha256": _digest(task_spec["expected"]),
                    "output_sha256": _digest(result.get("output")),
                }
            )

        for case_index, value in enumerate(second_recursive_challenges):
            expected = not _target_answer(value)
            engine = _mechanical_engine(root)
            run_id = _new_runtime_run(engine)
            response = _invoke(
                engine,
                run_id,
                scope=second_recursive_spec.scope,
                tool_id=second_recursive_spec.tool_id,
                value=value,
            )
            result = _validate_runtime_output(
                response,
                expected=expected,
                candidate_id=second_recursive_candidate_id,
            )
            fresh_engine_runs.append(run_id)
            replay_records.append(
                {
                    "family": "second_recursive",
                    "repeat_index": repeat_index,
                    "case_index": case_index,
                    "input_sha256": _digest(value),
                    "expected_sha256": _digest(expected),
                    "output_sha256": _digest(result.get("output")),
                }
            )

    if len(set(fresh_engine_runs)) != len(fresh_engine_runs):
        raise InterleavedCrossFamilyRetentionError(
            "interleaved retention reused a fresh Engine run"
        )

    first_stage_trials_after_replay = _trial_snapshots(root, first_stage_ids)
    second_stage_trials_after_replay = _trial_snapshots(root, second_stage_ids)
    first_stage_trials_unchanged_after_replay = (
        first_stage_trials_after_replay
        == first_stage_trials_before_second_recursive
    )
    second_stage_trials_unchanged_after_replay = (
        second_stage_trials_after_replay
        == second_stage_trials_before_interleaved_replay
    )
    if not first_stage_trials_unchanged_after_replay:
        raise InterleavedCrossFamilyRetentionError(
            "interleaved replay rewrote first-stage Candidate trial evidence"
        )
    if not second_stage_trials_unchanged_after_replay:
        raise InterleavedCrossFamilyRetentionError(
            "interleaved replay rewrote second-stage Candidate trial evidence"
        )

    all_outputs_matched = all(
        item["expected_sha256"] == item["output_sha256"]
        for item in replay_records
    )
    if not all_outputs_matched:
        raise InterleavedCrossFamilyRetentionError(
            "interleaved cross-family behavior changed on fresh replay"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "interleaved-cross-family-retention-v1",
        "acquisition_sequence": [
            "recursive_memory_skill",
            "heterogeneous",
            "recursive_memory_skill",
        ],
        "first_recursive_candidate_id": first_recursive_candidate_id,
        "first_recursive_component_candidate_ids": first_recursive_component_ids,
        "heterogeneous_candidate_ids": first_heterogeneous_candidate_ids,
        "heterogeneous_capability_count": len(first_heterogeneous_candidate_ids),
        "second_recursive_candidate_id": second_recursive_candidate_id,
        "second_recursive_component_candidate_ids": second_recursive_component_ids,
        "first_stage_candidate_count": len(first_stage_ids),
        "second_stage_candidate_count": len(second_stage_ids),
        "first_stage_trials_unchanged_after_second_recursive_learning": (
            first_stage_trials_unchanged_after_second_recursive
        ),
        "first_stage_trials_unchanged_after_interleaved_replay": (
            first_stage_trials_unchanged_after_replay
        ),
        "second_stage_trials_unchanged_after_interleaved_replay": (
            second_stage_trials_unchanged_after_replay
        ),
        "heterogeneous_negative_evidence_retained": bool(
            first["later_negative_evidence_retained"]
        ),
        "fresh_engine_run_count": len(fresh_engine_runs),
        "fresh_engine_runs": fresh_engine_runs,
        "repeat_count": 2,
        "first_recursive_replay_case_count": len(first_recursive_challenges),
        "heterogeneous_replay_case_count": len(task_specs),
        "second_recursive_replay_case_count": len(second_recursive_challenges),
        "all_interleaved_outputs_matched": all_outputs_matched,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal deterministic multi-stage continual-retention evidence only. A recursive "
            "memory-derived skill, later heterogeneous numeric/string/sequence/object learning, and a "
            "second recursive-memory learning stage coexist with fresh-Engine behavioral replay and "
            "byte-identical Candidate trial ledgers. Repository-authored tasks and evaluators do not "
            "establish independent production AGI or satisfy the strict external evidence gate."
        ),
    }
    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "interleaved-cross-family-retention"
        / f"interleaved-{_digest(seed)[:16]}.json",
        report,
    )
    return report
