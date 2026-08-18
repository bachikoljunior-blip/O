from __future__ import annotations

import json
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
from continual.candidate_regression import record_candidate_regression
from continual.learned_tools import LearnedToolError


class CrossFamilyRollbackIsolationError(RuntimeError):
    pass


def _load_measurements(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise CrossFamilyRollbackIsolationError(
            f"regression input must be a measurement array: {path}"
        )
    return [dict(item) for item in raw]


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
        raise CrossFamilyRollbackIsolationError(
            f"retained capability changed during rollback isolation: {candidate_id}"
        )
    return run_id


def run_cross_family_rollback_isolation(root: Path, seed: str) -> dict[str, Any]:
    """Revoke and recover one learned scope without damaging other accumulated families.

    A reverse-order cross-family campaign first accumulates four heterogeneous Candidates and a
    second-generation recursive memory-derived skill. One heterogeneous Candidate is then deliberately
    re-evaluated with a fresh protected-regression failure. Its exact scope must fail closed while the
    other heterogeneous capabilities and the recursive skill remain callable in distinct fresh Engines
    and keep byte-identical trial ledgers. Re-validating the unchanged target must recover only that
    scope, after which all retained capabilities replay correctly and the rollback failure remains as
    contradictory evidence.

    This is repository-authored deterministic development evidence only. It tests rollback isolation,
    no-forgetting, and reversible scoped activation; it cannot satisfy the independent production AGI
    evidence gate.
    """
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    base = run_cross_family_order_retention(root, f"{seed}:base")
    if not base.get("passed"):
        raise CrossFamilyRollbackIsolationError(
            "cross-family prerequisite did not pass"
        )

    heterogeneous_ids = [str(item) for item in base["heterogeneous_candidate_ids"]]
    if len(heterogeneous_ids) != 4:
        raise CrossFamilyRollbackIsolationError(
            "rollback isolation requires the four heterogeneous capabilities"
        )
    task_specs = _task_specs()
    if len(task_specs) != len(heterogeneous_ids):
        raise CrossFamilyRollbackIsolationError(
            "heterogeneous Candidate family no longer matches task specifications"
        )

    rollback_index = 1
    target_id = heterogeneous_ids[rollback_index]
    target_spec = _candidate_spec(root, target_id)
    target_task_spec = task_specs[rollback_index]
    unaffected_pairs = [
        (candidate_id, task_spec)
        for index, (candidate_id, task_spec) in enumerate(
            zip(heterogeneous_ids, task_specs, strict=True)
        )
        if index != rollback_index
    ]

    recursive_id = str(base["recursive_candidate_id"])
    recursive_component_ids = [
        str(item) for item in base["recursive_component_candidate_ids"]
    ]
    recursive_ids = [*recursive_component_ids, recursive_id]
    stable_ids = [
        *(candidate_id for candidate_id, _ in unaffected_pairs),
        *recursive_ids,
    ]
    stable_trials_before = _trial_snapshots(root, stable_ids)
    target_trials_before = _trial_snapshots(root, [target_id])[target_id]

    candidate_dir = root / ".continual" / "candidates" / target_id
    regression_input = candidate_dir / "regression-input"
    baseline_path = regression_input / "baseline.json"
    valid_path = regression_input / "candidate-valid.json"
    baseline = _load_measurements(baseline_path)
    valid = _load_measurements(valid_path)

    target_task_id = next(
        str(item["task_id"])
        for item in valid
        if str(item.get("task_id", "")).startswith("withheld-")
    )
    protected_index = next(
        index
        for index, item in enumerate(valid)
        if str(item.get("task_id", "")).startswith("protected-before-")
    )
    rollback_measurements = [dict(item) for item in valid]
    rollback_measurements[protected_index] = {
        **rollback_measurements[protected_index],
        "passed": False,
        "score": 0.0,
        "artifact_sha256": _digest(
            {
                "campaign": "cross-family-rollback-isolation-v1",
                "seed": seed,
                "candidate_id": target_id,
                "scope": target_spec.scope,
                "forced_protected_drop": True,
            }
        ),
    }
    rollback_path = regression_input / "candidate-cross-family-rollback.json"
    _atomic_json(rollback_path, rollback_measurements)

    rollback = record_candidate_regression(
        root,
        candidate_id=target_id,
        scope=target_spec.scope,
        baseline_path=baseline_path,
        candidate_path=rollback_path,
        target_task_ids=[target_task_id],
    )
    if rollback["decision"]["adopt_candidate"]:
        raise CrossFamilyRollbackIsolationError(
            "forced protected regression failed to revoke target scope"
        )

    blocked_engine = _mechanical_engine(root)
    blocked_run_id = _new_runtime_run(blocked_engine)
    target_failed_closed = False
    try:
        _invoke(
            blocked_engine,
            blocked_run_id,
            scope=target_spec.scope,
            tool_id=target_spec.tool_id,
            value=target_task_spec["probe"],
        )
    except (AcquiredProgramToolError, LearnedToolError):
        target_failed_closed = True
    if not target_failed_closed:
        raise CrossFamilyRollbackIsolationError(
            "revoked heterogeneous scope remained callable"
        )

    fresh_runs: list[str] = [blocked_run_id]
    for candidate_id, task_spec in unaffected_pairs:
        fresh_runs.append(
            _run_one(
                root,
                candidate_id=candidate_id,
                value=task_spec["probe"],
                expected=task_spec["expected"],
            )
        )

    recursive_probe = {"a": False, "rollback": 51}
    recursive_expected = not _target_answer(recursive_probe)
    fresh_runs.append(
        _run_one(
            root,
            candidate_id=recursive_id,
            value=recursive_probe,
            expected=recursive_expected,
        )
    )

    stable_trials_after_rollback = _trial_snapshots(root, stable_ids)
    stable_trials_unchanged_during_rollback = (
        stable_trials_after_rollback == stable_trials_before
    )
    if not stable_trials_unchanged_during_rollback:
        raise CrossFamilyRollbackIsolationError(
            "target rollback rewrote another family's Candidate trial evidence"
        )

    recovery = record_candidate_regression(
        root,
        candidate_id=target_id,
        scope=target_spec.scope,
        baseline_path=baseline_path,
        candidate_path=valid_path,
        target_task_ids=[target_task_id],
    )
    if not recovery["decision"]["adopt_candidate"]:
        raise CrossFamilyRollbackIsolationError(
            "unchanged heterogeneous target did not recover after re-validation"
        )

    recovered_target_run = _run_one(
        root,
        candidate_id=target_id,
        value=target_task_spec["probe"],
        expected=target_task_spec["expected"],
    )
    fresh_runs.append(recovered_target_run)

    for candidate_id, task_spec in unaffected_pairs:
        fresh_runs.append(
            _run_one(
                root,
                candidate_id=candidate_id,
                value=task_spec["probe"],
                expected=task_spec["expected"],
            )
        )
    fresh_runs.append(
        _run_one(
            root,
            candidate_id=recursive_id,
            value={"b": 61, "rollback": 62},
            expected=True,
        )
    )

    if len(set(fresh_runs)) != len(fresh_runs):
        raise CrossFamilyRollbackIsolationError(
            "rollback isolation reused a fresh Engine run"
        )

    stable_trials_after_recovery = _trial_snapshots(root, stable_ids)
    stable_trials_unchanged_after_recovery = (
        stable_trials_after_recovery == stable_trials_before
    )
    if not stable_trials_unchanged_after_recovery:
        raise CrossFamilyRollbackIsolationError(
            "target recovery rewrote another family's Candidate trial evidence"
        )

    target_trials_after = _trial_snapshots(root, [target_id])[target_id]
    new_target_trial_records = sorted(
        set(target_trials_after) - set(target_trials_before)
    )
    if len(new_target_trial_records) != 1:
        raise CrossFamilyRollbackIsolationError(
            "rollback did not retain exactly one new target regression record"
        )

    target_state = json.loads((candidate_dir / "candidate.json").read_text(encoding="utf-8"))
    contradictory = target_state.get("contradictory_evidence", [])
    rollback_negative_retained = any(
        isinstance(item, dict)
        and item.get("type") == "deterministic_regression_gate_failure"
        and item.get("scope") == target_spec.scope
        and item.get("decision_ref") == rollback["decision_ref"]
        for item in contradictory
    )
    if not rollback_negative_retained:
        raise CrossFamilyRollbackIsolationError(
            "rollback failure was not retained as contradictory evidence"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "cross-family-rollback-isolation-v1",
        "target_candidate_id": target_id,
        "target_scope": target_spec.scope,
        "unaffected_heterogeneous_candidate_ids": [
            candidate_id for candidate_id, _ in unaffected_pairs
        ],
        "recursive_candidate_id": recursive_id,
        "recursive_component_candidate_ids": recursive_component_ids,
        "rollback_adopted": bool(rollback["decision"]["adopt_candidate"]),
        "rollback_decision_ref": rollback["decision_ref"],
        "revoked_scope_failed_closed": target_failed_closed,
        "stable_trials_unchanged_during_rollback": (
            stable_trials_unchanged_during_rollback
        ),
        "recovery_adopted": bool(recovery["decision"]["adopt_candidate"]),
        "recovery_decision_ref": recovery["decision_ref"],
        "stable_trials_unchanged_after_recovery": (
            stable_trials_unchanged_after_recovery
        ),
        "rollback_negative_evidence_retained": rollback_negative_retained,
        "new_target_trial_record_count": len(new_target_trial_records),
        "fresh_engine_run_count": len(fresh_runs),
        "fresh_engine_runs": fresh_runs,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal deterministic cross-family rollback-isolation evidence only. One exact "
            "heterogeneous scope can be revoked by a protected-regression failure and later recovered "
            "by deterministic re-validation while unrelated heterogeneous and recursive-memory skills "
            "remain callable and keep byte-identical trial ledgers. Repository-authored tasks, runtime, "
            "and evaluators do not establish independent production AGI."
        ),
    }
    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "cross-family-rollback-isolation"
        / f"rollback-{_digest(seed)[:16]}.json",
        report,
    )
    return report
