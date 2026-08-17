from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

from agi.acquired_programs import execute_program
from agi.materialized_retention_matrix import _active_acquired_candidates
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
    _probe_for_domain,
)
from continual.learned_tools import LearnedToolError


def _domains(acquired: Mapping[str, Any]) -> tuple[str, str]:
    program = acquired.get("program")
    if not isinstance(program, Mapping):
        raise RuntimeError("materialized acquired program descriptor is missing")
    return str(program.get("input_domain", "")), str(program.get("output_domain", ""))


def _composable_pair(root: Path):
    values = _active_acquired_candidates(root)
    for first_candidate, first_acquired in values:
        _, first_output = _domains(first_acquired)
        for second_candidate, second_acquired in values:
            if first_candidate["candidate_id"] == second_candidate["candidate_id"]:
                continue
            second_input, _ = _domains(second_acquired)
            if first_output and first_output == second_input:
                return (first_candidate, first_acquired), (second_candidate, second_acquired)
    raise RuntimeError("no distinct typed-composable materialized Candidate pair is available")


def _result_output(
    response: Mapping[str, Any],
    candidate: Mapping[str, Any],
    acquired: Mapping[str, Any],
) -> Any:
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise RuntimeError("typed composition returned malformed execution output")
    if result.get("candidate_id") != candidate["candidate_id"]:
        raise RuntimeError("typed composition selected the wrong Candidate")
    if result.get("scope") != acquired["scope"] or result.get("tool_id") != acquired["tool_id"]:
        raise RuntimeError("typed composition crossed an exact scope/tool boundary")
    if result.get("execution_kind") != "verified_learned_tool" or result.get("program_kind") != "acquired_program":
        raise RuntimeError("typed composition bypassed verified acquired-program execution")
    return result.get("output")


def _compose_once(
    root: Path,
    first: tuple[dict[str, Any], dict[str, Any]],
    second: tuple[dict[str, Any], dict[str, Any]],
    source: Any,
) -> dict[str, Any]:
    first_candidate, first_acquired = first
    second_candidate, second_acquired = second
    engine = _mechanical_engine(root)
    run_id = _new_runtime_run(engine)
    first_response = _invoke(
        engine,
        run_id,
        scope=str(first_acquired["scope"]),
        tool_id=str(first_acquired["tool_id"]),
        value=source,
    )
    intermediate = _result_output(first_response, first_candidate, first_acquired)
    second_response = _invoke(
        engine,
        run_id,
        scope=str(second_acquired["scope"]),
        tool_id=str(second_acquired["tool_id"]),
        value=intermediate,
    )
    final = _result_output(second_response, second_candidate, second_acquired)
    run_dir = engine.store.run_dir(run_id)
    return {
        "run_id": run_id,
        "run_persisted": run_dir.is_dir() and (run_dir / "snapshot.json").is_file(),
        "intermediate": intermediate,
        "final": final,
    }


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def run_materialized_typed_composition(root: Path) -> dict[str, Any]:
    root = root.resolve()
    first, second = _composable_pair(root)
    first_candidate, first_acquired = first
    second_candidate, second_acquired = second
    first_program = first_acquired["program"]
    second_program = second_acquired["program"]
    first_input, first_output = _domains(first_acquired)
    second_input, second_output = _domains(second_acquired)
    if first_output != second_input:
        raise RuntimeError("selected programs are not type composable")

    source = _probe_for_domain(first_input)
    expected_intermediate = execute_program(first_program, source)
    expected_final = execute_program(second_program, expected_intermediate)
    candidate_ids = [str(first_candidate["candidate_id"]), str(second_candidate["candidate_id"])]
    trial_before = {candidate_id: _candidate_trial_snapshot(root, candidate_id) for candidate_id in candidate_ids}

    with tempfile.TemporaryDirectory(prefix="agi-composition-baseline-") as temporary:
        empty_engine = _mechanical_engine(Path(temporary))
        empty_run = _new_runtime_run(empty_engine)
        for acquired, value in ((first_acquired, source), (second_acquired, expected_intermediate)):
            try:
                _invoke(
                    empty_engine,
                    empty_run,
                    scope=str(acquired["scope"]),
                    tool_id=str(acquired["tool_id"]),
                    value=value,
                )
            except LearnedToolError:
                continue
            raise RuntimeError("materialized composition tool was callable without Candidate state")

    run_one = _compose_once(root, first, second, source)
    run_two = _compose_once(root, first, second, source)
    if run_one["intermediate"] != expected_intermediate or run_one["final"] != expected_final:
        raise RuntimeError("typed composition disagreed with persisted program semantics")
    if run_two["intermediate"] != expected_intermediate or run_two["final"] != expected_final:
        raise RuntimeError("typed composition did not survive a fresh Engine")
    if run_one["run_id"] == run_two["run_id"] or not run_one["run_persisted"] or not run_two["run_persisted"]:
        raise RuntimeError("typed composition did not use distinct persisted runtime runs")

    trial_after = {candidate_id: _candidate_trial_snapshot(root, candidate_id) for candidate_id in candidate_ids}
    if trial_after != trial_before:
        raise RuntimeError("typed composition changed persistent Candidate trial state")

    return {
        "schema_version": 1,
        "passed": True,
        "candidate_ids": candidate_ids,
        "scopes": [str(first_acquired["scope"]), str(second_acquired["scope"])],
        "tool_ids": [str(first_acquired["tool_id"]), str(second_acquired["tool_id"])],
        "domain_chain": [first_input, first_output, second_output],
        "source_sha256": _digest(source),
        "intermediate_sha256": _digest(expected_intermediate),
        "final_sha256": _digest(expected_final),
        "baseline_without_candidates_failed_closed": True,
        "durable_run_ids": [run_one["run_id"], run_two["run_id"]],
        "durable_runs_persisted": True,
        "fresh_engine_composition_matched": True,
        "candidate_trial_state_unchanged": True,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal typed-composition retained-learning evidence only; this does not establish AGI "
            "or independent production evidence."
        ),
    }
