from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

from agi.benchmark import AgentAnswer
from agi.heldout import (
    EvaluationIdentity,
    LEGACY_TOOL_RECOVERY_CONTRACT,
    PUBLIC_CONCEPT_CONTRACT_V2,
    ReferenceHeldOutAgent,
    generate_heldout_suite,
    run_heldout_suite,
    seed_commitment,
    validate_heldout_suite,
)


def identities():
    return (
        EvaluationIdentity("generator:test", "generator"),
        EvaluationIdentity("executor:test", "executor"),
        EvaluationIdentity("scorer:test", "scorer"),
    )


def test_generated_suite_is_complete_and_deterministic():
    first = generate_heldout_suite("secret-seed", nonce="n1")
    second = generate_heldout_suite("secret-seed", nonce="n1")
    assert first == second
    result = validate_heldout_suite(first)
    assert result["valid"] is True
    assert result["task_count"] == 12
    assert all(count == 2 for count in result["coverage"].values())


def test_different_seed_changes_exact_instances():
    first = generate_heldout_suite("seed-a")
    second = generate_heldout_suite("seed-b")
    assert [item.public for item in first] != [item.public for item in second]
    assert seed_commitment("seed-a", "heldout-v1") != seed_commitment("seed-b", "heldout-v1")


def test_reference_agent_passes_without_receiving_expected_values():
    seed = "reference-public-seed"
    tasks = generate_heldout_suite(seed)
    generator, executor, scorer = identities()
    report = run_heldout_suite(
        ReferenceHeldOutAgent(),
        tasks,
        seed_commit=seed_commitment(seed, "heldout-v1"),
        generator=generator,
        executor=executor,
        scorer=scorer,
    )
    assert report.passed is True
    assert report.to_dict()["sealed_values_persisted"] is False
    assert all(result.success for result in report.task_results)


def test_executor_receives_redacted_expected_value():
    class SpyAgent:
        name = "spy"

        def solve(self, task, state):
            assert task.expected is None
            assert "secret-seed" not in repr(task.input)
            return AgentAnswer(answer=None)

    seed = "secret-seed"
    generator, executor, scorer = identities()
    report = run_heldout_suite(
        SpyAgent(),
        generate_heldout_suite(seed),
        seed_commit=seed_commitment(seed, "heldout-v1"),
        generator=generator,
        executor=executor,
        scorer=scorer,
    )
    assert report.passed is False


def test_roles_must_be_distinct_identities():
    seed = "x"
    tasks = generate_heldout_suite(seed)
    same = "identity:same"
    try:
        run_heldout_suite(
            ReferenceHeldOutAgent(),
            tasks,
            seed_commit=seed_commitment(seed, "heldout-v1"),
            generator=EvaluationIdentity(same, "generator"),
            executor=EvaluationIdentity(same, "executor"),
            scorer=EvaluationIdentity("scorer:other", "scorer"),
        )
    except ValueError as exc:
        assert "distinct identities" in str(exc)
    else:
        raise AssertionError("same generator/executor identity should be rejected")


def test_report_never_contains_seed_or_expected_field():
    seed = "do-not-persist-this-seed"
    generator, executor, scorer = identities()
    report = run_heldout_suite(
        ReferenceHeldOutAgent(),
        generate_heldout_suite(seed),
        seed_commit=seed_commitment(seed, "heldout-v1"),
        generator=generator,
        executor=executor,
        scorer=scorer,
    )
    payload = repr(report.to_dict())
    assert seed not in payload
    assert "'expected':" not in payload


def test_legacy_tool_contract_remains_byte_semantically_stable():
    tasks = generate_heldout_suite(
        "legacy-contract-regression",
        nonce="heldout-v1",
        tool_recovery_contract=LEGACY_TOOL_RECOVERY_CONTRACT,
    )
    assert tasks == generate_heldout_suite(
        "legacy-contract-regression",
        nonce="heldout-v1",
    )
    encoded = json.dumps(
        [asdict(item) for item in tasks],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == (
        "5b569d0f5f9bae6c609be03c62c86400ef77dc3ddbb60c22e0270fd693ae3297"
    )
    tool_task = next(item for item in tasks if item.public.domain == "tool-use")
    assert tool_task.evaluator == "invariant"
    assert tool_task.expected == {"required": ["verify", "fallback", "checkpoint"]}


def test_public_concept_contract_v2_is_shared_structural_and_redacted():
    seed = "public-concept-contract-v2"
    tasks = generate_heldout_suite(
        seed,
        tool_recovery_contract=PUBLIC_CONCEPT_CONTRACT_V2,
    )
    tool_task = next(item for item in tasks if item.public.domain == "tool-use")
    public_contract = tool_task.public.input["public_concept_contract"]
    assert tool_task.evaluator == "public-concepts-v2"
    assert tool_task.expected == {"public_concept_contract": public_contract}
    assert tool_task.expected["public_concept_contract"] is not public_contract
    assert public_contract["concept_ids"] == [
        "verify_external_state",
        "use_fallback",
        "checkpoint_progress",
    ]
    assert all(
        concept_id in tool_task.public.instruction
        for concept_id in public_contract["concept_ids"]
    )

    class RedactionSpy:
        name = "public-contract-redaction-spy"

        def __init__(self):
            self.reference = ReferenceHeldOutAgent()

        def solve(self, task, state):
            assert task.expected is None
            assert seed not in repr(task.input)
            assert "heldout-v1" not in repr(task.input)
            if task.domain == "tool-use":
                assert task.input["public_concept_contract"] == public_contract
            return self.reference.solve(task, state)

    generator, executor, scorer = identities()
    report = run_heldout_suite(
        RedactionSpy(),
        tasks,
        seed_commit=seed_commitment(seed, "heldout-v1"),
        generator=generator,
        executor=executor,
        scorer=scorer,
    )
    assert report.passed is True
    assert next(result for result in report.task_results if result.domain == "tool-use").success


def test_public_concept_contract_v2_rejects_sealed_public_mismatch():
    tasks = list(
        generate_heldout_suite(
            "public-concept-contract-mismatch",
            tool_recovery_contract=PUBLIC_CONCEPT_CONTRACT_V2,
        )
    )
    index = next(index for index, item in enumerate(tasks) if item.public.domain == "tool-use")
    tool_task = tasks[index]
    tampered_contract = dict(tool_task.public.input["public_concept_contract"])
    tampered_contract["concept_ids"] = ["attacker_controlled"]
    tampered_input = dict(tool_task.public.input)
    tampered_input["public_concept_contract"] = tampered_contract
    tasks[index] = replace(tool_task, public=replace(tool_task.public, input=tampered_input))

    validation = validate_heldout_suite(tasks)
    assert validation["valid"] is False
    assert any("public concept contract mismatch" in error for error in validation["errors"])


def _run_v2_with_tool_answer(answer):
    seed = "public-concept-negative-cases"
    tasks = generate_heldout_suite(
        seed,
        tool_recovery_contract=PUBLIC_CONCEPT_CONTRACT_V2,
    )

    class ToolAnswerAgent:
        name = "public-contract-negative-agent"

        def __init__(self):
            self.reference = ReferenceHeldOutAgent()

        def solve(self, task, state):
            if task.domain == "tool-use":
                return AgentAnswer(answer=answer)
            return self.reference.solve(task, state)

    generator, executor, scorer = identities()
    report = run_heldout_suite(
        ToolAnswerAgent(),
        tasks,
        seed_commit=seed_commitment(seed, "heldout-v1"),
        generator=generator,
        executor=executor,
        scorer=scorer,
    )
    return next(result for result in report.task_results if result.domain == "tool-use")


def test_public_concept_contract_v2_fails_closed_on_bad_structure():
    correct_steps = [
        {"concept_id": "verify_external_state", "action": "Check remote state."},
        {"concept_id": "use_fallback", "action": "Use the approved fallback."},
        {"concept_id": "checkpoint_progress", "action": "Checkpoint before retry."},
    ]
    assert _run_v2_with_tool_answer({"steps": correct_steps}).success is True

    missing = {"steps": correct_steps[:-1]}
    duplicate = {"steps": [correct_steps[0], correct_steps[1], correct_steps[0]]}
    malformed = {
        "steps": [
            correct_steps[0],
            correct_steps[1],
            {**correct_steps[2], "unexpected": "not allowed"},
        ]
    }
    substring_only = {
        "note": "verify_external_state use_fallback checkpoint_progress are words in prose"
    }
    empty_action = {
        "steps": [correct_steps[0], correct_steps[1], {**correct_steps[2], "action": " "}]
    }
    for answer in (missing, duplicate, malformed, substring_only, empty_action):
        assert _run_v2_with_tool_answer(answer).success is False


def test_frozen_blinded_measurement_remains_exact_historical_fail():
    report_path = (
        Path(__file__).resolve().parents[1]
        / ".continual/runs/run-work-mode-handoff-v2/blinded-measurements"
        / "blinded-unit-7ac66d7f847659930dd3729a-v1/scorer-report.json"
    )
    raw = report_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "ba9b112e8b9171c13c15680f9fd7b1edbb422a2e0f29d23fc5c5f22bbef8c0a2"
    )
    payload = json.loads(raw)
    assert payload["report"]["digest"] == (
        "c6f9b9c06fd5b939f12076131dd756062b779766130a9907ad8779b389fc7646"
    )
    assert payload["verdict"] == "FAIL"
    assert payload["bounded_unit_passed"] is False
    assert payload["report"]["passed"] is False
    assert sum(result["success"] for result in payload["report"]["task_results"]) == 11
