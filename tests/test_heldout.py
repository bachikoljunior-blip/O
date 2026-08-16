from __future__ import annotations

from agi.benchmark import AgentAnswer
from agi.heldout import (
    EvaluationIdentity,
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


def test_write_heldout_report_persists_atomically(tmp_path):
    """Persist a heldout report and ensure the artifact omits seeds and expected values
    and that the atomic writer leaves no temporary files behind.
    """
    from agi.heldout import write_heldout_report

    seed = "audit-seed"
    generator, executor, scorer = identities()
    report = run_heldout_suite(
        ReferenceHeldOutAgent(),
        generate_heldout_suite(seed),
        seed_commit=seed_commitment(seed, "heldout-v1"),
        generator=generator,
        executor=executor,
        scorer=scorer,
    )
    out = tmp_path / "heldout-report.json"
    write_heldout_report(report, out)
    assert out.exists()
    data = out.read_text(encoding="utf-8")
    assert seed not in data
    assert "'expected':" not in data
    tmpfile = out.with_suffix(out.suffix + ".tmp")
    assert not tmpfile.exists()
