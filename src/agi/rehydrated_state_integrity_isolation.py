from __future__ import annotations

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
from agi.durable_state_rehydration import _run_one, _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest, _task_specs
from agi.materialized_runtime_replay import _invoke, _mechanical_engine, _new_runtime_run
from agi.multiround_disambiguation_bank import _target_answer
from continual.acquired_program_tools import AcquiredProgramToolError
from continual.learned_tools import LearnedToolError


class RehydratedStateIntegrityIsolationError(RuntimeError):
    pass


def _target_fails_closed(
    root: Path,
    *,
    scope: str,
    tool_id: str,
    value: Any,
) -> tuple[bool, str, str | None]:
    engine = _mechanical_engine(root)
    run_id = _new_runtime_run(engine)
    try:
        _invoke(
            engine,
            run_id,
            scope=scope,
            tool_id=tool_id,
            value=value,
        )
    except (AcquiredProgramToolError, LearnedToolError) as exc:
        return True, run_id, type(exc).__name__
    return False, run_id, None


def _replay_unaffected_families(
    root: Path,
    *,
    heterogeneous_ids: list[str],
    task_specs: list[dict[str, Any]],
    recursive_id: str,
    recursive_value: dict[str, Any],
) -> list[str]:
    runs: list[str] = []
    for candidate_id, task_spec in zip(
        heterogeneous_ids,
        task_specs,
        strict=True,
    ):
        runs.append(
            _run_one(
                root,
                candidate_id=candidate_id,
                value=task_spec["probe"],
                expected=task_spec["expected"],
            )
        )
    runs.append(
        _run_one(
            root,
            candidate_id=recursive_id,
            value=recursive_value,
            expected=not _target_answer(recursive_value),
        )
    )
    return runs


def run_rehydrated_state_integrity_isolation(root: Path, seed: str) -> dict[str, Any]:
    """Falsify whether one corrupted durable proof can contaminate other retained skills.

    The campaign accumulates the verified heterogeneous family plus the later recursive memory-derived
    skill, copies only durable Candidate/system state into a distinct root, and then applies two local
    corruption probes to one heterogeneous Candidate. First its exact regression decision file is
    removed. Then, after exact restoration, its immutable Candidate description is modified while the
    old regression proof remains. Each corruption must make only the target exact scope fail closed;
    the other heterogeneous capabilities and the recursive composite must keep replaying through fresh
    Engines with byte-identical Candidate state and trial ledgers. Restoring the exact persisted bytes
    must recover the target without new regression trials. All source state must remain unchanged.

    This is repository-authored deterministic robustness evidence only. It cannot establish evaluator
    independence or satisfy the production AGI claim gate.
    """
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    base = run_cross_family_order_retention(root, f"{seed}:source")
    if not base.get("passed"):
        raise RehydratedStateIntegrityIsolationError(
            "cross-family accumulated-capability prerequisite did not pass"
        )

    heterogeneous_ids = [str(item) for item in base["heterogeneous_candidate_ids"]]
    recursive_component_ids = [
        str(item) for item in base["recursive_component_candidate_ids"]
    ]
    recursive_id = str(base["recursive_candidate_id"])
    all_candidate_ids = [
        *heterogeneous_ids,
        *recursive_component_ids,
        recursive_id,
    ]
    if len(all_candidate_ids) != len(set(all_candidate_ids)):
        raise RehydratedStateIntegrityIsolationError(
            "accumulated Candidate identities are not unique"
        )

    task_specs = list(_task_specs())
    if len(task_specs) != len(heterogeneous_ids):
        raise RehydratedStateIntegrityIsolationError(
            "heterogeneous Candidate family no longer matches task specifications"
        )

    target_index = 0
    target_id = heterogeneous_ids[target_index]
    target_task = task_specs[target_index]
    target_spec = _candidate_spec(root, target_id)
    unaffected_heterogeneous_ids = [
        candidate_id
        for index, candidate_id in enumerate(heterogeneous_ids)
        if index != target_index
    ]
    unaffected_task_specs = [
        task_spec
        for index, task_spec in enumerate(task_specs)
        if index != target_index
    ]
    stable_ids = [
        *unaffected_heterogeneous_ids,
        *recursive_component_ids,
        recursive_id,
    ]

    source_tree_before = _tree_snapshot(root, all_candidate_ids)
    source_trials_before = _trial_snapshots(root, all_candidate_ids)

    with tempfile.TemporaryDirectory(prefix="agi-rehydrated-integrity-") as temporary:
        rehydrated_root = Path(temporary).resolve()
        shutil.copytree(
            root / ".continual" / "candidates",
            rehydrated_root / ".continual" / "candidates",
        )
        shutil.copytree(
            root / ".continual" / "system",
            rehydrated_root / ".continual" / "system",
        )

        copied_tree_before = _tree_snapshot(rehydrated_root, all_candidate_ids)
        copied_trials_before = _trial_snapshots(rehydrated_root, all_candidate_ids)
        if copied_tree_before != source_tree_before:
            raise RehydratedStateIntegrityIsolationError(
                "rehydrated Candidate bytes differ from source"
            )
        if copied_trials_before != source_trials_before:
            raise RehydratedStateIntegrityIsolationError(
                "rehydrated trial ledgers differ from source"
            )

        stable_tree_before = _tree_snapshot(rehydrated_root, stable_ids)
        stable_trials_before = _trial_snapshots(rehydrated_root, stable_ids)
        target_candidate_path = (
            rehydrated_root
            / ".continual"
            / "candidates"
            / target_id
            / "candidate.json"
        )
        target_candidate_bytes = target_candidate_path.read_bytes()
        target_candidate = json.loads(target_candidate_bytes.decode("utf-8"))
        if not isinstance(target_candidate, dict):
            raise RehydratedStateIntegrityIsolationError(
                "target Candidate state must be an object"
            )
        verified = target_candidate.get("verified_scope_states", {}).get(target_spec.scope)
        if not isinstance(verified, dict):
            raise RehydratedStateIntegrityIsolationError(
                "target Candidate lacks exact-scope verified state"
            )
        decision_ref = verified.get("decision_ref")
        if not isinstance(decision_ref, str) or not decision_ref:
            raise RehydratedStateIntegrityIsolationError(
                "target Candidate lacks a regression decision reference"
            )
        decision_path = rehydrated_root / decision_ref
        decision_bytes = decision_path.read_bytes()

        decision_path.unlink()
        missing_proof_failed_closed, missing_proof_run, missing_proof_error = (
            _target_fails_closed(
                rehydrated_root,
                scope=target_spec.scope,
                tool_id=target_spec.tool_id,
                value=target_task["probe"],
            )
        )
        if not missing_proof_failed_closed:
            raise RehydratedStateIntegrityIsolationError(
                "target remained callable after its exact regression proof was removed"
            )
        missing_unaffected_runs = _replay_unaffected_families(
            rehydrated_root,
            heterogeneous_ids=unaffected_heterogeneous_ids,
            task_specs=unaffected_task_specs,
            recursive_id=recursive_id,
            recursive_value={"a": False, "integrity": 81},
        )
        stable_after_missing = _tree_snapshot(rehydrated_root, stable_ids)
        stable_trials_after_missing = _trial_snapshots(rehydrated_root, stable_ids)
        if stable_after_missing != stable_tree_before:
            raise RehydratedStateIntegrityIsolationError(
                "missing target proof changed another Candidate family"
            )
        if stable_trials_after_missing != stable_trials_before:
            raise RehydratedStateIntegrityIsolationError(
                "missing target proof changed another Candidate trial ledger"
            )

        decision_path.parent.mkdir(parents=True, exist_ok=True)
        decision_path.write_bytes(decision_bytes)
        recovered_after_missing_run = _run_one(
            rehydrated_root,
            candidate_id=target_id,
            value=target_task["probe"],
            expected=target_task["expected"],
        )
        if _tree_snapshot(rehydrated_root, all_candidate_ids) != copied_tree_before:
            raise RehydratedStateIntegrityIsolationError(
                "exact proof restoration did not restore Candidate bytes"
            )

        semantic_tamper = json.loads(target_candidate_bytes.decode("utf-8"))
        acquired = semantic_tamper.get("acquired_program")
        if not isinstance(acquired, dict):
            raise RehydratedStateIntegrityIsolationError(
                "target is not an acquired-program Candidate"
            )
        acquired["description"] = str(acquired.get("description", "")) + " [integrity-tamper]"
        _atomic_json(target_candidate_path, semantic_tamper)
        semantic_tamper_failed_closed, semantic_tamper_run, semantic_tamper_error = (
            _target_fails_closed(
                rehydrated_root,
                scope=target_spec.scope,
                tool_id=target_spec.tool_id,
                value=target_task["probe"],
            )
        )
        if not semantic_tamper_failed_closed:
            raise RehydratedStateIntegrityIsolationError(
                "target remained callable after immutable Candidate state changed"
            )
        tamper_unaffected_runs = _replay_unaffected_families(
            rehydrated_root,
            heterogeneous_ids=unaffected_heterogeneous_ids,
            task_specs=unaffected_task_specs,
            recursive_id=recursive_id,
            recursive_value={"b": 82, "integrity": 83},
        )
        stable_after_semantic_tamper = _tree_snapshot(rehydrated_root, stable_ids)
        stable_trials_after_semantic_tamper = _trial_snapshots(rehydrated_root, stable_ids)
        if stable_after_semantic_tamper != stable_tree_before:
            raise RehydratedStateIntegrityIsolationError(
                "target semantic tamper changed another Candidate family"
            )
        if stable_trials_after_semantic_tamper != stable_trials_before:
            raise RehydratedStateIntegrityIsolationError(
                "target semantic tamper changed another Candidate trial ledger"
            )

        target_candidate_path.write_bytes(target_candidate_bytes)
        recovered_after_semantic_tamper_run = _run_one(
            rehydrated_root,
            candidate_id=target_id,
            value=target_task["probe"],
            expected=target_task["expected"],
        )
        copied_tree_after = _tree_snapshot(rehydrated_root, all_candidate_ids)
        copied_trials_after = _trial_snapshots(rehydrated_root, all_candidate_ids)
        exact_state_restored = copied_tree_after == copied_tree_before
        trial_ledgers_unchanged = copied_trials_after == copied_trials_before
        if not exact_state_restored:
            raise RehydratedStateIntegrityIsolationError(
                "rehydrated state did not return to exact persisted bytes after restoration"
            )
        if not trial_ledgers_unchanged:
            raise RehydratedStateIntegrityIsolationError(
                "integrity probes rewrote Candidate trial ledgers"
            )

    source_tree_after = _tree_snapshot(root, all_candidate_ids)
    source_trials_after = _trial_snapshots(root, all_candidate_ids)
    source_state_unchanged = source_tree_after == source_tree_before
    source_trials_unchanged = source_trials_after == source_trials_before
    if not source_state_unchanged or not source_trials_unchanged:
        raise RehydratedStateIntegrityIsolationError(
            "integrity isolation probe mutated source durable state"
        )

    fresh_runs = [
        missing_proof_run,
        *missing_unaffected_runs,
        recovered_after_missing_run,
        semantic_tamper_run,
        *tamper_unaffected_runs,
        recovered_after_semantic_tamper_run,
    ]
    if len(set(fresh_runs)) != len(fresh_runs):
        raise RehydratedStateIntegrityIsolationError(
            "integrity isolation reused a fresh Engine run"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "rehydrated-state-integrity-isolation-v1",
        "target_candidate_id": target_id,
        "target_scope": target_spec.scope,
        "target_decision_ref": decision_ref,
        "unaffected_heterogeneous_candidate_ids": unaffected_heterogeneous_ids,
        "recursive_candidate_id": recursive_id,
        "recursive_component_candidate_ids": recursive_component_ids,
        "missing_regression_proof_failed_closed": missing_proof_failed_closed,
        "missing_regression_proof_error": missing_proof_error,
        "unaffected_state_unchanged_after_missing_proof": (
            stable_after_missing == stable_tree_before
        ),
        "unaffected_trials_unchanged_after_missing_proof": (
            stable_trials_after_missing == stable_trials_before
        ),
        "target_recovered_after_exact_proof_restore": True,
        "semantic_tamper_failed_closed": semantic_tamper_failed_closed,
        "semantic_tamper_error": semantic_tamper_error,
        "unaffected_state_unchanged_after_semantic_tamper": (
            stable_after_semantic_tamper == stable_tree_before
        ),
        "unaffected_trials_unchanged_after_semantic_tamper": (
            stable_trials_after_semantic_tamper == stable_trials_before
        ),
        "target_recovered_after_exact_candidate_restore": True,
        "rehydrated_exact_state_restored": exact_state_restored,
        "rehydrated_trial_ledgers_unchanged": trial_ledgers_unchanged,
        "source_candidate_state_unchanged": source_state_unchanged,
        "source_trial_ledgers_unchanged": source_trials_unchanged,
        "fresh_engine_run_count": len(fresh_runs),
        "fresh_engine_runs": fresh_runs,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal deterministic rehydrated-state integrity evidence only. Missing regression proof "
            "or immutable Candidate-state changes fail closed only for the affected exact scope while "
            "unrelated accumulated families remain replayable, and exact persisted-byte restoration "
            "recovers the target without new trials. Repository-authored corruption probes and evaluators "
            "do not establish independent production AGI."
        ),
    }
    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "rehydrated-state-integrity-isolation"
        / f"integrity-{_digest(seed)[:16]}.json",
        report,
    )
    return report
