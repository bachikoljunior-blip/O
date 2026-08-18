from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.heterogeneous_retention_campaign import (
    _digest,
    _task_specs,
    run_heterogeneous_retention_campaign,
)
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
    _validate_runtime_output,
)
from agi.multiround_disambiguation_bank import _target_answer
from agi.recursive_memory_skill_composition import run_recursive_memory_skill_composition
from continual.acquired_program_tools import AcquiredProgramToolSpec


class CrossFamilyOrderRetentionError(RuntimeError):
    pass


def _candidate_spec(root: Path, candidate_id: str) -> AcquiredProgramToolSpec:
    path = root / ".continual" / "candidates" / candidate_id / "candidate.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise CrossFamilyOrderRetentionError("retained Candidate must be an object")
    return AcquiredProgramToolSpec.from_candidate(raw)


def _trial_snapshots(root: Path, candidate_ids: list[str]) -> dict[str, dict[str, str]]:
    snapshots = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in candidate_ids
    }
    if not all(snapshots.values()):
        raise CrossFamilyOrderRetentionError(
            "cross-family Candidate lacks durable regression trial evidence"
        )
    return snapshots


def run_cross_family_order_retention(root: Path, seed: str) -> dict[str, Any]:
    """Falsify acquisition-order forgetting across heterogeneous and recursive skill families.

    The complementary earlier campaign learns a recursive memory-derived skill first and then performs
    unrelated heterogeneous learning. This campaign reverses that order: it first accumulates verified
    numeric, string, sequence, and object Candidates, freezes their complete trial ledgers, then learns
    a second-generation recursive memory-derived skill. Every earlier heterogeneous Candidate must still
    execute correctly afterwards in distinct fresh Engines, the recursive skill must also replay on fresh
    held-out objects, and neither family may rewrite the other's durable regression evidence.

    The result is internal deterministic development evidence only. All tasks, measurements, runtime,
    and evaluators are repository-authored and cannot satisfy the independent production AGI gate.
    """
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    heterogeneous = run_heterogeneous_retention_campaign(
        root, f"{seed}:heterogeneous-first"
    )
    if not heterogeneous.get("passed"):
        raise CrossFamilyOrderRetentionError(
            "heterogeneous-first accumulation did not pass"
        )
    heterogeneous_candidate_ids = [
        str(item) for item in heterogeneous["candidate_ids"]
    ]
    heterogeneous_trials_before_recursive = _trial_snapshots(
        root, heterogeneous_candidate_ids
    )

    task_specs = _task_specs()
    if len(task_specs) != len(heterogeneous_candidate_ids):
        raise CrossFamilyOrderRetentionError(
            "heterogeneous report no longer matches the retained task family"
        )
    heterogeneous_specs = [
        _candidate_spec(root, candidate_id)
        for candidate_id in heterogeneous_candidate_ids
    ]

    recursive = run_recursive_memory_skill_composition(
        root, f"{seed}:recursive-later"
    )
    if not recursive.get("passed") or not recursive.get(
        "second_generation_candidate_promoted"
    ):
        raise CrossFamilyOrderRetentionError(
            "later recursive memory skill did not reach verified promotion"
        )

    heterogeneous_trials_after_recursive = _trial_snapshots(
        root, heterogeneous_candidate_ids
    )
    heterogeneous_trials_unchanged_after_recursive = (
        heterogeneous_trials_after_recursive
        == heterogeneous_trials_before_recursive
    )
    if not heterogeneous_trials_unchanged_after_recursive:
        raise CrossFamilyOrderRetentionError(
            "later recursive learning rewrote heterogeneous trial evidence"
        )

    recursive_candidate_id = str(recursive["second_generation_candidate_id"])
    recursive_component_candidate_ids = [
        str(item) for item in recursive["component_candidate_ids"]
    ]
    recursive_candidate_ids = [
        *recursive_component_candidate_ids,
        recursive_candidate_id,
    ]
    recursive_trials_before_cross_replay = _trial_snapshots(
        root, recursive_candidate_ids
    )
    recursive_spec = _candidate_spec(root, recursive_candidate_id)
    if recursive_spec.scope != str(recursive["second_generation_scope"]):
        raise CrossFamilyOrderRetentionError(
            "recursive Candidate scope changed before cross-family replay"
        )

    recursive_challenges = (
        {"new": 21},
        {"a": 21, "new": 22},
        {"b": 23, "new": 24},
        {"a": False, "b": 25, "new": 26},
    )
    fresh_engine_runs: list[str] = []
    replay_records: list[dict[str, Any]] = []

    for repeat_index in range(2):
        for case_index, (candidate_id, candidate_spec, task_spec) in enumerate(
            zip(
                heterogeneous_candidate_ids,
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
            if result.get("output") != task_spec["expected"]:
                raise CrossFamilyOrderRetentionError(
                    "heterogeneous capability changed after later recursive learning"
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

        for case_index, value in enumerate(recursive_challenges):
            expected = not _target_answer(value)
            engine = _mechanical_engine(root)
            run_id = _new_runtime_run(engine)
            response = _invoke(
                engine,
                run_id,
                scope=recursive_spec.scope,
                tool_id=recursive_spec.tool_id,
                value=value,
            )
            result = _validate_runtime_output(
                response,
                expected=expected,
                candidate_id=recursive_candidate_id,
            )
            if result.get("output") != expected:
                raise CrossFamilyOrderRetentionError(
                    "recursive capability changed during reverse-order retention replay"
                )
            fresh_engine_runs.append(run_id)
            replay_records.append(
                {
                    "family": "recursive",
                    "repeat_index": repeat_index,
                    "case_index": case_index,
                    "candidate_id": recursive_candidate_id,
                    "input_sha256": _digest(value),
                    "expected_sha256": _digest(expected),
                    "output_sha256": _digest(result.get("output")),
                }
            )

    if len(set(fresh_engine_runs)) != len(fresh_engine_runs):
        raise CrossFamilyOrderRetentionError(
            "reverse-order retention reused a fresh Engine run"
        )

    heterogeneous_trials_after_replay = _trial_snapshots(
        root, heterogeneous_candidate_ids
    )
    recursive_trials_after_replay = _trial_snapshots(root, recursive_candidate_ids)
    heterogeneous_trials_unchanged_after_replay = (
        heterogeneous_trials_after_replay
        == heterogeneous_trials_before_recursive
    )
    recursive_trials_unchanged_after_replay = (
        recursive_trials_after_replay
        == recursive_trials_before_cross_replay
    )
    if not heterogeneous_trials_unchanged_after_replay:
        raise CrossFamilyOrderRetentionError(
            "cross-family replay rewrote heterogeneous Candidate trial evidence"
        )
    if not recursive_trials_unchanged_after_replay:
        raise CrossFamilyOrderRetentionError(
            "cross-family replay rewrote recursive Candidate trial evidence"
        )

    all_replays_matched = all(
        item["expected_sha256"] == item["output_sha256"]
        for item in replay_records
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": bool(
            heterogeneous_trials_unchanged_after_recursive
            and heterogeneous_trials_unchanged_after_replay
            and recursive_trials_unchanged_after_replay
            and all_replays_matched
        ),
        "campaign_kind": "cross-family-order-retention-v1",
        "acquisition_order": ["heterogeneous", "recursive_memory_skill"],
        "heterogeneous_candidate_ids": heterogeneous_candidate_ids,
        "heterogeneous_candidate_count": len(heterogeneous_candidate_ids),
        "heterogeneous_input_domains": list(heterogeneous["input_domains"]),
        "heterogeneous_output_domains": list(heterogeneous["output_domains"]),
        "heterogeneous_negative_evidence_retained": bool(
            heterogeneous["all_negative_evidence_retained"]
        ),
        "recursive_candidate_id": recursive_candidate_id,
        "recursive_component_candidate_ids": recursive_component_candidate_ids,
        "recursive_candidate_count": len(recursive_candidate_ids),
        "heterogeneous_trials_unchanged_after_recursive_learning": (
            heterogeneous_trials_unchanged_after_recursive
        ),
        "heterogeneous_trials_unchanged_after_cross_replay": (
            heterogeneous_trials_unchanged_after_replay
        ),
        "recursive_trials_unchanged_after_cross_replay": (
            recursive_trials_unchanged_after_replay
        ),
        "fresh_engine_run_count": len(fresh_engine_runs),
        "fresh_engine_runs": fresh_engine_runs,
        "repeat_count": 2,
        "heterogeneous_replay_case_count": len(task_specs),
        "recursive_replay_case_count": len(recursive_challenges),
        "all_cross_family_outputs_matched": all_replays_matched,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal deterministic acquisition-order retention evidence only. Heterogeneous numeric, "
            "string, sequence, and object Candidates remain behaviorally correct after later recursive "
            "memory-derived skill learning, and both families retain byte-identical regression trial "
            "ledgers through distinct fresh-Engine replay. Repository-authored tasks and evaluators do "
            "not establish independent production AGI or satisfy the strict external evidence gate."
        ),
    }
    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "cross-family-order-retention"
        / f"order-retention-{_digest(seed)[:16]}.json",
        report,
    )
    return report
