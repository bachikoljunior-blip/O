from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from continual.candidate_regression import record_candidate_regression
from continual.parameterized_registry import (
    ParameterizedToolRegistry,
    parameterized_tool_candidate_payload,
)

from .parameterized_tools import run_parameterized_tool_campaign


PARAMETERIZED_EXECUTION_SCOPE = "agi/parameterized-tool-execution/string-v1"


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _measurement(task_id: str, repeat: int, score: float, artifact: Any) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "criterion": "self_improvement",
        "domain": "string",
        "repeat_index": repeat,
        "passed": score == 1.0,
        "score": score,
        "artifact_sha256": _digest(artifact),
    }


def run_parameterized_execution_campaign(root: Path, seed: str) -> dict[str, Any]:
    """Promote one acquired parameterized tool only after held-out deterministic regression."""

    if not seed:
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    learning_report = run_parameterized_tool_campaign(seed)
    if not learning_report["passed"]:
        raise RuntimeError("parameterized learning campaign did not pass its development gate")
    learned = next(
        item for item in learning_report["acquired_tools"] if item["tool_id"] == "string-tool"
    )
    heldout = [
        item for item in learning_report["task_results"] if item["task_id"] == "string-tool"
    ]
    if len(heldout) != 2 or not all(item["success"] for item in heldout):
        raise RuntimeError("string parameterized tool lacks two successful held-out trials")

    token = _digest({"seed": seed, "campaign": "parameterized-runtime-v1"})[:16]
    candidate_id = f"parameterized-tool-{token}"
    tool_id = f"parameterized-string-{token}"
    candidate_dir = root / ".continual" / "candidates" / candidate_id
    candidate_path = candidate_dir / "candidate.json"
    candidate = parameterized_tool_candidate_payload(
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=PARAMETERIZED_EXECUTION_SCOPE,
        domain=str(learned["domain"]),
        family=str(learned["family"]),
        parameters=dict(learned["parameters"]),
        support_sha256=str(learned["support_sha256"]),
        description="Apply an evaluator-demonstrated safe parameterized string transformation.",
    )
    candidate["supporting_evidence"] = [
        {
            "type": "parameterized_learning_campaign",
            "learning_report_digest": learning_report["digest"],
            "hidden_program_commitment": learning_report["hidden_program_commitments"]["string"],
        }
    ]
    if candidate_path.exists():
        raise ValueError("parameterized runtime campaign candidate already exists")
    _atomic_json(candidate_path, candidate)

    registry_before = ParameterizedToolRegistry(root)
    inactive_before_regression = registry_before.descriptors(PARAMETERIZED_EXECUTION_SCOPE) == ()
    if not inactive_before_regression:
        raise RuntimeError("parameterized tool became active before deterministic regression")

    baseline: list[dict[str, Any]] = []
    trial: list[dict[str, Any]] = []
    for item in heldout:
        repeat = int(item["repeat_index"])
        artifact = {
            "query_sha256": _digest(item["query"]),
            "expected_sha256": _digest(item["expected"]),
            "learning_report_digest": learning_report["digest"],
        }
        baseline.append(_measurement("parameterized-runtime-transfer", repeat, 0.0, artifact))
        trial.append(
            _measurement(
                "parameterized-runtime-transfer",
                repeat,
                1.0 if item["success"] else 0.0,
                artifact,
            )
        )

    protected_artifact = {
        "kind": "parameterized-registry-safety-invariant",
        "rule": "unverified tools remain uncallable and exact scope is required",
    }
    baseline.append(_measurement("protected-parameterized-registry", 0, 1.0, protected_artifact))
    trial.append(_measurement("protected-parameterized-registry", 0, 1.0, protected_artifact))

    baseline_path = candidate_dir / "regression-input" / "baseline.json"
    trial_path = candidate_dir / "regression-input" / "candidate.json"
    _atomic_json(baseline_path, baseline)
    _atomic_json(trial_path, trial)
    promotion = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=PARAMETERIZED_EXECUTION_SCOPE,
        baseline_path=baseline_path,
        candidate_path=trial_path,
        target_task_ids=["parameterized-runtime-transfer"],
    )

    registry = ParameterizedToolRegistry(root)
    descriptors = registry.descriptors(PARAMETERIZED_EXECUTION_SCOPE)
    if len(descriptors) != 1 or descriptors[0]["tool_id"] != tool_id:
        raise RuntimeError("promoted parameterized tool was not exposed for the exact scope")
    runtime_results = []
    for item in heldout:
        answer = registry.apply(
            scope=PARAMETERIZED_EXECUTION_SCOPE,
            tool_id=tool_id,
            value=item["query"],
        )
        runtime_results.append(
            {
                "query": item["query"],
                "expected": item["expected"],
                "answer": answer,
                "success": answer == item["expected"],
            }
        )

    payload = {
        "candidate_id": candidate_id,
        "tool_id": tool_id,
        "scope": PARAMETERIZED_EXECUTION_SCOPE,
        "learning_report_digest": learning_report["digest"],
        "inactive_before_regression": inactive_before_regression,
        "promotion": promotion,
        "descriptor": descriptors[0],
        "runtime_results": runtime_results,
    }
    return {
        **payload,
        "passed": (
            inactive_before_regression
            and bool(promotion["decision"]["adopt_candidate"])
            and all(item["success"] for item in runtime_results)
        ),
        "digest": _digest(payload),
        "evidence_tier": "development",
        "claim_boundary": (
            "This demonstrates deterministic activation and scoped execution of one acquired safe "
            "parameterized tool. It does not establish arbitrary tool learning, arbitrary code execution, "
            "broad transfer, or AGI."
        ),
    }
