from __future__ import annotations

import json
from pathlib import Path

import pytest

import continual.engine as engine_module
from agi.acquired_program_runtime import _new_runtime_run
from agi.external_tool_acquisition import (
    _hidden_external_adapter,
    run_external_tool_acquisition_campaign,
)
from continual.contracted_external_tools import (
    ContractedExternalToolError,
    ExternalToolAdapter,
)
from continual.external_tool_engine import ContractedExternalToolEngine


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-external-tool-test"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(f"model call was not expected for contracted tool execution: {component}")


def test_unknown_host_tool_is_regression_gated_and_resource_bounded(runtime_repo: Path) -> None:
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


def test_verified_host_tool_executes_in_continual_runtime_with_idempotent_per_run_budget(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    seed = "unknown-host-runtime-tool-20260817"
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)
    report = run_external_tool_acquisition_campaign(runtime_repo, seed)
    adapter_fn, adapter_sha = _hidden_external_adapter(seed)
    adapter = ExternalToolAdapter(adapter_sha256=adapter_sha, function=adapter_fn)
    engine = ContractedExternalToolEngine(runtime_repo, {report["tool_id"]: adapter})

    catalog = [
        item
        for item in engine._verified_tool_catalog()
        if item.get("program_kind") == "contracted_external_tool"
    ]
    assert len(catalog) == 1
    assert catalog[0]["candidate_id"] == report["candidate_id"]
    assert catalog[0]["input_domain"] == "object"

    run_id = _new_runtime_run(engine)

    def invoke(value: dict[str, object]) -> dict:
        return engine._invoke(
            run_id,
            "execute",
            {
                "snapshot": {"revision": 0},
                "execution_unit": {
                    "goal": "use the regression-verified host tool on an unseen object work unit",
                    "scope": report["scope"],
                    "learned_tool_call": {"tool_id": report["tool_id"], "input": value},
                },
            },
        )

    values = [
        {"runtime": 1, "label": "a"},
        {"runtime": 2, "label": "b"},
        {"runtime": 3, "label": "c"},
        {"runtime": 4, "label": "d"},
    ]
    outputs = [invoke(value) for value in values]
    assert all(
        output["result"]["program_kind"] == "contracted_external_tool"
        for output in outputs
    )
    assert all(
        output["result"]["output"] == adapter_fn(value)
        for output, value in zip(outputs, values, strict=True)
    )

    replay = invoke(values[0])
    assert replay == outputs[0]
    events_path = engine.store.run_dir(run_id) / "events.jsonl"
    assert "external_tool_invocation_reused" in events_path.read_text(encoding="utf-8")

    with pytest.raises(ContractedExternalToolError, match="call budget exhausted"):
        invoke({"runtime": 5, "label": "must-fail-closed"})

    second_run_id = _new_runtime_run(engine)
    second_value = {"runtime": 5, "label": "new-run"}
    second = engine._invoke(
        second_run_id,
        "execute",
        {
            "snapshot": {"revision": 0},
            "execution_unit": {
                "goal": "prove a new durable run receives its own bounded tool-call budget",
                "scope": report["scope"],
                "learned_tool_call": {
                    "tool_id": report["tool_id"],
                    "input": second_value,
                },
            },
        },
    )
    assert second["result"]["output"] == adapter_fn(second_value)
    assert NeverModelClient.calls == []
