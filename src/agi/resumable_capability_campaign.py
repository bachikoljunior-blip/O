from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from continual.contracted_external_tools import ExternalToolAdapter
from continual.learning_engine import LearningEnabledEngine

from .acquired_program_runtime import _atomic_json, _digest, _new_runtime_run
from .durable_runtime_transfer import run_durable_runtime_transfer_campaign
from .external_tool_acquisition import (
    EXTERNAL_TOOL_SCOPE,
    _hidden_external_adapter,
    run_external_tool_acquisition_campaign,
)
from .generated_crossdomain_cegis import _runtime_call, run_generated_crossdomain_cegis_campaign

_CAMPAIGN_SCHEMA = 1
_STAGE_ORDER = ("crossdomain_acquisition", "durable_transfer_rollback", "external_tool_engine")


def _state_path(root: Path, campaign_id: str) -> Path:
    return root / ".continual" / "system" / "capability-campaigns" / f"{campaign_id}.json"


def _seed_commitments(seeds: Mapping[str, str]) -> dict[str, str]:
    return {key: _digest(value) for key, value in sorted(seeds.items())}


def _load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("capability campaign state must be an object")
    return value


def _validate_existing_state(
    state: Mapping[str, Any],
    *,
    campaign_id: str,
    commitments: Mapping[str, str],
) -> None:
    if state.get("schema_version") != _CAMPAIGN_SCHEMA:
        raise RuntimeError("capability campaign schema mismatch")
    if state.get("campaign_id") != campaign_id:
        raise RuntimeError("capability campaign identity mismatch")
    if state.get("seed_commitments") != dict(commitments):
        raise RuntimeError("capability campaign seed commitments changed across resume")
    stages = state.get("stages")
    if not isinstance(stages, dict):
        raise RuntimeError("capability campaign stages must be an object")
    for stage in _STAGE_ORDER:
        entry = stages.get(stage)
        if not isinstance(entry, dict) or entry.get("status") not in {"pending", "running", "completed"}:
            raise RuntimeError(f"invalid capability campaign stage state: {stage}")


def _initial_state(campaign_id: str, commitments: Mapping[str, str]) -> dict[str, Any]:
    return {
        "schema_version": _CAMPAIGN_SCHEMA,
        "campaign_id": campaign_id,
        "seed_commitments": dict(commitments),
        "stage_order": list(_STAGE_ORDER),
        "stages": {
            stage: {
                "status": "pending",
                "attempts": 0,
                "result_digest": None,
                "evidence_ref": None,
            }
            for stage in _STAGE_ORDER
        },
        "completed_stages": [],
        "active_stage": None,
        "status": "in_progress",
        "claim_boundary": (
            "Repository-internal resumable capability-development evidence only. Independent "
            "production-grade external AGI evidence is still required."
        ),
    }


def _run_external_engine_stage(root: Path, seed: str) -> dict[str, Any]:
    acquisition = run_external_tool_acquisition_campaign(root, seed)
    if not acquisition["passed"]:
        raise RuntimeError("external tool acquisition prerequisite failed")
    adapter_fn, adapter_sha = _hidden_external_adapter(seed)
    adapter = ExternalToolAdapter(adapter_sha256=adapter_sha, function=adapter_fn)
    engine = LearningEnabledEngine(
        root,
        external_tool_adapters={str(acquisition["tool_id"]): adapter},
    )
    run_id = _new_runtime_run(engine)
    query = {"campaign": 3, "resume": 11, "kind": "external-engine-stage"}
    output = _runtime_call(
        engine,
        run_id,
        scope=EXTERNAL_TOOL_SCOPE,
        tool_id=str(acquisition["tool_id"]),
        value=query,
    )
    expected = adapter_fn(query)
    payload = {
        "acquisition_campaign_id": acquisition["campaign_id"],
        "acquisition_digest": acquisition["digest"],
        "engine_run_id": run_id,
        "engine_run_persisted": (root / ".continual" / "runs" / run_id / "snapshot.json").is_file(),
        "output_sha256": _digest(output),
        "expected_sha256": _digest(expected),
        "success": output == expected,
        "adapter_source_persisted": False,
    }
    payload["passed"] = bool(
        acquisition["passed"]
        and payload["engine_run_persisted"]
        and payload["success"]
        and payload["adapter_source_persisted"] is False
    )
    payload["digest"] = _digest({key: value for key, value in payload.items() if key != "digest"})
    return payload


def _run_stage(root: Path, stage: str, seeds: Mapping[str, str]) -> dict[str, Any]:
    if stage == "crossdomain_acquisition":
        return run_generated_crossdomain_cegis_campaign(root, seeds[stage])
    if stage == "durable_transfer_rollback":
        return run_durable_runtime_transfer_campaign(root, seeds[stage])
    if stage == "external_tool_engine":
        return _run_external_engine_stage(root, seeds[stage])
    raise RuntimeError(f"unknown capability campaign stage: {stage}")


def run_resumable_capability_campaign(
    root: Path,
    campaign_id: str,
    *,
    seeds: Mapping[str, str],
    max_new_stages: int | None = None,
) -> dict[str, Any]:
    """Advance a persisted capability campaign without replaying completed milestones.

    Raw stage seeds are supplied by the caller on every invocation and never written to campaign
    state; only commitments are persisted. A stage is marked running before execution and completed
    only after its underlying evidence reports success. A later invocation skips completed stages,
    resumes the first pending/running stage, and keeps prior result digests immutable. ``max_new_stages``
    is an external execution budget rather than a success condition, allowing hourly processes to
    stop safely and resume without rediscovery.
    """

    if not campaign_id or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-_." for ch in campaign_id):
        raise ValueError("campaign_id must be a safe lowercase identifier")
    if set(seeds) != set(_STAGE_ORDER) or not all(isinstance(value, str) and value for value in seeds.values()):
        raise ValueError("seeds must provide exactly one non-empty seed per campaign stage")
    if max_new_stages is not None and max_new_stages < 1:
        raise ValueError("max_new_stages must be positive when supplied")

    root = root.resolve()
    commitments = _seed_commitments(seeds)
    path = _state_path(root, campaign_id)
    state = _load_state(path)
    if state is None:
        state = _initial_state(campaign_id, commitments)
        _atomic_json(path, state)
    else:
        _validate_existing_state(state, campaign_id=campaign_id, commitments=commitments)

    completed_before = tuple(state["completed_stages"])
    prior_digests = {
        stage: state["stages"][stage]["result_digest"]
        for stage in completed_before
    }
    advanced = 0
    for stage in _STAGE_ORDER:
        entry = state["stages"][stage]
        if entry["status"] == "completed":
            continue
        if max_new_stages is not None and advanced >= max_new_stages:
            break
        entry["status"] = "running"
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        state["active_stage"] = stage
        _atomic_json(path, state)

        report = _run_stage(root, stage, seeds)
        if not bool(report.get("passed")):
            raise RuntimeError(f"capability campaign stage failed: {stage}")
        digest = str(report.get("digest") or _digest(report))
        entry["status"] = "completed"
        entry["result_digest"] = digest
        entry["evidence_ref"] = str(report.get("campaign_id") or report.get("candidate_id") or stage)
        if stage not in state["completed_stages"]:
            state["completed_stages"].append(stage)
        state["active_stage"] = None
        advanced += 1
        _atomic_json(path, state)

    for stage, digest in prior_digests.items():
        if state["stages"][stage]["result_digest"] != digest:
            raise RuntimeError("completed capability campaign evidence changed during resume")

    complete = tuple(state["completed_stages"]) == _STAGE_ORDER
    state["status"] = "completed" if complete else "in_progress"
    state["active_stage"] = None
    state["completed_count"] = len(state["completed_stages"])
    state["resume_skipped_completed"] = len(completed_before)
    state["last_advance_count"] = advanced
    state["passed"] = complete
    state["digest"] = _digest({key: value for key, value in state.items() if key != "digest"})
    _atomic_json(path, state)
    return state
