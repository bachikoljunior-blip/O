from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from agi.acquired_programs import execute_program
from agi.external_tool_acquisition import (
    EXTERNAL_TOOL_SCOPE,
    _hidden_external_adapter,
    run_external_tool_acquisition_campaign,
)
from agi.external_tool_runtime_retention import _engine_with_adapter
from agi.materialized_retention_matrix import _active_acquired_candidates
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _new_runtime_run,
)
from continual.contracted_external_tools import ExternalToolAdapter


def _string_input_candidate(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    for candidate, acquired in _active_acquired_candidates(root):
        program = acquired.get("program")
        if isinstance(program, Mapping) and program.get("input_domain") == "string":
            return candidate, acquired
    raise RuntimeError("no verified materialized string-input acquired-program Candidate is available")


def _result(
    response: Mapping[str, Any],
    *,
    candidate_id: str,
    scope: str,
    tool_id: str,
    program_kind: str,
) -> Any:
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise RuntimeError("cross-boundary composition returned malformed execution output")
    if result.get("candidate_id") != candidate_id:
        raise RuntimeError("cross-boundary composition selected the wrong Candidate")
    if result.get("scope") != scope or result.get("tool_id") != tool_id:
        raise RuntimeError("cross-boundary composition crossed an exact scope/tool boundary")
    if result.get("execution_kind") != "verified_learned_tool":
        raise RuntimeError("cross-boundary composition bypassed verified learned-tool execution")
    if result.get("program_kind") != program_kind:
        raise RuntimeError("cross-boundary composition used the wrong program kind")
    return result.get("output")


def _digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def run_external_acquired_composition(root: Path, seed: str) -> dict[str, Any]:
    """Compose a newly acquired host capability with a separately retained learned program.

    The first stage is a behavior-bound object->string external-tool Candidate. Its output is fed,
    through the normal verified runtime path, into an independently materialized string-input
    acquired-program Candidate. The chain must reproduce across two fresh Engine instances while both
    Candidates' regression trial ledgers remain byte-identical after acquisition.

    This is internal typed transfer/retention evidence. The host adapter remains a trusted zero-effect
    boundary; this function neither proves arbitrary host code harmless nor constitutes AGI evidence.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    acquisition = run_external_tool_acquisition_campaign(root, seed)
    if not acquisition.get("passed"):
        raise RuntimeError("external tool acquisition did not pass")

    external_candidate_id = str(acquisition["candidate_id"])
    external_tool_id = str(acquisition["tool_id"])
    adapter_fn, adapter_sha256 = _hidden_external_adapter(seed)
    if adapter_sha256 != acquisition.get("adapter_sha256"):
        raise RuntimeError("verified external adapter identity changed before composition")
    adapter = ExternalToolAdapter(adapter_sha256=adapter_sha256, function=adapter_fn)

    learned_candidate, acquired = _string_input_candidate(root)
    learned_candidate_id = str(learned_candidate["candidate_id"])
    learned_scope = str(acquired["scope"])
    learned_tool_id = str(acquired["tool_id"])
    program = acquired.get("program")
    if not isinstance(program, Mapping):
        raise RuntimeError("selected acquired-program Candidate is missing its program")

    trial_before = {
        external_candidate_id: _candidate_trial_snapshot(root, external_candidate_id),
        learned_candidate_id: _candidate_trial_snapshot(root, learned_candidate_id),
    }
    if not all(trial_before.values()):
        raise RuntimeError("composition Candidates must have persisted regression trial evidence")

    source = {"alpha": 5, "beta": 13, "label": "external-to-acquired"}
    expected_intermediate = adapter_fn(source)
    expected_final = execute_program(program, expected_intermediate)

    def compose_once() -> dict[str, Any]:
        engine = _engine_with_adapter(root, tool_id=external_tool_id, adapter=adapter)
        run_id = _new_runtime_run(engine)
        first_response = _invoke(
            engine,
            run_id,
            scope=EXTERNAL_TOOL_SCOPE,
            tool_id=external_tool_id,
            value=source,
        )
        intermediate = _result(
            first_response,
            candidate_id=external_candidate_id,
            scope=EXTERNAL_TOOL_SCOPE,
            tool_id=external_tool_id,
            program_kind="contracted_external_tool",
        )
        second_response = _invoke(
            engine,
            run_id,
            scope=learned_scope,
            tool_id=learned_tool_id,
            value=intermediate,
        )
        final = _result(
            second_response,
            candidate_id=learned_candidate_id,
            scope=learned_scope,
            tool_id=learned_tool_id,
            program_kind="acquired_program",
        )
        return {"run_id": run_id, "intermediate": intermediate, "final": final}

    first = compose_once()
    second = compose_once()
    for result in (first, second):
        if result["intermediate"] != expected_intermediate:
            raise RuntimeError("external stage changed across typed composition")
        if result["final"] != expected_final:
            raise RuntimeError("acquired-program stage disagreed with persisted program semantics")
    if first["run_id"] == second["run_id"]:
        raise RuntimeError("cross-boundary composition did not use distinct fresh runtime runs")

    trial_after = {
        external_candidate_id: _candidate_trial_snapshot(root, external_candidate_id),
        learned_candidate_id: _candidate_trial_snapshot(root, learned_candidate_id),
    }
    if trial_after != trial_before:
        raise RuntimeError("cross-boundary composition rewrote Candidate regression trial state")

    return {
        "schema_version": 1,
        "passed": True,
        "candidate_ids": [external_candidate_id, learned_candidate_id],
        "tool_ids": [external_tool_id, learned_tool_id],
        "scopes": [EXTERNAL_TOOL_SCOPE, learned_scope],
        "domain_chain": ["object", "string", str(program.get("output_domain", ""))],
        "source_sha256": _digest(source),
        "intermediate_sha256": _digest(expected_intermediate),
        "final_sha256": _digest(expected_final),
        "fresh_engine_runs": [first["run_id"], second["run_id"]],
        "fresh_engine_composition_matched": True,
        "candidate_trial_state_unchanged": True,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal cross-boundary retained-capability evidence only. The host adapter remains a "
            "trusted zero-effect boundary; this does not establish open-ended tool safety or AGI."
        ),
    }
