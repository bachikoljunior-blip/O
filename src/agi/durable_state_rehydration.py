from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.cross_family_order_retention import (
    _candidate_spec,
    _trial_snapshots,
    run_cross_family_order_retention,
)
from agi.heterogeneous_retention_campaign import _digest, _task_specs
from agi.materialized_runtime_replay import (
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
    _validate_runtime_output,
)
from agi.multiround_disambiguation_bank import _target_answer
from continual.acquired_program_tools import AcquiredProgramToolError
from continual.learned_tools import LearnedToolError


class DurableStateRehydrationError(RuntimeError):
    pass


def _tree_snapshot(root: Path, candidate_ids: list[str]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for candidate_id in sorted(candidate_ids):
        base = root / ".continual" / "candidates" / candidate_id
        if not base.is_dir():
            raise DurableStateRehydrationError(
                f"missing persisted Candidate state: {candidate_id}"
            )
        for path in sorted(item for item in base.rglob("*") if item.is_file()):
            relative = path.relative_to(root).as_posix()
            snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not snapshot:
        raise DurableStateRehydrationError("no Candidate files were available to rehydrate")
    return snapshot


def _run_one(
    root: Path,
    *,
    candidate_id: str,
    value: Any,
    expected: Any,
) -> str:
    spec = _candidate_spec(root, candidate_id)
    engine = _mechanical_engine(root)
    run_id = _new_runtime_run(engine)
    response = _invoke(
        engine,
        run_id,
        scope=spec.scope,
        tool_id=spec.tool_id,
        value=value,
    )
    result = _validate_runtime_output(
        response,
        expected=expected,
        candidate_id=candidate_id,
    )
    if result.get("output") != expected:
        raise DurableStateRehydrationError(
            f"rehydrated capability returned the wrong output: {candidate_id}"
        )
    return run_id


def run_durable_state_rehydration(root: Path, seed: str) -> dict[str, Any]:
    """Reconstruct accumulated learned capability state in a separate filesystem root.

    The source root first accumulates the verified heterogeneous numeric/string/sequence/object family
    and a later second-generation recursive memory-derived skill. Only durable Candidate state and the
    persistent system state are then copied into a distinct root: prior runs, episodes, and evidence are
    deliberately omitted. Fresh mechanical Engines in that root must replay every accumulated family,
    while a control root without Candidate state must fail closed. Candidate bytes and trial ledgers
    must remain unchanged across replay.

    This is repository-authored deterministic development evidence only. It demonstrates persistence
    and reconstruction properties of the current bounded learned capabilities, not independent AGI.
    """
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    base = run_cross_family_order_retention(root, f"{seed}:source")
    if not base.get("passed"):
        raise DurableStateRehydrationError(
            "cross-family accumulated-capability prerequisite did not pass"
        )

    heterogeneous_ids = [str(item) for item in base["heterogeneous_candidate_ids"]]
    recursive_component_ids = [
        str(item) for item in base["recursive_component_candidate_ids"]
    ]
    recursive_id = str(base["recursive_candidate_id"])
    candidate_ids = [
        *heterogeneous_ids,
        *recursive_component_ids,
        recursive_id,
    ]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise DurableStateRehydrationError(
            "accumulated capability state contains duplicate Candidate identities"
        )

    source_candidate_before = _tree_snapshot(root, candidate_ids)
    source_trials_before = _trial_snapshots(root, candidate_ids)
    task_specs = _task_specs()
    if len(task_specs) != len(heterogeneous_ids):
        raise DurableStateRehydrationError(
            "heterogeneous Candidate family no longer matches task specifications"
        )

    with tempfile.TemporaryDirectory(prefix="agi-durable-rehydration-") as temporary:
        rehydrated_root = Path(temporary).resolve()
        candidates_source = root / ".continual" / "candidates"
        system_source = root / ".continual" / "system"
        if not candidates_source.is_dir() or not system_source.is_dir():
            raise DurableStateRehydrationError(
                "durable Candidate/system state is incomplete"
            )
        shutil.copytree(
            candidates_source,
            rehydrated_root / ".continual" / "candidates",
        )
        shutil.copytree(
            system_source,
            rehydrated_root / ".continual" / "system",
        )

        copied_candidate_before = _tree_snapshot(rehydrated_root, candidate_ids)
        copied_trials_before = _trial_snapshots(rehydrated_root, candidate_ids)
        copy_byte_identical = copied_candidate_before == source_candidate_before
        if not copy_byte_identical:
            raise DurableStateRehydrationError(
                "rehydrated Candidate state differs from persisted source bytes"
            )
        if copied_trials_before != source_trials_before:
            raise DurableStateRehydrationError(
                "rehydrated Candidate trial ledgers differ from source"
            )

        with tempfile.TemporaryDirectory(prefix="agi-rehydration-control-") as control_temporary:
            control_root = Path(control_temporary).resolve()
            shutil.copytree(
                system_source,
                control_root / ".continual" / "system",
            )
            first_spec = _candidate_spec(rehydrated_root, heterogeneous_ids[0])
            control_engine = _mechanical_engine(control_root)
            control_run = _new_runtime_run(control_engine)
            no_candidate_state_failed_closed = False
            try:
                _invoke(
                    control_engine,
                    control_run,
                    scope=first_spec.scope,
                    tool_id=first_spec.tool_id,
                    value=task_specs[0]["probe"],
                )
            except (LearnedToolError, AcquiredProgramToolError):
                no_candidate_state_failed_closed = True
            if not no_candidate_state_failed_closed:
                raise DurableStateRehydrationError(
                    "control root executed a learned capability without Candidate state"
                )

        fresh_runs: list[str] = []
        replay_records: list[dict[str, Any]] = []
        for candidate_id, task_spec in zip(
            heterogeneous_ids,
            task_specs,
            strict=True,
        ):
            run_id = _run_one(
                rehydrated_root,
                candidate_id=candidate_id,
                value=task_spec["probe"],
                expected=task_spec["expected"],
            )
            fresh_runs.append(run_id)
            replay_records.append(
                {
                    "family": "heterogeneous",
                    "candidate_id": candidate_id,
                    "input_sha256": _digest(task_spec["probe"]),
                    "expected_sha256": _digest(task_spec["expected"]),
                }
            )

        recursive_challenges = (
            {"new": 71},
            {"a": 72, "new": 73},
            {"b": 74, "new": 75},
            {"a": False, "b": 76, "new": 77},
        )
        for value in recursive_challenges:
            expected = not _target_answer(value)
            run_id = _run_one(
                rehydrated_root,
                candidate_id=recursive_id,
                value=value,
                expected=expected,
            )
            fresh_runs.append(run_id)
            replay_records.append(
                {
                    "family": "recursive_memory_skill",
                    "candidate_id": recursive_id,
                    "input_sha256": _digest(value),
                    "expected_sha256": _digest(expected),
                }
            )

        if len(set(fresh_runs)) != len(fresh_runs):
            raise DurableStateRehydrationError(
                "rehydration replay reused a supposedly fresh Engine run"
            )

        copied_candidate_after = _tree_snapshot(rehydrated_root, candidate_ids)
        copied_trials_after = _trial_snapshots(rehydrated_root, candidate_ids)
        candidate_state_unchanged_after_replay = (
            copied_candidate_after == copied_candidate_before
        )
        trial_ledgers_unchanged_after_replay = (
            copied_trials_after == copied_trials_before
        )
        if not candidate_state_unchanged_after_replay:
            raise DurableStateRehydrationError(
                "fresh replay mutated persisted Candidate state"
            )
        if not trial_ledgers_unchanged_after_replay:
            raise DurableStateRehydrationError(
                "fresh replay mutated Candidate trial ledgers"
            )

    source_candidate_after = _tree_snapshot(root, candidate_ids)
    source_trials_after = _trial_snapshots(root, candidate_ids)
    source_state_unchanged = source_candidate_after == source_candidate_before
    source_trials_unchanged = source_trials_after == source_trials_before
    if not source_state_unchanged or not source_trials_unchanged:
        raise DurableStateRehydrationError(
            "rehydration validation mutated source Candidate state"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "durable-state-rehydration-v1",
        "candidate_ids": candidate_ids,
        "candidate_count": len(candidate_ids),
        "heterogeneous_candidate_ids": heterogeneous_ids,
        "recursive_component_candidate_ids": recursive_component_ids,
        "recursive_candidate_id": recursive_id,
        "copied_state_roots": [
            ".continual/candidates",
            ".continual/system",
        ],
        "prior_runs_copied": False,
        "prior_episodes_copied": False,
        "prior_evidence_copied": False,
        "rehydrated_root_is_distinct": True,
        "candidate_state_copy_byte_identical": copy_byte_identical,
        "no_candidate_state_failed_closed": no_candidate_state_failed_closed,
        "candidate_state_unchanged_after_replay": (
            candidate_state_unchanged_after_replay
        ),
        "trial_ledgers_unchanged_after_replay": trial_ledgers_unchanged_after_replay,
        "source_candidate_state_unchanged": source_state_unchanged,
        "source_trial_ledgers_unchanged": source_trials_unchanged,
        "fresh_engine_run_count": len(fresh_runs),
        "fresh_engine_runs": fresh_runs,
        "replay_records": replay_records,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal deterministic durable-state reconstruction evidence only. Persisted Candidate and "
            "system state can reconstruct the currently accumulated bounded skill families in a distinct "
            "filesystem root without copying prior runs, episodes, or evidence, and replay stays fail-closed "
            "without Candidate state. Repository-authored tasks and evaluators do not establish independent "
            "production AGI."
        ),
    }
    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "durable-state-rehydration"
        / f"rehydration-{_digest(seed)[:16]}.json",
        report,
    )
    return report
