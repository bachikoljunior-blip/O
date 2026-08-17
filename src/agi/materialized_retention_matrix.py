from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

from agi.acquired_programs import execute_program
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
    _probe_for_domain,
)
from continual.learned_tools import LearnedToolError


def _active_acquired_candidates(root: Path) -> tuple[tuple[dict[str, Any], dict[str, Any]], ...]:
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    candidates_dir = root / ".continual" / "candidates"
    for path in sorted(candidates_dir.glob("*/candidate.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            continue
        if raw.get("target_component") != "acquired_program":
            continue
        if raw.get("status") != "active-for-scope":
            continue
        acquired = raw.get("acquired_program")
        verified = raw.get("verified_scope_states")
        if not isinstance(acquired, Mapping) or not isinstance(verified, Mapping):
            continue
        scope = acquired.get("scope")
        if not isinstance(scope, str) or scope not in verified:
            continue
        selected.append((dict(raw), dict(acquired)))
    return tuple(selected)


def _validate_result(
    output: Mapping[str, Any],
    *,
    expected: Any,
    candidate_id: str,
    scope: str,
    tool_id: str,
) -> dict[str, Any]:
    result = output.get("result")
    if not isinstance(result, Mapping):
        raise RuntimeError("materialized retention matrix returned malformed execution output")
    if result.get("output") != expected:
        raise RuntimeError("materialized retention matrix disagreed with persisted program semantics")
    if result.get("candidate_id") != candidate_id:
        raise RuntimeError("materialized retention matrix used the wrong Candidate")
    if result.get("scope") != scope or result.get("tool_id") != tool_id:
        raise RuntimeError("materialized retention matrix crossed an exact Candidate scope/tool boundary")
    if result.get("execution_kind") != "verified_learned_tool":
        raise RuntimeError("materialized retention matrix bypassed verified learned-tool execution")
    if result.get("program_kind") != "acquired_program":
        raise RuntimeError("materialized retention matrix did not use the acquired-program path")
    return dict(result)


def _exercise_candidate(
    root: Path,
    candidate: Mapping[str, Any],
    acquired: Mapping[str, Any],
) -> dict[str, Any]:
    candidate_id = str(candidate["candidate_id"])
    scope = str(acquired["scope"])
    tool_id = str(acquired["tool_id"])
    program = acquired.get("program")
    if not isinstance(program, Mapping):
        raise RuntimeError(f"materialized Candidate {candidate_id} is missing its program")
    input_domain = str(program.get("input_domain", ""))
    probe = _probe_for_domain(input_domain)
    expected = execute_program(program, probe)

    with tempfile.TemporaryDirectory(prefix="agi-materialized-matrix-baseline-") as temporary:
        baseline_engine = _mechanical_engine(Path(temporary))
        baseline_run = _new_runtime_run(baseline_engine)
        failed_closed = False
        try:
            _invoke(
                baseline_engine,
                baseline_run,
                scope=scope,
                tool_id=tool_id,
                value=probe,
            )
        except LearnedToolError:
            failed_closed = True
        if not failed_closed:
            raise RuntimeError(
                f"materialized Candidate {candidate_id} was callable without Candidate state"
            )

    trial_before = _candidate_trial_snapshot(root, candidate_id)
    first_engine = _mechanical_engine(root)
    first_run = _new_runtime_run(first_engine)
    first_output = _invoke(first_engine, first_run, scope=scope, tool_id=tool_id, value=probe)
    first_result = _validate_result(
        first_output,
        expected=expected,
        candidate_id=candidate_id,
        scope=scope,
        tool_id=tool_id,
    )

    restarted_engine = _mechanical_engine(root)
    restarted_run = _new_runtime_run(restarted_engine)
    restarted_output = _invoke(
        restarted_engine,
        restarted_run,
        scope=scope,
        tool_id=tool_id,
        value=probe,
    )
    restarted_result = _validate_result(
        restarted_output,
        expected=expected,
        candidate_id=candidate_id,
        scope=scope,
        tool_id=tool_id,
    )

    trial_after = _candidate_trial_snapshot(root, candidate_id)
    if trial_after != trial_before:
        raise RuntimeError(f"runtime replay rewrote Candidate trial state for {candidate_id}")

    return {
        "candidate_id": candidate_id,
        "scope": scope,
        "tool_id": tool_id,
        "input_domain": input_domain,
        "baseline_without_candidate_failed_closed": True,
        "fresh_engine_replay_matched": restarted_result == first_result,
        "candidate_trial_state_unchanged": True,
        "trial_file_count": len(trial_before),
        "probe": probe,
        "expected": expected,
    }


def run_materialized_retention_matrix(
    root: Path,
    *,
    minimum_candidates: int = 3,
) -> dict[str, Any]:
    """Retest every committed acquired capability across fresh Engines without forgetting.

    The matrix exercises all active materialized acquired-program Candidates, not merely a single
    chosen example. Each exact capability must fail closed when Candidate state is absent, execute
    correctly through a fresh mechanical LearningEnabledEngine, survive a second fresh Engine, and
    leave its persistent regression-trial ledger byte-identical. After all later capabilities run,
    the earliest capability is invoked again in another fresh Engine to detect cross-capability
    forgetting or contamination.
    """

    root = root.resolve()
    candidates = _active_acquired_candidates(root)
    if len(candidates) < minimum_candidates:
        raise RuntimeError(
            f"materialized retention matrix requires at least {minimum_candidates} active acquired "
            f"Candidates; found {len(candidates)}"
        )

    ids = [str(candidate["candidate_id"]) for candidate, _ in candidates]
    scopes = [str(acquired["scope"]) for _, acquired in candidates]
    if len(set(ids)) != len(ids) or len(set(scopes)) != len(scopes):
        raise RuntimeError("materialized retention matrix requires distinct Candidate IDs and scopes")

    global_trial_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id) for candidate_id in ids
    }
    results = [
        _exercise_candidate(root, candidate, acquired) for candidate, acquired in candidates
    ]

    first_candidate, first_acquired = candidates[0]
    first_result = results[0]
    final_engine = _mechanical_engine(root)
    final_run = _new_runtime_run(final_engine)
    final_output = _invoke(
        final_engine,
        final_run,
        scope=first_result["scope"],
        tool_id=first_result["tool_id"],
        value=first_result["probe"],
    )
    final_result = _validate_result(
        final_output,
        expected=first_result["expected"],
        candidate_id=str(first_candidate["candidate_id"]),
        scope=str(first_acquired["scope"]),
        tool_id=str(first_acquired["tool_id"]),
    )

    global_trial_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id) for candidate_id in ids
    }
    if global_trial_after != global_trial_before:
        raise RuntimeError("multi-capability runtime replay changed persistent Candidate trial state")

    compact_results = [
        {
            key: value
            for key, value in item.items()
            if key not in {"probe", "expected"}
        }
        for item in results
    ]
    return {
        "schema_version": 1,
        "passed": True,
        "candidate_count": len(candidates),
        "candidate_ids": ids,
        "scopes": scopes,
        "input_domains": sorted({str(item["input_domain"]) for item in results}),
        "results": compact_results,
        "all_baselines_failed_closed": all(
            item["baseline_without_candidate_failed_closed"] for item in results
        ),
        "all_fresh_engine_replays_matched": all(
            item["fresh_engine_replay_matched"] for item in results
        ),
        "all_candidate_trial_state_unchanged": global_trial_after == global_trial_before,
        "earliest_candidate_survived_after_later_replays": (
            final_result.get("candidate_id") == str(first_candidate["candidate_id"])
            and final_result.get("output") == first_result["expected"]
        ),
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal multi-capability retained-learning evidence only. These Candidates, evaluator, "
            "runtime, and tests remain repository-internal and do not establish AGI or independent "
            "production evidence."
        ),
    }
