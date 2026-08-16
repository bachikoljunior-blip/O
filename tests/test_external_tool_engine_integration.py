from __future__ import annotations

from pathlib import Path

import continual.engine as engine_module
from agi.acquired_program_runtime import _new_runtime_run
from agi.external_tool_acquisition import (
    EXTERNAL_TOOL_SCOPE,
    _hidden_external_adapter,
    run_external_tool_acquisition_campaign,
)
from continual.contracted_external_tools import ExternalToolAdapter
from continual.learning_engine import LearningEnabledEngine


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-external-engine-test"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(f"model call was not expected for mechanical external tool: {component}")


def test_verified_external_tool_executes_through_learning_engine_and_replays_idempotently(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    seed = "engine-integrated-host-object-tool-91"
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)
    report = run_external_tool_acquisition_campaign(runtime_repo, seed)
    adapter_fn, adapter_sha = _hidden_external_adapter(seed)
    adapter = ExternalToolAdapter(adapter_sha256=adapter_sha, function=adapter_fn)

    engine = LearningEnabledEngine(
        runtime_repo,
        external_tool_adapters={report["tool_id"]: adapter},
    )
    catalog = engine._verified_tool_catalog()
    external = [item for item in catalog if item.get("program_kind") == "contracted_external_tool"]
    assert len(external) == 1
    assert external[0]["tool_id"] == report["tool_id"]
    assert external[0]["scope"] == EXTERNAL_TOOL_SCOPE

    run_id = _new_runtime_run(engine)
    query = {"engine": 4, "new": 7, "label": "runtime"}
    payload = {
        "snapshot": {"revision": 0},
        "execution_unit": {
            "goal": "apply a regression-verified host-bound external tool",
            "scope": EXTERNAL_TOOL_SCOPE,
            "learned_tool_call": {"tool_id": report["tool_id"], "input": query},
        },
    }
    first = engine._invoke(run_id, "execute", payload)
    replay = engine._invoke(run_id, "execute", payload)

    assert first == replay
    assert first["result"]["output"] == adapter_fn(query)
    assert first["result"]["program_kind"] == "contracted_external_tool"
    assert first["result"]["support_sha256"] == adapter_sha
    assert NeverModelClient.calls == []

    # The exact replay is journal-reused and therefore does not spend another host-call budget unit.
    for index in range(3):
        value = {"index": index, "value": index + 1}
        output = engine._invoke(
            run_id,
            "execute",
            {
                "snapshot": {"revision": index + 1},
                "execution_unit": {
                    "goal": "apply another bounded external work unit",
                    "scope": EXTERNAL_TOOL_SCOPE,
                    "learned_tool_call": {"tool_id": report["tool_id"], "input": value},
                },
            },
        )
        assert output["result"]["output"] == adapter_fn(value)

    invocations = list((runtime_repo / ".continual" / "runs" / run_id / "invocations").glob("*.json"))
    assert len(invocations) == 4
