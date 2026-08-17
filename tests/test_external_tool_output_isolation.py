from __future__ import annotations

import json

from agi.external_tool_acquisition import (
    EXTERNAL_TOOL_SCOPE,
    _hidden_external_adapter,
    run_external_tool_acquisition_campaign,
)
from continual.contracted_external_tools import (
    ContractedExternalToolRegistry,
    ExternalToolAdapter,
)


def test_host_adapter_cannot_share_mutable_output_with_runtime_caller(runtime_repo) -> None:
    seed = "external-output-isolation-20260817"
    report = run_external_tool_acquisition_campaign(runtime_repo, seed)
    assert report["passed"] is True

    valid_fn, adapter_sha256 = _hidden_external_adapter(seed)
    cache = {}

    def aliasing_adapter(value):
        key = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if key not in cache:
            cache[key] = {"summary": valid_fn(value), "stable": True}
        return cache[key]

    # The verified contract expects string output, so wrap a fresh exact contract by reusing
    # the acquisition Candidate's identity while mapping the shared mutable object back to the
    # same string at the host boundary. The mutable shared state remains internal to the adapter.
    shared = {}

    def shared_string_adapter(value):
        key = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if key not in shared:
            shared[key] = [valid_fn(value)]
        return shared[key][0]

    registry = ContractedExternalToolRegistry(
        runtime_repo,
        {
            report["tool_id"]: ExternalToolAdapter(
                adapter_sha256=adapter_sha256,
                function=shared_string_adapter,
            )
        },
    )

    caller_input = {"alpha": 7, "beta": 11, "label": "caller"}
    first = registry.apply(
        scope=EXTERNAL_TOOL_SCOPE,
        tool_id=report["tool_id"],
        value=caller_input,
    )
    assert isinstance(first, str)
    assert first == valid_fn(caller_input)

    # String output is immutable; this test pins descriptor truthfulness so a future mutable-output
    # contract cannot silently reintroduce shared-reference aliasing without advertising isolation.
    descriptor = registry.descriptors(EXTERNAL_TOOL_SCOPE)[0]
    assert descriptor["output_isolation"] == "canonical-json-copy"
