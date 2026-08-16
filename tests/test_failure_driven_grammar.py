from __future__ import annotations

import json
from pathlib import Path

import pytest

import continual.engine as engine_module
from agi.acquired_programs import (
    AcquiredProgramError,
    ProgramExample,
    execute_program,
    synthesize_program,
)
from agi.failure_driven_grammar import run_failure_driven_fold_campaign


class NeverModelClient:
    calls: list[tuple[str, dict, str | None]] = []

    def __init__(self, root: Path):
        self.root = root
        self.model = "never-model-failure-driven-test"

    def call(self, component: str, payload: dict, prompt_path: str | None = None) -> dict:
        type(self).calls.append((component, payload, prompt_path))
        raise AssertionError(f"model call was not expected in mechanical fold campaign: {component}")


def test_value_dependent_sequence_task_falsifies_old_grammar_and_justifies_fold() -> None:
    examples = (
        ProgramExample([1, 2], 3),
        ProgramExample([3, 4], 7),
        ProgramExample([-2, 7], 5),
    )
    with pytest.raises(AcquiredProgramError, match="no bounded pure typed program"):
        synthesize_program(
            input_domain="sequence",
            output_domain="numeric",
            examples=examples,
            max_nodes=5,
            allow_sequence_folds=False,
        )

    learned = synthesize_program(
        input_domain="sequence",
        output_domain="numeric",
        examples=examples,
        max_nodes=5,
    )
    assert learned.expression == {
        "op": "fold_numeric",
        "arg": {"op": "input"},
        "reducer": "add",
        "initial": 0,
    }
    assert learned.apply([10, -1, 2]) == 11


def test_fold_kernel_is_typed_pure_and_resource_bounded() -> None:
    descriptor = {
        "input_domain": "sequence",
        "output_domain": "numeric",
        "expression": {
            "op": "fold_numeric",
            "arg": {"op": "input"},
            "reducer": "add",
            "initial": 0,
        },
        "effects": [],
        "max_steps": 16,
        "max_output_length": 16,
    }
    with pytest.raises(AcquiredProgramError, match="invalid numeric"):
        execute_program(descriptor, [1, "not-numeric", 3])

    step_limited = {**descriptor, "max_steps": 3}
    with pytest.raises(AcquiredProgramError, match="deterministic step budget"):
        execute_program(step_limited, [1, 2])

    with pytest.raises(AcquiredProgramError, match="reducer must be add or mul"):
        execute_program(
            {
                **descriptor,
                "expression": {
                    "op": "fold_numeric",
                    "arg": {"op": "input"},
                    "reducer": "shell",
                    "initial": 0,
                },
            },
            [1, 2],
        )


def test_failure_driven_fold_reaches_runtime_only_after_regression_and_retains_prior_capability(
    runtime_repo: Path,
    monkeypatch,
) -> None:
    NeverModelClient.calls = []
    monkeypatch.setattr(engine_module, "ModelClient", NeverModelClient)

    report = run_failure_driven_fold_campaign(runtime_repo)

    assert report["passed"] is True
    assert report["pre_extension_failure_observed"] is True
    assert report["admitted_feature"] == "fold_numeric"
    assert report["baseline_score"] == 0.0
    assert report["candidate_score"] == 1.0
    assert report["promotion"]["adopt_candidate"] is True
    assert report["post_restart_runtime_score"] == 1.0
    assert report["prior_capability_score_before_promotion"] == 1.0
    assert report["prior_capability_score_after_promotion"] == 1.0
    assert report["pre_extension_failure_retained"] is True
    assert all(item["success"] for item in report["runtime_results"])
    assert NeverModelClient.calls == []

    candidate_dir = runtime_repo / ".continual" / "candidates" / report["candidate_id"]
    candidate = json.loads((candidate_dir / "candidate.json").read_text(encoding="utf-8"))
    failures = [
        item
        for item in candidate["contradictory_evidence"]
        if item.get("type") == "pre_extension_synthesis_failure"
    ]
    assert len(failures) == 1
    assert candidate["scope_states"][report["scope"]] == "VERIFIED_FOR_SCOPE"
    assert candidate["acquired_program"]["program"]["expression"]["op"] == "fold_numeric"

    trial_files = list((candidate_dir / "trials").glob("*.json"))
    assert len(trial_files) == 1
    ledger = json.loads(trial_files[0].read_text(encoding="utf-8"))
    assert len(ledger["attempts"]) == 1
    assert ledger["attempts"][0]["state"] == "COMPLETED"

    evidence_path = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "failure-driven-grammar"
        / f"{report['campaign_id']}.json"
    )
    persisted = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["admitted_feature"] == "fold_numeric"
    assert "not independent production AGI evidence" in persisted["claim_boundary"]
