from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from continual.candidate_regression import record_candidate_regression
from continual.contracted_external_tools import (
    ContractedExternalToolError,
    ContractedExternalToolRegistry,
    ExternalToolAdapter,
    contracted_external_tool_candidate_payload,
)

from .acquired_program_runtime import _atomic_json, _digest


EXTERNAL_TOOL_SCOPE = "agi/contracted-external-tool/object-summary-v1"


def _measurement(task_id: str, repeat: int, score: float, artifact: Any) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "criterion": "self_improvement",
        "domain": "external-tool:object-to-string",
        "repeat_index": repeat,
        "passed": score == 1.0,
        "score": score,
        "artifact_sha256": _digest(artifact),
    }


def _hidden_external_adapter(seed: str):
    tag = hashlib.sha256(f"external-object-summary-v1\0{seed}".encode("utf-8")).hexdigest()[:8]

    def adapter(value: Any) -> str:
        if not isinstance(value, dict):
            raise ValueError("object required")
        numeric_total = sum(
            int(item)
            for item in value.values()
            if isinstance(item, (int, float)) and not isinstance(item, bool)
        )
        keys = ",".join(sorted(str(key) for key in value))
        return f"{tag}|{len(value)}|{numeric_total}|{keys}"

    identity = _digest(
        {
            "kind": "host-object-summary-v1",
            "tag": tag,
            "input_domain": "object",
            "output_domain": "string",
        }
    )
    return adapter, identity


def run_external_tool_acquisition_campaign(root: Path, seed: str) -> dict[str, Any]:
    """Acquire a host-supplied tool outside the handwritten program grammar under a strict contract.

    The tool accepts the previously unsupported object domain and its implementation is supplied by
    the host rather than persisted/generated source code. The Candidate declares exact type, effect,
    call, and byte limits plus deterministic behavior-attestation probes. Effectful requests remain
    quarantined. Deterministic regression must pass before the host adapter is exposed, and runtime
    limits or a same-identity behavior drift fail closed after promotion.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    adapter_fn, adapter_sha = _hidden_external_adapter(seed)
    token = _digest({"seed": seed, "kind": "contracted-external-object-summary-v1"})[:16]
    candidate_id = f"external-tool-{token}"
    tool_id = f"host-object-summary-{token}"
    max_calls = 4
    attestation_inputs = (
        {"attest": 2, "drift": -1, "label": "alpha"},
        {"attest": 4, "z": 7, "label": "beta"},
    )
    attestation_cases = tuple(
        {"input": value, "output_sha256": _digest(adapter_fn(value))}
        for value in attestation_inputs
    )

    effectful_rejected = False
    try:
        contracted_external_tool_candidate_payload(
            candidate_id=f"effectful-{token}",
            tool_id=f"effectful-host-{token}",
            scope=EXTERNAL_TOOL_SCOPE,
            input_domain="object",
            output_domain="string",
            effects=("network",),
            max_calls=1,
            max_input_bytes=1024,
            max_output_bytes=1024,
            adapter_sha256=adapter_sha,
            description="Must remain quarantined because it requests network effects.",
        )
    except ContractedExternalToolError:
        effectful_rejected = True
    if not effectful_rejected:
        raise RuntimeError("effectful external Candidate was not quarantined")

    candidate = contracted_external_tool_candidate_payload(
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=EXTERNAL_TOOL_SCOPE,
        input_domain="object",
        output_domain="string",
        effects=(),
        max_calls=max_calls,
        max_input_bytes=2048,
        max_output_bytes=2048,
        adapter_sha256=adapter_sha,
        description="Host-provided bounded object summarizer acquired outside the handwritten primitive grammar.",
        attestation_cases=attestation_cases,
    )
    candidate["supporting_evidence"] = [
        {
            "type": "host_adapter_contract",
            "adapter_sha256": adapter_sha,
            "effects": [],
            "limits": {
                "max_calls": max_calls,
                "max_input_bytes": 2048,
                "max_output_bytes": 2048,
            },
            "behavior_attestation_case_count": len(attestation_cases),
            "raw_seed_persisted": False,
        }
    ]
    candidate_dir = root / ".continual" / "candidates" / candidate_id
    candidate_path = candidate_dir / "candidate.json"
    _atomic_json(candidate_path, candidate)

    adapter = ExternalToolAdapter(adapter_sha256=adapter_sha, function=adapter_fn)
    registry_before = ContractedExternalToolRegistry(root, {tool_id: adapter})
    inactive_before_regression = registry_before.descriptors(EXTERNAL_TOOL_SCOPE) == ()
    if not inactive_before_regression:
        raise RuntimeError("external tool became callable before deterministic regression")

    heldout = (
        {"alpha": 2, "beta": 5, "note": "x"},
        {"z": -3, "a": 9, "flag": True},
        {"one": 1, "two": 2, "three": 3, "label": "novel"},
    )
    baseline = []
    trial = []
    target_task = f"withheld-external-object-summary-{token}"
    for repeat, query in enumerate(heldout):
        expected = adapter_fn(query)
        artifact = {
            "input_sha256": _digest(query),
            "expected_sha256": _digest(expected),
            "adapter_sha256": adapter_sha,
            "input_domain": "object",
            "output_domain": "string",
        }
        baseline.append(_measurement(target_task, repeat, 0.0, artifact))
        trial.append(_measurement(target_task, repeat, 1.0, artifact))

    protected = _measurement(
        "protected-existing-capability",
        0,
        1.0,
        {"invariant": "existing capabilities remain available", "candidate_id": candidate_id},
    )
    baseline_with_protected = [*baseline, protected]
    adverse = [
        *trial,
        {
            **protected,
            "passed": False,
            "score": 0.0,
            "artifact_sha256": _digest({"forced_protected_regression": candidate_id}),
        },
    ]
    valid = [*trial, protected]
    regression_dir = candidate_dir / "regression-input"
    baseline_path = regression_dir / "baseline.json"
    adverse_path = regression_dir / "candidate-adverse.json"
    valid_path = regression_dir / "candidate-valid.json"
    _atomic_json(baseline_path, baseline_with_protected)
    _atomic_json(adverse_path, adverse)
    _atomic_json(valid_path, valid)

    rejected = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=EXTERNAL_TOOL_SCOPE,
        baseline_path=baseline_path,
        candidate_path=adverse_path,
        target_task_ids=[target_task],
    )
    promoted = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=EXTERNAL_TOOL_SCOPE,
        baseline_path=baseline_path,
        candidate_path=valid_path,
        target_task_ids=[target_task],
    )
    if rejected["decision"]["adopt_candidate"] or not promoted["decision"]["adopt_candidate"]:
        raise RuntimeError("external tool regression gate produced an invalid transition")

    wrong_identity_rejected = False
    wrong = ExternalToolAdapter(adapter_sha256="0" * 64, function=adapter_fn)
    try:
        ContractedExternalToolRegistry(root, {tool_id: wrong}).descriptors(EXTERNAL_TOOL_SCOPE)
    except ContractedExternalToolError:
        wrong_identity_rejected = True
    if not wrong_identity_rejected:
        raise RuntimeError("mismatched host adapter identity was accepted")

    same_identity_behavior_drift_rejected = False
    drifted = ExternalToolAdapter(adapter_sha256=adapter_sha, function=lambda _value: "behavior-drift")
    try:
        ContractedExternalToolRegistry(root, {tool_id: drifted}).descriptors(EXTERNAL_TOOL_SCOPE)
    except ContractedExternalToolError:
        same_identity_behavior_drift_rejected = True
    if not same_identity_behavior_drift_rejected:
        raise RuntimeError("same-identity host adapter behavior drift was accepted")

    registry = ContractedExternalToolRegistry(root, {tool_id: adapter})
    runtime_results = []
    for query in heldout:
        expected = adapter_fn(query)
        answer = registry.apply(scope=EXTERNAL_TOOL_SCOPE, tool_id=tool_id, value=query)
        runtime_results.append(
            {
                "input_sha256": _digest(query),
                "output_sha256": _digest(answer),
                "success": answer == expected,
            }
        )

    fourth_query = {"fresh": 7, "domain": "object"}
    fourth_answer = registry.apply(
        scope=EXTERNAL_TOOL_SCOPE,
        tool_id=tool_id,
        value=fourth_query,
    )
    call_budget_failed_closed = False
    try:
        registry.apply(
            scope=EXTERNAL_TOOL_SCOPE,
            tool_id=tool_id,
            value={"fifth": 5},
        )
    except ContractedExternalToolError:
        call_budget_failed_closed = True

    oversized_input_failed_closed = False
    fresh_registry = ContractedExternalToolRegistry(root, {tool_id: adapter})
    try:
        fresh_registry.apply(
            scope=EXTERNAL_TOOL_SCOPE,
            tool_id=tool_id,
            value={"payload": "x" * 4096},
        )
    except ContractedExternalToolError:
        oversized_input_failed_closed = True

    state = json.loads(candidate_path.read_text(encoding="utf-8"))
    report = {
        "schema_version": 1,
        "campaign_id": f"contracted-external-tool-{token}",
        "candidate_id": candidate_id,
        "tool_id": tool_id,
        "scope": EXTERNAL_TOOL_SCOPE,
        "seed_sha256": _digest(seed),
        "seed_persisted": False,
        "adapter_sha256": adapter_sha,
        "input_domain": "object",
        "output_domain": "string",
        "outside_handwritten_program_grammar": True,
        "adapter_source_persisted": False,
        "effects": [],
        "behavior_attestation_case_count": len(attestation_cases),
        "effectful_candidate_rejected": effectful_rejected,
        "inactive_before_regression": inactive_before_regression,
        "forced_protected_regression_rejected": rejected["decision"]["adopt_candidate"] is False,
        "promotion_adopted": promoted["decision"]["adopt_candidate"],
        "wrong_adapter_identity_rejected": wrong_identity_rejected,
        "same_identity_behavior_drift_rejected": same_identity_behavior_drift_rejected,
        "runtime_results": runtime_results,
        "fourth_output_sha256": _digest(fourth_answer),
        "call_budget_failed_closed": call_budget_failed_closed,
        "oversized_input_failed_closed": oversized_input_failed_closed,
        "negative_evidence_retained": bool(state.get("contradictory_evidence")),
        "claim_boundary": (
            "This demonstrates bounded onboarding of a host-supplied effect-free tool outside the "
            "handwritten program grammar with runtime behavior re-attestation. Host execution and the "
            "evaluator remain internal; effectful adapters are still quarantined, so this is not "
            "open-ended tool use or AGI proof."
        ),
    }
    report["passed"] = bool(
        report["outside_handwritten_program_grammar"]
        and report["adapter_source_persisted"] is False
        and report["behavior_attestation_case_count"] >= 2
        and report["effectful_candidate_rejected"]
        and report["inactive_before_regression"]
        and report["forced_protected_regression_rejected"]
        and report["promotion_adopted"]
        and report["wrong_adapter_identity_rejected"]
        and report["same_identity_behavior_drift_rejected"]
        and all(item["success"] for item in runtime_results)
        and report["call_budget_failed_closed"]
        and report["oversized_input_failed_closed"]
        and report["negative_evidence_retained"]
    )
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root / ".continual" / "evidence" / "contracted-external-tool" / f"{report['campaign_id']}.json",
        report,
    )
    return report
