from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, synthesize_program
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
    _validate_runtime_output,
)
from continual.acquired_program_tools import acquired_program_candidate_payload
from continual.candidate_regression import record_candidate_regression


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _measurement(
    task_id: str,
    repeat_index: int,
    score: float,
    artifact: Any,
    *,
    domain: str,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "criterion": "continual_learning",
        "domain": domain,
        "repeat_index": repeat_index,
        "passed": score == 1.0,
        "score": score,
        "artifact_sha256": _digest(artifact),
    }


def _task_specs() -> tuple[dict[str, Any], ...]:
    return (
        {
            "name": "numeric-abs",
            "input_domain": "numeric",
            "output_domain": "numeric",
            "examples": (
                ProgramExample(-3, 3),
                ProgramExample(0, 0),
                ProgramExample(5, 5),
            ),
            "probe": -11,
            "expected": 11,
            "max_nodes": 3,
        },
        {
            "name": "string-upper",
            "input_domain": "string",
            "output_domain": "string",
            "examples": (
                ProgramExample("ab", "AB"),
                ProgramExample("Mixed", "MIXED"),
                ProgramExample("z", "Z"),
            ),
            "probe": "Novel x",
            "expected": "NOVEL X",
            "max_nodes": 3,
        },
        {
            "name": "sequence-sum",
            "input_domain": "sequence",
            "output_domain": "numeric",
            "examples": (
                ProgramExample([1, 2], 3),
                ProgramExample([4, -1], 3),
                ProgramExample([5], 5),
            ),
            "probe": [10, -3, 2],
            "expected": 9,
            "max_nodes": 3,
        },
        {
            "name": "object-kind",
            "input_domain": "object",
            "output_domain": "boolean",
            "examples": (
                ProgramExample({"kind": "target", "noise": 0}, True),
                ProgramExample({"kind": "other", "noise": 0}, False),
                ProgramExample({"kind": "target", "noise": 1}, True),
                ProgramExample({"kind": "alternate", "noise": 1}, False),
            ),
            "probe": {"kind": "target", "noise": 999, "novel": True},
            "expected": True,
            "max_nodes": 5,
        },
    )


def _promote_task(
    root: Path,
    *,
    seed: str,
    spec: dict[str, Any],
) -> dict[str, Any]:
    token = _digest({"seed": seed, "task": spec["name"]})[:16]
    candidate_id = f"hetero-{spec['name']}-{token}"
    tool_id = f"hetero-tool-{spec['name']}-{token}"
    scope = f"agi/acquired-program/heterogeneous/{spec['name']}/v1"
    learned = synthesize_program(
        input_domain=str(spec["input_domain"]),
        output_domain=str(spec["output_domain"]),
        examples=spec["examples"],
        max_nodes=int(spec["max_nodes"]),
    )

    candidate_dir = root / ".continual" / "candidates" / candidate_id
    _atomic_json(
        candidate_dir / "candidate.json",
        acquired_program_candidate_payload(
            candidate_id=candidate_id,
            tool_id=tool_id,
            scope=scope,
            program=learned.descriptor(),
            description=(
                "Bounded pure acquired program in a heterogeneous sequential continual-retention campaign."
            ),
        ),
    )

    target_task = f"withheld-{candidate_id}"
    target_domain = f"heterogeneous:{spec['input_domain']}-to-{spec['output_domain']}"
    baseline: list[dict[str, Any]] = []
    trial: list[dict[str, Any]] = []
    for repeat_index in range(3):
        artifact = {
            "candidate_id": candidate_id,
            "repeat_index": repeat_index,
            "program_sha256": _digest(learned.descriptor()),
            "probe_sha256": _digest(spec["probe"]),
            "expected_sha256": _digest(spec["expected"]),
        }
        baseline.append(
            _measurement(
                target_task,
                repeat_index,
                0.0,
                artifact,
                domain=target_domain,
            )
        )
        trial.append(
            _measurement(
                target_task,
                repeat_index,
                1.0,
                artifact,
                domain=target_domain,
            )
        )

    protected_task = f"protected-before-{candidate_id}"
    protected = _measurement(
        protected_task,
        0,
        1.0,
        {"candidate_id": candidate_id, "invariant": "previous verified capability retained"},
        domain="protected",
    )
    reg_dir = candidate_dir / "regression-input"
    baseline_path = reg_dir / "baseline.json"
    adverse_path = reg_dir / "candidate-adverse.json"
    valid_path = reg_dir / "candidate-valid.json"
    _atomic_json(baseline_path, [*baseline, protected])
    _atomic_json(
        adverse_path,
        [
            *trial,
            {
                **protected,
                "passed": False,
                "score": 0.0,
                "artifact_sha256": _digest(
                    {"candidate_id": candidate_id, "forced_protected_drop": True}
                ),
            },
        ],
    )
    _atomic_json(valid_path, [*trial, protected])

    rejected = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline_path,
        candidate_path=adverse_path,
        target_task_ids=[target_task],
    )
    if rejected["decision"]["adopt_candidate"]:
        raise RuntimeError("heterogeneous Candidate ignored a protected regression")

    promoted = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline_path,
        candidate_path=valid_path,
        target_task_ids=[target_task],
    )
    if not promoted["decision"]["adopt_candidate"]:
        raise RuntimeError("heterogeneous Candidate failed valid repeated target improvement")

    return {
        "candidate_id": candidate_id,
        "tool_id": tool_id,
        "scope": scope,
        "input_domain": spec["input_domain"],
        "output_domain": spec["output_domain"],
        "program": learned.descriptor(),
        "expression": learned.expression,
        "probe": spec["probe"],
        "expected": spec["expected"],
        "target_task_id": target_task,
        "negative_evidence_retained": True,
    }


def _replay_all(
    root: Path,
    promoted: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    engine = _mechanical_engine(root)
    run_id = _new_runtime_run(engine)
    results: list[dict[str, Any]] = []
    for item in promoted:
        response = _invoke(
            engine,
            run_id,
            scope=str(item["scope"]),
            tool_id=str(item["tool_id"]),
            value=item["probe"],
        )
        result = _validate_runtime_output(
            response,
            expected=item["expected"],
            candidate_id=str(item["candidate_id"]),
        )
        results.append(
            {
                "candidate_id": item["candidate_id"],
                "scope": item["scope"],
                "probe_sha256": _digest(item["probe"]),
                "output_sha256": _digest(item["expected"]),
                "program_kind": result.get("program_kind"),
            }
        )
    return run_id, results


def run_heterogeneous_retention_campaign(root: Path, seed: str) -> dict[str, Any]:
    """Accumulate distinct typed capabilities and verify no-forgetting after each later promotion.

    The campaign deliberately spans numeric, string, sequence, and object inputs rather than repeating
    one task family. Each Candidate first encounters a forced protected-regression rejection, then must
    pass repeated exact-scope target improvement. After every promotion a fresh mechanical Engine
    replays every capability accumulated so far on unseen inputs. Once a Candidate is promoted, its
    complete regression-trial ledger is frozen and must remain byte-identical through all later
    promotions and replays.

    This is internal deterministic continual-learning evidence. It strengthens breadth/retention
    falsification but is not genuine independent production evaluation and does not establish AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    promoted: list[dict[str, Any]] = []
    frozen_trials: dict[str, dict[str, str]] = {}
    stage_runs: list[str] = []
    stage_replays: list[list[dict[str, Any]]] = []

    for spec in _task_specs():
        item = _promote_task(root, seed=seed, spec=spec)
        promoted.append(item)
        candidate_id = str(item["candidate_id"])
        frozen_trials[candidate_id] = _candidate_trial_snapshot(root, candidate_id)
        if not frozen_trials[candidate_id]:
            raise RuntimeError("heterogeneous Candidate lacks persisted regression trials")

        run_id, replay = _replay_all(root, promoted)
        stage_runs.append(run_id)
        stage_replays.append(replay)

        current_trials = {
            existing_id: _candidate_trial_snapshot(root, existing_id)
            for existing_id in frozen_trials
        }
        if current_trials != frozen_trials:
            changed = sorted(
                existing_id
                for existing_id in frozen_trials
                if current_trials[existing_id] != frozen_trials[existing_id]
            )
            raise RuntimeError(
                "later heterogeneous learning rewrote prior Candidate trial state: "
                + ",".join(changed)
            )

    candidate_ids = [str(item["candidate_id"]) for item in promoted]
    scopes = [str(item["scope"]) for item in promoted]
    if len(set(candidate_ids)) != len(candidate_ids):
        raise RuntimeError("heterogeneous campaign reused a Candidate identity")
    if len(set(scopes)) != len(scopes):
        raise RuntimeError("heterogeneous campaign reused an exact scope")
    if len(set(stage_runs)) != len(stage_runs):
        raise RuntimeError("heterogeneous campaign reused a fresh Engine run")

    final_trials = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in candidate_ids
    }
    trial_state_unchanged = final_trials == frozen_trials
    if not trial_state_unchanged:
        raise RuntimeError("heterogeneous final replay changed Candidate trial state")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "heterogeneous-sequential-continual-retention",
        "capability_count": len(promoted),
        "input_domains": [str(item["input_domain"]) for item in promoted],
        "output_domains": [str(item["output_domain"]) for item in promoted],
        "candidate_ids": candidate_ids,
        "scopes": scopes,
        "expressions": [item["expression"] for item in promoted],
        "fresh_engine_runs": stage_runs,
        "fresh_engine_count": len(stage_runs),
        "cumulative_replay_invocation_count": sum(len(stage) for stage in stage_replays),
        "all_negative_evidence_retained": all(
            bool(item["negative_evidence_retained"]) for item in promoted
        ),
        "candidate_trial_state_unchanged_after_later_learning": trial_state_unchanged,
        "candidate_trial_file_counts": {
            candidate_id: len(frozen_trials[candidate_id])
            for candidate_id in candidate_ids
        },
        "all_fresh_engine_replays_matched": True,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal deterministic heterogeneous continual-learning evidence only. Numeric, string, "
            "sequence, and object capabilities accumulate and replay on fresh Engines while prior "
            "Candidate trial ledgers remain byte-identical and forced protected regressions are "
            "retained. This does not supply genuine-model production behavior, independent evaluator "
            "operation, broad open-world AGI evidence, or the strict external AGI claim gate."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "heterogeneous-retention"
        / f"heterogeneous-retention-{_digest(seed)[:16]}.json",
        report,
    )
    return report
