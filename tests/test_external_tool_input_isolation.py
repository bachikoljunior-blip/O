from __future__ import annotations

from agi.external_tool_acquisition import (
    EXTERNAL_TOOL_SCOPE,
    _hidden_external_adapter,
    run_external_tool_acquisition_campaign,
)
from continual.contracted_external_tools import (
    ContractedExternalToolRegistry,
    ExternalToolAdapter,
)


def test_host_adapter_cannot_mutate_caller_owned_json_input(runtime_repo) -> None:
    seed = "external-input-isolation-20260817"
    report = run_external_tool_acquisition_campaign(runtime_repo, seed)
    assert report["passed"] is True

    valid_fn, adapter_sha256 = _hidden_external_adapter(seed)

    def mutating_but_semantically_valid(value):
        answer = valid_fn(value)
        value["host_mutation"] = "must stay inside detached copy"
        return answer

    registry = ContractedExternalToolRegistry(
        runtime_repo,
        {
            report["tool_id"]: ExternalToolAdapter(
                adapter_sha256=adapter_sha256,
                function=mutating_but_semantically_valid,
            )
        },
    )

    descriptors = registry.descriptors(EXTERNAL_TOOL_SCOPE)
    assert len(descriptors) == 1
    assert descriptors[0]["input_isolation"] == "canonical-json-copy"

    caller_input = {"alpha": 7, "beta": 11, "label": "caller-owned"}
    before = dict(caller_input)
    expected = valid_fn(before)
    actual = registry.apply(
        scope=EXTERNAL_TOOL_SCOPE,
        tool_id=report["tool_id"],
        value=caller_input,
    )

    assert actual == expected
    assert caller_input == before
    assert "host_mutation" not in caller_input
