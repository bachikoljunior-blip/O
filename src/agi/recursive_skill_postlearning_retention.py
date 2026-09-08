from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.heterogeneous_retention_campaign import (
    _digest,
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


class RecursiveSkillPostlearningRetentionError(RuntimeError):
    pass


def _candidate_spec(root: Path, candidate_id: str) -> AcquiredProgramToolSpec:
    path = root / ".continual" / "candidates" / candidate_id / "candidate.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RecursiveSkillPostlearningRetentionError(
            "recursive retained Candidate must be an object"
        )
    return AcquiredProgramToolSpec.from_candidate(raw)


def _trial_snapshots(root: Path, candidate_ids: list[str]) -> dict[str, dict[str, str]]:
    values = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in candidate_ids
    }
    if not all(values.values()):
        raise RecursiveSkillPostlearningRetentionError(
            "retention campaign Candidate lacks durable regression trial evidence"
        )
    return values


def run_recursive_skill_postlearning_retention(root: Path, seed: str) -> dict[str, Any]:
    """Falsify cross-family forgetting of a recursively learned memory-derived skill.

    First materialize and regression-promote the second-generation recursive memory skill. Freeze its
    complete trial ledger plus every component ledger. Then perform a later heterogeneous learning
    campaign spanning numeric, string, sequence, and object capabilities. After that unrelated learning,
    replay the recursive skill in fresh mechanical Engines on held-out objects and require both families'
    trial ledgers to remain byte-identical.

    This specifically tests interference across distinct learning families rather than replaying a skill
    only immediately after its own promotion. All tasks and evaluators remain repository-authored internal
    development evidence and cannot satisfy the independent production AGI gate.
    """
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    recursive = run_recursive_memory_skill_composition(root, f"{seed}:recursive")
    if not recursive.get("passed") or not recursive.get("second_generation_candidate_promoted"):
        raise RecursiveSkillPostlearningRetentionError(
            "recursive memory skill did not reach verified second-generation promotion"
        )

    recursive_candidate_id = str(recursive["second_generation_candidate_id"])
    recursive_component_ids = [str(item) for item in recursive["component_candidate_ids"]]
    recursive_ids = [*recursive_component_ids, recursive_candidate_id]
    recursive_trials_before = _trial_snapshots(root, recursive_ids)
    recursive_spec = _candidate_spec(root, recursive_candidate_id)
    if recursive_spec.scope != str(recursive["second_generation_scope"]):
        raise RecursiveSkillPostlearningRetentionError(
            "recursive Candidate scope changed before later learning"
        )

    later = run_heterogeneous_retention_campaign(root, f"{seed}:later-heterogeneous")
    if not later.get("passed"):
        raise RecursiveSkillPostlearningRetentionError(
            "later heterogeneous learning campaign failed"
        )
    later_candidate_ids = [str(item) for item in later["candidate_ids"]]
    later_trials_before_replay = _trial_snapshots(root, later_candidate_ids)

    recursive_trials_after_later_learning = _trial_snapshots(root, recursive_ids)
    recursive_trials_unchanged_after_later_learning = (
        recursive_trials_after_later_learning == recursive_trials_before
    )
    if not recursive_trials_unchanged_after_later_learning:
        raise RecursiveSkillPostlearningRetentionError(
            "later heterogeneous learning rewrote recursive skill trial evidence"
        )

    challenge_values = (
        {"new": 11},
        {"a": 11, "new": 12},
        {"b": 13, "new": 14},
        {"a": False, "b": 15, "new": 16},
    )
    replay_records: list[dict[str, Any]] = []
    fresh_engine_runs: list[str] = []
    for repeat_index in range(2):
        for case_index, value in enumerate(challenge_values):
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
                raise RecursiveSkillPostlearningRetentionError(
                    "recursive skill changed behavior after unrelated later learning"
                )
            fresh_engine_runs.append(run_id)
            replay_records.append(
                {
                    "repeat_index": repeat_index,
                    "case_index": case_index,
                    "input_sha256": _digest(value),
                    "expected": expected,
                    "output": result.get("output"),
                }
            )

    if len(set(fresh_engine_runs)) != len(fresh_engine_runs):
        raise RecursiveSkillPostlearningRetentionError(
            "post-learning retention replay reused a fresh Engine run"
        )

    recursive_trials_after_replay = _trial_snapshots(root, recursive_ids)
    later_trials_after_replay = _trial_snapshots(root, later_candidate_ids)
    recursive_trials_unchanged_after_replay = (
        recursive_trials_after_replay == recursive_trials_before
    )
    later_trials_unchanged_after_recursive_replay = (
        later_trials_after_replay == later_trials_before_replay
    )
    if not recursive_trials_unchanged_after_replay:
        raise RecursiveSkillPostlearningRetentionError(
            "post-learning replay changed recursive Candidate trial evidence"
        )
    if not later_trials_unchanged_after_recursive_replay:
        raise RecursiveSkillPostlearningRetentionError(
            "recursive replay changed later heterogeneous Candidate trial evidence"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "recursive-skill-postlearning-retention-v1",
        "recursive_candidate_id": recursive_candidate_id,
        "recursive_component_candidate_ids": recursive_component_ids,
        "recursive_candidate_count": len(recursive_ids),
        "later_heterogeneous_candidate_ids": later_candidate_ids,
        "later_heterogeneous_capability_count": int(later["capability_count"]),
        "later_heterogeneous_input_domains": list(later["input_domains"]),
        "later_heterogeneous_output_domains": list(later["output_domains"]),
        "recursive_trials_unchanged_after_later_learning": (
            recursive_trials_unchanged_after_later_learning
        ),
        "recursive_trials_unchanged_after_postlearning_replay": (
            recursive_trials_unchanged_after_replay
        ),
        "later_trials_unchanged_after_recursive_replay": (
            later_trials_unchanged_after_recursive_replay
        ),
        "later_negative_evidence_retained": bool(later["all_negative_evidence_retained"]),
        "fresh_engine_run_count": len(fresh_engine_runs),
        "fresh_engine_runs": fresh_engine_runs,
        "heldout_case_count": len(challenge_values),
        "repeat_count": 2,
        "all_postlearning_outputs_matched": all(
            item["output"] == item["expected"] for item in replay_records
        ),
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal deterministic cross-family continual-retention evidence only. A second-generation "
            "skill compiled from memory-derived verified components remains behaviorally correct in fresh "
            "Engines after unrelated heterogeneous numeric/string/sequence/object learning, while both "
            "families' Candidate trial ledgers remain byte-identical. Repository-authored tasks, "
            "mechanical runtime, and internal evaluators do not establish independent production AGI."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "recursive-skill-postlearning-retention"
        / f"retention-{_digest(seed)[:16]}.json",
        report,
    )
    return report
