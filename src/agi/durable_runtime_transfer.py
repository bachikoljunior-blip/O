from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from continual.acquired_program_tools import AcquiredProgramToolError
from continual.candidate_control import (
    resume_verified_candidate_scope,
    suspend_verified_candidate_scope,
)
from continual.learned_tools import LearnedToolError
from continual.learning_engine import LearningEnabledEngine

from .acquired_program_runtime import (
    ACQUIRED_RUNTIME_SCOPE,
    _atomic_json,
    _digest,
    _hidden_descriptor,
    _inputs,
    _new_runtime_run,
    _runtime_call as _prior_runtime_call,
    run_acquired_program_runtime_campaign,
)
from .acquired_programs import execute_program
from .generated_crossdomain_cegis import (
    GENERATED_CROSSDOMAIN_SCOPE_PREFIX,
    _GENERATOR_VERSION,
    _runtime_call,
    generated_crossdomain_target,
    run_generated_crossdomain_cegis_campaign,
)

_PROTECTED_PRIOR_SEED = "generated-crossdomain-protected-prior-v1"


def _trial_snapshot(root: Path, candidate_id: str) -> dict[str, str]:
    trial_dir = root / ".continual" / "candidates" / candidate_id / "trials"
    return {
        path.name: _digest(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(trial_dir.glob("*.json"))
    }


def _target_identity(seed: str) -> dict[str, Any]:
    target, meta, initial_inputs, diagnostic_inputs, final_inputs = generated_crossdomain_target(seed)
    diagnostic_expected = tuple(execute_program(target, value) for value in diagnostic_inputs)
    final_expected = tuple(execute_program(target, value) for value in final_inputs)
    commitment_payload = {
        "generator_version": _GENERATOR_VERSION,
        "seed_sha256": _digest(seed),
        "target": target,
        "diagnostic": [
            {"input": query, "expected": expected}
            for query, expected in zip(diagnostic_inputs, diagnostic_expected, strict=True)
        ],
        "final": [
            {"input": query, "expected": expected}
            for query, expected in zip(final_inputs, final_expected, strict=True)
        ],
    }
    commitment = _digest(commitment_payload)
    token = commitment[:16]
    return {
        "target": target,
        "meta": meta,
        "initial_inputs": initial_inputs,
        "diagnostic_inputs": diagnostic_inputs,
        "final_inputs": final_inputs,
        "commitment_payload": commitment_payload,
        "commitment": commitment,
        "candidate_id": f"generated-crossdomain-{token}",
        "tool_id": f"generated-crossdomain-tool-{token}",
        "scope": f"{GENERATED_CROSSDOMAIN_SCOPE_PREFIX}/{token}",
    }


def _unseen_inputs(input_domain: str) -> tuple[Any, ...]:
    if input_domain == "string":
        return (
            "DurableRuntimeProbe",
            "novel-transfer-work-unit",
            "ZyXwVuTsRqPoNmLkJiHgFeDcBa",
        )
    if input_domain == "sequence":
        return (
            [10, -1, 2],
            [-8, 3, 9, 4],
            [0, 1, 2, 3, 4, 5, 6],
        )
    raise ValueError(f"unsupported durable transfer input domain: {input_domain}")


def _work_unit_commitment(target: Mapping[str, Any], inputs: Sequence[Any]) -> tuple[str, tuple[Any, ...]]:
    expected = tuple(execute_program(target, value) for value in inputs)
    payload = {
        "target_sha256": _digest(target),
        "units": [
            {"input_sha256": _digest(value), "expected_sha256": _digest(answer)}
            for value, answer in zip(inputs, expected, strict=True)
        ],
    }
    return _digest(payload), expected


def _execute_generated_units(
    root: Path,
    *,
    scope: str,
    tool_id: str,
    values: Sequence[Any],
    expected: Sequence[Any],
    engine_factory: Callable[[Path], LearningEnabledEngine],
) -> dict[str, Any]:
    engine = engine_factory(root)
    run_id = _new_runtime_run(engine)
    results: list[dict[str, Any]] = []
    for value, answer_expected in zip(values, expected, strict=True):
        try:
            answer = _runtime_call(
                engine,
                run_id,
                scope=scope,
                tool_id=tool_id,
                value=value,
            )
            available = True
            success = answer == answer_expected
            answer_sha256 = _digest(answer)
        except (LearnedToolError, AcquiredProgramToolError):
            available = False
            success = False
            answer_sha256 = None
        results.append(
            {
                "input_sha256": _digest(value),
                "answer_sha256": answer_sha256,
                "available": available,
                "success": success,
            }
        )
    return {
        "run_id": run_id,
        "score": sum(1.0 if item["success"] else 0.0 for item in results) / len(results),
        "available_count": sum(1 for item in results if item["available"]),
        "results": results,
    }


def _execute_protected_prior(
    root: Path,
    prior: Mapping[str, Any],
    *,
    engine_factory: Callable[[Path], LearningEnabledEngine],
) -> dict[str, Any]:
    hidden = _hidden_descriptor(_PROTECTED_PRIOR_SEED)
    _, heldout = _inputs(str(hidden["input_domain"]))
    expected = tuple(execute_program(hidden, value) for value in heldout)
    engine = engine_factory(root)
    run_id = _new_runtime_run(engine)
    results: list[dict[str, Any]] = []
    for value, answer_expected in zip(heldout, expected, strict=True):
        output = _prior_runtime_call(
            engine,
            run_id,
            tool_id=str(prior["tool_id"]),
            value=value,
        )
        answer = output["result"]["output"]
        results.append(
            {
                "input_sha256": _digest(value),
                "answer_sha256": _digest(answer),
                "success": answer == answer_expected,
            }
        )
    return {
        "run_id": run_id,
        "score": sum(1.0 if item["success"] else 0.0 for item in results) / len(results),
        "results": results,
    }


def run_durable_runtime_transfer_campaign(
    root: Path,
    seed: str,
    *,
    engine_factory: Callable[[Path], LearningEnabledEngine] = LearningEnabledEngine,
) -> dict[str, Any]:
    """Measure durable before/after transfer on evaluator-committed unseen runtime work units.

    Work-unit input/answer digests are committed before the target capability is acquired. The same
    work units are then attempted in separate persistent Engine runs before acquisition, after
    regression-gated acquisition, while the target scope is reversibly suspended, and after its exact
    original regression binding is re-verified and resumed. A pre-existing protected capability is
    executed throughout, and neither target nor protected Candidate trial ledgers may change during
    runtime retests or reversible availability control.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be a non-empty string")
    root = root.resolve()
    identity = _target_identity(seed)
    target = identity["target"]
    meta = identity["meta"]
    unseen_inputs = _unseen_inputs(str(meta["input_domain"]))
    seen_inputs = [
        *identity["initial_inputs"],
        *identity["diagnostic_inputs"],
        *identity["final_inputs"],
    ]
    seen_digests = {_digest(value) for value in seen_inputs}
    unseen_digests = {_digest(value) for value in unseen_inputs}
    if seen_digests & unseen_digests:
        raise RuntimeError("durable transfer inputs overlap acquisition probes")
    work_commitment, unseen_expected = _work_unit_commitment(target, unseen_inputs)

    prior = run_acquired_program_runtime_campaign(
        root,
        _PROTECTED_PRIOR_SEED,
        engine_factory=engine_factory,
    )
    if not prior["passed"]:
        raise RuntimeError("protected prior capability did not pass before durable transfer")
    prior_trials_before = _trial_snapshot(root, str(prior["candidate_id"]))
    protected_before = _execute_protected_prior(root, prior, engine_factory=engine_factory)

    baseline = _execute_generated_units(
        root,
        scope=str(identity["scope"]),
        tool_id=str(identity["tool_id"]),
        values=unseen_inputs,
        expected=unseen_expected,
        engine_factory=engine_factory,
    )
    if baseline["available_count"] != 0 or baseline["score"] != 0.0:
        raise RuntimeError("target capability was available before acquisition")

    acquisition = run_generated_crossdomain_cegis_campaign(
        root,
        seed,
        engine_factory=engine_factory,
    )
    if not acquisition["passed"]:
        raise RuntimeError("generated target capability failed acquisition")
    if (
        acquisition["candidate_id"] != identity["candidate_id"]
        or acquisition["tool_id"] != identity["tool_id"]
        or acquisition["scope"] != identity["scope"]
        or acquisition["commitment"] != identity["commitment"]
    ):
        raise RuntimeError("acquired target identity diverged from precommitted work units")

    target_trials_after_acquisition = _trial_snapshot(root, str(acquisition["candidate_id"]))
    post_acquisition = _execute_generated_units(
        root,
        scope=str(acquisition["scope"]),
        tool_id=str(acquisition["tool_id"]),
        values=unseen_inputs,
        expected=unseen_expected,
        engine_factory=engine_factory,
    )
    protected_after_acquisition = _execute_protected_prior(
        root,
        prior,
        engine_factory=engine_factory,
    )

    suspend = suspend_verified_candidate_scope(
        root,
        candidate_id=str(acquisition["candidate_id"]),
        scope=str(acquisition["scope"]),
        reason="durable runtime fail-closed rollback probe",
    )
    suspended = _execute_generated_units(
        root,
        scope=str(acquisition["scope"]),
        tool_id=str(acquisition["tool_id"]),
        values=unseen_inputs,
        expected=unseen_expected,
        engine_factory=engine_factory,
    )
    protected_while_suspended = _execute_protected_prior(
        root,
        prior,
        engine_factory=engine_factory,
    )

    resume = resume_verified_candidate_scope(
        root,
        candidate_id=str(acquisition["candidate_id"]),
        scope=str(acquisition["scope"]),
        reason="durable runtime exact-binding resume after rollback probe",
    )
    resumed = _execute_generated_units(
        root,
        scope=str(acquisition["scope"]),
        tool_id=str(acquisition["tool_id"]),
        values=unseen_inputs,
        expected=unseen_expected,
        engine_factory=engine_factory,
    )
    protected_after_resume = _execute_protected_prior(
        root,
        prior,
        engine_factory=engine_factory,
    )

    target_trials_after_controls = _trial_snapshot(root, str(acquisition["candidate_id"]))
    prior_trials_after = _trial_snapshot(root, str(prior["candidate_id"]))
    all_run_ids = [
        protected_before["run_id"],
        baseline["run_id"],
        post_acquisition["run_id"],
        protected_after_acquisition["run_id"],
        suspended["run_id"],
        protected_while_suspended["run_id"],
        resumed["run_id"],
        protected_after_resume["run_id"],
    ]
    work_commitment_recomputed, _ = _work_unit_commitment(target, unseen_inputs)

    report = {
        "schema_version": 1,
        "campaign_id": f"durable-runtime-transfer-{identity['commitment'][:16]}",
        "candidate_id": acquisition["candidate_id"],
        "tool_id": acquisition["tool_id"],
        "scope": acquisition["scope"],
        "family": acquisition["family"],
        "input_domain": acquisition["input_domain"],
        "output_domain": acquisition["output_domain"],
        "seed_value_persisted": False,
        "raw_work_unit_inputs_persisted": False,
        "raw_expected_answers_persisted": False,
        "acquisition_probe_overlap": False,
        "work_unit_commitment": work_commitment,
        "work_unit_commitment_verified": work_commitment_recomputed == work_commitment,
        "work_unit_input_sha256": [_digest(value) for value in unseen_inputs],
        "baseline": baseline,
        "acquisition_passed": acquisition["passed"],
        "post_acquisition": post_acquisition,
        "suspension": suspend,
        "while_suspended": suspended,
        "resume": resume,
        "after_resume": resumed,
        "protected_prior_candidate_id": prior["candidate_id"],
        "protected_before": protected_before,
        "protected_after_acquisition": protected_after_acquisition,
        "protected_while_target_suspended": protected_while_suspended,
        "protected_after_resume": protected_after_resume,
        "target_trial_budget_unchanged_by_runtime_and_control": (
            target_trials_after_acquisition == target_trials_after_controls
        ),
        "protected_prior_trial_budget_unchanged": prior_trials_before == prior_trials_after,
        "separate_durable_run_count": len(set(all_run_ids)),
        "run_ids": all_run_ids,
        "claim_boundary": (
            "Internal durable-runtime transfer and reversible-control evidence only. The evaluator, "
            "task generator, and evidence remain internally produced; this is not independent "
            "production evidence and does not prove AGI."
        ),
    }
    report["passed"] = bool(
        report["work_unit_commitment_verified"]
        and baseline["score"] == 0.0
        and baseline["available_count"] == 0
        and report["acquisition_passed"]
        and post_acquisition["score"] == 1.0
        and protected_before["score"] == 1.0
        and protected_after_acquisition["score"] == 1.0
        and suspend["state"] == "SUSPENDED_FOR_SCOPE"
        and suspended["score"] == 0.0
        and suspended["available_count"] == 0
        and protected_while_suspended["score"] == 1.0
        and resume["state"] == "VERIFIED_FOR_SCOPE"
        and resumed["score"] == 1.0
        and protected_after_resume["score"] == 1.0
        and report["target_trial_budget_unchanged_by_runtime_and_control"]
        and report["protected_prior_trial_budget_unchanged"]
        and report["separate_durable_run_count"] == len(all_run_ids)
    )
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "durable-runtime-transfer"
        / f"{report['campaign_id']}.json",
        report,
    )
    return report
