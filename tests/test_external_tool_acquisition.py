from __future__ import annotations

import json
from pathlib import Path

from agi.external_tool_acquisition import run_external_tool_acquisition_campaign


def test_unknown_host_tool_is_regression_gated_behavior_bound_and_resource_bounded(
    runtime_repo: Path,
) -> None:
    seed = "unknown-host-object-tool-20260817"

    report = run_external_tool_acquisition_campaign(runtime_repo, seed)

    assert report["passed"] is True
    assert report["seed_persisted"] is False
    assert report["outside_handwritten_program_grammar"] is True
    assert report["adapter_source_persisted"] is False
    assert report["input_domain"] == "object"
    assert report["output_domain"] == "string"
    assert report["effects"] == []
    assert report["effectful_candidate_rejected"] is True
    assert report["inactive_before_regression"] is True
    assert report["forced_protected_regression_rejected"] is True
    assert report["promotion_adopted"] is True
    assert report["wrong_adapter_identity_rejected"] is True
    assert report["spoofed_identity_behavior_rejected"] is True
    assert report["binding_challenge_count"] == 3
    assert report["behavior_binding_verified"] is True
    assert all(item["success"] for item in report["runtime_results"])
    assert report["call_budget_failed_closed"] is True
    assert report["oversized_input_failed_closed"] is True
    assert report["negative_evidence_retained"] is True

    candidate_path = (
        runtime_repo / ".continual" / "candidates" / report["candidate_id"] / "candidate.json"
    )
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    contract = candidate["contracted_external_tool"]
    assert contract["effects"] == []
    assert contract["limits"]["max_calls"] == 4
    assert contract["limits"]["max_input_bytes"] == 2048
    assert contract["adapter_sha256"] == report["adapter_sha256"]
    assert len(contract["binding_challenges"]) == 3
    assert all(
        set(challenge) == {"input", "output_sha256"}
        and len(challenge["output_sha256"]) == 64
        for challenge in contract["binding_challenges"]
    )
    assert candidate["scope_states"][report["scope"]] == "VERIFIED_FOR_SCOPE"

    evidence_path = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "contracted-external-tool"
        / f"{report['campaign_id']}.json"
    )
    evidence_text = evidence_path.read_text(encoding="utf-8")
    evidence = json.loads(evidence_text)
    assert evidence["digest"] == report["digest"]
    assert seed not in evidence_text
    assert "not open-ended tool use or AGI proof" in evidence["claim_boundary"]
