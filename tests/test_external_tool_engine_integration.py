from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

import continual.engine as engine_module
from agi.acquired_program_runtime import _digest, _new_runtime_run
from agi.external_tool_acquisition import (
    EXTERNAL_TOOL_SCOPE,
    _hidden_external_adapter,
    run_external_tool_acquisition_campaign,
)
from continual.attested_external_tools import (
    ExternalRunnerAttestationError,
    ExternalToolRunner,
    PinnedRunnerAttestationVerifier,
    RunnerIsolationAttestation,
)
from continual.contracted_external_tools import ContractedExternalToolError, ExternalToolAdapter
from continual.learning_engine import LearningEnabledEngine


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-external-engine-test"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(f"model call was not expected for mechanical external tool: {component}")


def _attested_runner(
    seed: str,
) -> tuple[Callable[[Any], str], str, ExternalToolRunner, PinnedRunnerAttestationVerifier]:
    adapter_fn, adapter_sha = _hidden_external_adapter(seed)
    runner_id = "isolated-test-runner"
    verifier_id = "independent-test-pin"
    runner_sha = _digest(
        {
            "kind": "external-isolated-runner-test-double",
            "runner_id": runner_id,
            "transport": "canonical-json",
        }
    )
    attestation = RunnerIsolationAttestation.create(
        verifier_id=verifier_id,
        runner_id=runner_id,
        runner_sha256=runner_sha,
        adapter_sha256=adapter_sha,
    )
    runner = ExternalToolRunner(
        adapter_sha256=adapter_sha,
        runner_sha256=runner_sha,
        attestation=attestation,
        invoke=adapter_fn,
    )
    verifier = PinnedRunnerAttestationVerifier(
        {(verifier_id, runner_id): attestation.statement_sha256}
    )
    return adapter_fn, adapter_sha, runner, verifier


def test_verified_external_tool_executes_through_attested_runner_and_replays_idempotently(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    seed = "engine-integrated-host-object-tool-91"
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)
    report = run_external_tool_acquisition_campaign(runtime_repo, seed)
    adapter_fn, adapter_sha, runner, verifier = _attested_runner(seed)

    engine = LearningEnabledEngine(
        runtime_repo,
        external_tool_runners={report["tool_id"]: runner},
        external_runner_verifier=verifier,
    )
    catalog = engine._verified_tool_catalog()
    external = [item for item in catalog if item.get("program_kind") == "contracted_external_tool"]
    assert len(external) == 1
    assert external[0]["tool_id"] == report["tool_id"]
    assert external[0]["scope"] == EXTERNAL_TOOL_SCOPE
    assert external[0]["runner_isolation_attested"] is True
    assert external[0]["runner_attestation_sha256"] == runner.attestation.statement_sha256

    run_id = _new_runtime_run(engine)
    query = {"engine": 4, "new": 7, "label": "runtime"}
    payload = {
        "snapshot": {"revision": 0},
        "execution_unit": {
            "goal": "apply a regression-verified host-bound external tool through an attested runner",
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

    # The exact replay is journal-reused and therefore does not spend another runner-call budget unit.
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


def test_learning_engine_rejects_arbitrary_in_process_adapter_before_execution(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    seed = "in-process-adapter-must-fail-closed"
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)
    report = run_external_tool_acquisition_campaign(runtime_repo, seed)
    adapter_fn, adapter_sha = _hidden_external_adapter(seed)
    called = False

    def should_never_run(value: Any) -> str:
        nonlocal called
        called = True
        return adapter_fn(value)

    with pytest.raises(ContractedExternalToolError, match="in-process"):
        LearningEnabledEngine(
            runtime_repo,
            external_tool_adapters={
                report["tool_id"]: ExternalToolAdapter(
                    adapter_sha256=adapter_sha,
                    function=should_never_run,
                )
            },
        )
    assert called is False
    assert NeverModelClient.calls == []


def test_untrusted_runner_attestation_fails_before_binding_challenge_invocation(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    seed = "untrusted-runner-pin-must-fail-before-call"
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)
    report = run_external_tool_acquisition_campaign(runtime_repo, seed)
    adapter_fn, adapter_sha, trusted_runner, _trusted_verifier = _attested_runner(seed)
    calls: list[Any] = []

    def counted(value: Any) -> str:
        calls.append(value)
        return adapter_fn(value)

    runner = ExternalToolRunner(
        adapter_sha256=adapter_sha,
        runner_sha256=trusted_runner.runner_sha256,
        attestation=trusted_runner.attestation,
        invoke=counted,
    )
    wrong_verifier = PinnedRunnerAttestationVerifier(
        {
            (runner.attestation.verifier_id, runner.attestation.runner_id): "0" * 64,
        }
    )
    engine = LearningEnabledEngine(
        runtime_repo,
        external_tool_runners={report["tool_id"]: runner},
        external_runner_verifier=wrong_verifier,
    )
    with pytest.raises(ExternalRunnerAttestationError, match="not independently trusted"):
        engine._verified_tool_catalog()
    assert calls == []
    assert NeverModelClient.calls == []
