from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from continual.candidate_regression import record_candidate_regression
from continual.contracted_external_tools import (
    ContractedExternalToolRegistry,
    ExternalToolAdapter,
    contracted_external_tool_candidate_payload,
)


def _json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _measurement(task_id: str, score: float, artifact: Any) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "criterion": "self_improvement",
        "domain": "external-tool:object-to-object",
        "repeat_index": 0,
        "passed": score == 1.0,
        "score": score,
        "artifact_sha256": _json_sha256(artifact),
    }


def test_host_adapter_cannot_share_mutable_output_with_runtime_caller(runtime_repo) -> None:
    scope = "tests/contracted-external-tool/output-isolation-v1"
    candidate_id = "external-output-isolation-candidate"
    tool_id = "external-output-isolation-tool"
    adapter_sha256 = hashlib.sha256(b"external-output-isolation-adapter-v1").hexdigest()
    cache: dict[str, dict[str, Any]] = {}

    def aliasing_adapter(value: Any) -> dict[str, Any]:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if key not in cache:
            cache[key] = {"echo": value["x"], "stable": True}
        return cache[key]

    challenge = {"x": 1}
    candidate = contracted_external_tool_candidate_payload(
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=scope,
        input_domain="object",
        output_domain="object",
        effects=(),
        max_calls=4,
        max_input_bytes=1024,
        max_output_bytes=1024,
        adapter_sha256=adapter_sha256,
        description="Mutable object-output adapter used to falsify cross-boundary aliasing.",
        binding_challenges=(
            {
                "input": challenge,
                "output_sha256": _json_sha256(aliasing_adapter(challenge)),
            },
        ),
    )
    candidate_dir = runtime_repo / ".continual" / "candidates" / candidate_id
    _write_json(candidate_dir / "candidate.json", candidate)

    target_task = "withheld-external-output-isolation"
    protected_task = "protected-existing-capability"
    baseline = [
        _measurement(target_task, 0.0, {"candidate_id": candidate_id, "baseline": True}),
        _measurement(protected_task, 1.0, {"protected": True}),
    ]
    trial = [
        _measurement(target_task, 1.0, {"candidate_id": candidate_id, "trial": True}),
        _measurement(protected_task, 1.0, {"protected": True}),
    ]
    regression_dir = candidate_dir / "regression-input"
    baseline_path = regression_dir / "baseline.json"
    trial_path = regression_dir / "candidate-valid.json"
    _write_json(baseline_path, baseline)
    _write_json(trial_path, trial)

    decision = record_candidate_regression(
        runtime_repo,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline_path,
        candidate_path=trial_path,
        target_task_ids=[target_task],
    )
    assert decision["decision"]["adopt_candidate"] is True

    registry = ContractedExternalToolRegistry(
        runtime_repo,
        {
            tool_id: ExternalToolAdapter(
                adapter_sha256=adapter_sha256,
                function=aliasing_adapter,
            )
        },
    )
    descriptor = registry.descriptors(scope)[0]
    assert descriptor["input_isolation"] == "canonical-json-copy"
    assert descriptor["output_isolation"] == "canonical-json-copy"

    caller_input = {"x": 9}
    cache_key = json.dumps(caller_input, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    first = registry.apply(scope=scope, tool_id=tool_id, value=caller_input)
    assert first == {"echo": 9, "stable": True}
    assert first is not cache[cache_key]

    first["echo"] = "caller mutation must not reach host-owned state"
    first["new"] = "caller-only"
    assert cache[cache_key] == {"echo": 9, "stable": True}

    second = registry.apply(scope=scope, tool_id=tool_id, value=caller_input)
    assert second == {"echo": 9, "stable": True}
    assert second is not cache[cache_key]
    assert "new" not in second
