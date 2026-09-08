from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from continual.acquired_program_tools import AcquiredProgramToolError
from continual.candidate_regression import record_candidate_regression
from continual.learned_tools import LearnedToolError
from continual.learning_engine import LearningEnabledEngine

from .acquired_program_runtime import (
    _atomic_json,
    _digest,
    _new_runtime_run,
    run_acquired_program_runtime_campaign,
)
from .acquired_programs import execute_program
from .generated_crossdomain_cegis import (
    _prior_retest,
    _runtime_call,
    generated_crossdomain_target,
    run_generated_crossdomain_cegis_campaign,
)


def _execute_durable_work_unit(
    root: Path,
    *,
    scope: str,
    tool_id: str,
    inputs: tuple[Any, ...],
    expected: tuple[Any, ...],
    engine_factory: Callable[[Path], LearningEnabledEngine],
) -> dict[str, Any]:
    engine = engine_factory(root)
    run_id = _new_runtime_run(engine)
    results: list[dict[str, Any]] = []
    for value, answer_expected in zip(inputs, expected, strict=True):
        answer = _runtime_call(
            engine,
            run_id,
            scope=scope,
            tool_id=tool_id,
            value=value,
        )
        results.append(
            {
                "input_sha256": _digest(value),
                "output_sha256": _digest(answer),
                "success": answer == answer_expected,
            }
        )
    run_dir = engine.store.run_dir(run_id)
    return {
        "run_id": run_id,
        "run_persisted": run_dir.is_dir() and (run_dir / "snapshot.json").is_file(),
        "score": sum(1.0 if item["success"] else 0.0 for item in results) / len(results),
        "results": results,
    }


def run_durable_runtime_transfer_campaign(
    root: Path,
    seed: str,
    *,
    engine_factory: Callable[[Path], LearningEnabledEngine] = LearningEnabledEngine,
) -> dict[str, Any]:
    """Exercise learned capability transfer, fail-closed rollback, and recovery across durable runs.

    The target capability is acquired by the existing sealed generated-crossdomain CEGIS gate. It is
    then used in two separately persisted runtime runs. A fresh deterministic protected-baseline
    regression record revokes the target's exact scope; a new runtime must fail closed while the
    previously protected capability remains callable. Re-validating the unchanged target restores
    only that exact scope, after which another fresh runtime must recover the learned behavior.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be a non-empty string")
    root = root.resolve()
    acquisition = run_generated_crossdomain_cegis_campaign(root, seed, engine_factory=engine_factory)
    if not acquisition["passed"]:
        raise RuntimeError("generated cross-domain prerequisite did not pass")

    target, _, _, _, final_inputs = generated_crossdomain_target(seed)
    expected = tuple(execute_program(target, value) for value in final_inputs)
    scope = str(acquisition["scope"])
    tool_id = str(acquisition["tool_id"])
    candidate_id = str(acquisition["candidate_id"])

    durable_runs = [
        _execute_durable_work_unit(
            root,
            scope=scope,
            tool_id=tool_id,
            inputs=final_inputs,
            expected=expected,
            engine_factory=engine_factory,
        )
        for _ in range(2)
    ]
    if len({item["run_id"] for item in durable_runs}) != 2:
        raise RuntimeError("durable work executions did not receive distinct run IDs")
    if not all(item["run_persisted"] and item["score"] == 1.0 for item in durable_runs):
        raise RuntimeError("learned capability did not transfer across durable executions")

    prior_seed = "generated-crossdomain-protected-prior-v1"
    prior_report = run_acquired_program_runtime_campaign(
        root,
        prior_seed,
        engine_factory=engine_factory,
    )
    if not prior_report["passed"]:
        raise RuntimeError("protected prior replay did not pass")
    protected_before = _prior_retest(root, prior_seed, prior_report, engine_factory=engine_factory)
    if protected_before["score"] != 1.0:
        raise RuntimeError("protected prior was not intact before rollback trial")

    candidate_dir = root / ".continual" / "candidates" / candidate_id
    regression_dir = candidate_dir / "regression-input"
    baseline_path = regression_dir / "baseline.json"
    original_valid_path = regression_dir / "candidate-valid.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    valid = json.loads(original_valid_path.read_text(encoding="utf-8"))
    if not isinstance(baseline, list) or not isinstance(valid, list) or len(valid) < 2:
        raise RuntimeError("generated cross-domain regression inputs are malformed")

    target_task = next(
        str(item["task_id"])
        for item in valid
        if isinstance(item, dict) and str(item.get("task_id", "")).startswith("withheld-generated-crossdomain-")
    )
    rollback_measurements = [dict(item) for item in valid]
    protected_index = next(
        index
        for index, item in enumerate(rollback_measurements)
        if item.get("task_id") == "protected-prior-generated-crossdomain"
    )
    rollback_measurements[protected_index] = {
        **rollback_measurements[protected_index],
        "passed": False,
        "score": 0.0,
        "artifact_sha256": _digest(
            {
                "phase": "durable-runtime-protected-regression",
                "candidate_id": candidate_id,
                "scope": scope,
            }
        ),
    }
    rollback_path = regression_dir / "candidate-durable-rollback.json"
    _atomic_json(rollback_path, rollback_measurements)

    rollback = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline_path,
        candidate_path=rollback_path,
        target_task_ids=[target_task],
    )
    if rollback["decision"]["adopt_candidate"]:
        raise RuntimeError("protected regression did not revoke the target Candidate")

    blocked_engine = engine_factory(root)
    blocked_run_id = _new_runtime_run(blocked_engine)
    fail_closed = False
    try:
        _runtime_call(
            blocked_engine,
            blocked_run_id,
            scope=scope,
            tool_id=tool_id,
            value=final_inputs[0],
        )
    except (LearnedToolError, AcquiredProgramToolError):
        fail_closed = True
    if not fail_closed:
        raise RuntimeError("revoked learned capability remained callable")

    protected_after_rollback = _prior_retest(
        root,
        prior_seed,
        prior_report,
        engine_factory=engine_factory,
    )
    if protected_after_rollback["score"] != 1.0:
        raise RuntimeError("rollback damaged the protected prior capability")

    recovery = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline_path,
        candidate_path=original_valid_path,
        target_task_ids=[target_task],
    )
    if not recovery["decision"]["adopt_candidate"]:
        raise RuntimeError("unchanged target did not recover after deterministic re-validation")

    recovered_run = _execute_durable_work_unit(
        root,
        scope=scope,
        tool_id=tool_id,
        inputs=final_inputs,
        expected=expected,
        engine_factory=engine_factory,
    )
    state = json.loads((candidate_dir / "candidate.json").read_text(encoding="utf-8"))
    negative_evidence = state.get("contradictory_evidence", [])
    report = {
        "schema_version": 1,
        "campaign_id": f"durable-runtime-transfer-{_digest({'seed': seed, 'candidate': candidate_id})[:16]}",
        "candidate_id": candidate_id,
        "scope": scope,
        "tool_id": tool_id,
        "seed_sha256": _digest(seed),
        "seed_persisted": False,
        "acquisition_passed": acquisition["passed"],
        "durable_run_ids": [item["run_id"] for item in durable_runs],
        "durable_runs_persisted": all(item["run_persisted"] for item in durable_runs),
        "durable_transfer_scores": [item["score"] for item in durable_runs],
        "rollback_adopted": rollback["decision"]["adopt_candidate"],
        "rollback_decision_ref": rollback["decision_ref"],
        "revoked_runtime_run_id": blocked_run_id,
        "revoked_scope_failed_closed": fail_closed,
        "protected_prior_score_before_rollback": protected_before["score"],
        "protected_prior_score_after_rollback": protected_after_rollback["score"],
        "recovery_adopted": recovery["decision"]["adopt_candidate"],
        "recovery_decision_ref": recovery["decision_ref"],
        "recovered_run_id": recovered_run["run_id"],
        "recovered_run_persisted": recovered_run["run_persisted"],
        "recovered_score": recovered_run["score"],
        "negative_evidence_retained": any(
            isinstance(item, dict)
            and item.get("type") == "deterministic_regression_gate_failure"
            and item.get("scope") == scope
            for item in negative_evidence
        ),
        "claim_boundary": (
            "Internal durable-runtime transfer and rollback evidence only. The task generator, "
            "regression measurements, and evaluator remain internal and do not prove AGI."
        ),
    }
    report["passed"] = bool(
        report["acquisition_passed"]
        and report["durable_runs_persisted"]
        and report["durable_transfer_scores"] == [1.0, 1.0]
        and report["rollback_adopted"] is False
        and report["revoked_scope_failed_closed"]
        and report["protected_prior_score_before_rollback"] == 1.0
        and report["protected_prior_score_after_rollback"] == 1.0
        and report["recovery_adopted"] is True
        and report["recovered_run_persisted"]
        and report["recovered_score"] == 1.0
        and report["negative_evidence_retained"]
    )
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root / ".continual" / "evidence" / "durable-runtime-transfer" / f"{report['campaign_id']}.json",
        report,
    )
    return report
