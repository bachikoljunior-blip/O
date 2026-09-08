from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.attested_structured_runner_transfer import run_attested_structured_runner_transfer
from agi.materialized_runtime_replay import _candidate_trial_snapshot


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def run_multi_campaign_retention(
    root: Path,
    seed: str,
    *,
    campaign_count: int = 4,
) -> dict[str, Any]:
    """Accumulate several structured capabilities and falsify cross-campaign forgetting.

    Each child campaign independently promotes a behavior-bound numeric->object external Candidate and
    a learned object->boolean Candidate, then composes them through three fresh mechanical Engines via
    the fail-closed runner-attestation path. After the first pair is promoted, its complete Candidate
    trial ledgers are frozen. Later child campaigns must add distinct Candidates and runtime runs without
    changing one byte of the anchor pair's regression evidence.

    This is deliberately longer than a single promotion/replay probe and exercises sequential durable
    accumulation, retained negative evidence, fresh-engine replay, and no-forgetting across later
    learning. The runner/verifier used by each child campaign is still an internal deterministic test
    double, so the result is development evidence only and cannot satisfy the independent production
    AGI claim gate.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    if (
        not isinstance(campaign_count, int)
        or isinstance(campaign_count, bool)
        or not 3 <= campaign_count <= 8
    ):
        raise ValueError("campaign_count must be an integer from 3 through 8")

    root = root.resolve()
    child_reports: list[dict[str, Any]] = []
    anchor_candidate_ids: list[str] = []
    anchor_trial_before: dict[str, dict[str, str]] = {}

    for index in range(campaign_count):
        child_seed = f"{seed}:sequential-retention:{index}"
        child = run_attested_structured_runner_transfer(root, child_seed)
        if not child.get("passed"):
            raise RuntimeError(f"structured retention child campaign failed: {index}")
        child_reports.append(child)
        if index == 0:
            anchor_candidate_ids = [str(item) for item in child["candidate_ids"]]
            anchor_trial_before = {
                candidate_id: _candidate_trial_snapshot(root, candidate_id)
                for candidate_id in anchor_candidate_ids
            }
            if not all(anchor_trial_before.values()):
                raise RuntimeError("anchor Candidates lack persisted regression trial evidence")

    all_candidate_ids = [
        str(candidate_id)
        for child in child_reports
        for candidate_id in child["candidate_ids"]
    ]
    all_tool_ids = [
        str(tool_id)
        for child in child_reports
        for tool_id in child["tool_ids"]
    ]
    all_runtime_runs = [
        str(run_id)
        for child in child_reports
        for run_id in child["fresh_engine_runs"]
    ]
    if len(set(all_candidate_ids)) != len(all_candidate_ids):
        raise RuntimeError("sequential retention reused a Candidate identity")
    if len(set(all_tool_ids)) != len(all_tool_ids):
        raise RuntimeError("sequential retention reused a tool identity")
    if len(set(all_runtime_runs)) != len(all_runtime_runs):
        raise RuntimeError("sequential retention reused a runtime run")

    anchor_trial_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in anchor_candidate_ids
    }
    anchor_trial_state_unchanged = anchor_trial_after == anchor_trial_before
    if not anchor_trial_state_unchanged:
        raise RuntimeError("later learning rewrote anchor Candidate regression trial state")

    negative_evidence_retained = all(
        bool(child.get("external_negative_evidence_retained"))
        and bool(child.get("learned_negative_evidence_retained"))
        for child in child_reports
    )
    fresh_engine_composition_matched = all(
        bool(child.get("fresh_engine_composition_matched"))
        for child in child_reports
    )
    internal_runner_boundary_retained = all(
        bool(child.get("runner_attestation_checked"))
        and bool(child.get("runner_test_double_only"))
        for child in child_reports
    )
    no_live_model_invocation_required = all(
        child.get("live_model_invocation_required") is False
        for child in child_reports
    )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": bool(
            anchor_trial_state_unchanged
            and negative_evidence_retained
            and fresh_engine_composition_matched
            and internal_runner_boundary_retained
            and no_live_model_invocation_required
        ),
        "campaign_count": campaign_count,
        "candidate_count": len(all_candidate_ids),
        "tool_count": len(all_tool_ids),
        "fresh_engine_count": len(all_runtime_runs),
        "candidate_ids": all_candidate_ids,
        "tool_ids": all_tool_ids,
        "fresh_engine_runs": all_runtime_runs,
        "anchor_candidate_ids": anchor_candidate_ids,
        "anchor_trial_file_counts": {
            candidate_id: len(anchor_trial_before[candidate_id])
            for candidate_id in anchor_candidate_ids
        },
        "anchor_trial_state_unchanged_after_later_learning": anchor_trial_state_unchanged,
        "all_negative_evidence_retained": negative_evidence_retained,
        "all_fresh_engine_composition_matched": fresh_engine_composition_matched,
        "all_runner_attestations_checked": internal_runner_boundary_retained,
        "all_runners_internal_test_doubles": internal_runner_boundary_retained,
        "live_model_invocation_required": not no_live_model_invocation_required,
        "claim_boundary": (
            "Internal sequential continual-retention evidence only. Multiple distinct structured "
            "Candidates accumulate across fresh Engines while the first pair's regression trial "
            "ledgers remain byte-identical, but every runner/verifier is still a deterministic "
            "repository test double. This does not establish independent operating-system isolation, "
            "production autonomy, broad AGI, or the repository's strict external AGI evidence gate."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "multi-campaign-retention"
        / f"multi-campaign-retention-{_digest(seed)[:16]}.json",
        report,
    )
    return report
