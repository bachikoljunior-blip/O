from __future__ import annotations

import copy

import pytest

from agi.heldout import (
    DevelopmentReferenceAgent,
    SuiteConfig,
    generate_suite,
    prepare_suite,
    run_suite,
    suite_commitment,
    verify_report,
)


SEED = bytes.fromhex("ab" * 32)
CONFIG = SuiteConfig(
    suite_id="held-out-suite-test",
    suite_version="1.0",
    challenge_nonce="cd" * 32,
    difficulty=2,
)


def test_generation_is_deterministic_and_commitment_binds_config() -> None:
    first = generate_suite(SEED, CONFIG)
    second = generate_suite(SEED, CONFIG)

    assert [task.reveal_dict() for task in first] == [
        task.reveal_dict() for task in second
    ]
    assert suite_commitment(SEED, CONFIG) == suite_commitment(SEED, CONFIG)

    changed = SuiteConfig(
        suite_id=CONFIG.suite_id,
        suite_version=CONFIG.suite_version,
        challenge_nonce="ce" * 32,
        difficulty=CONFIG.difficulty,
    )
    assert suite_commitment(SEED, CONFIG) != suite_commitment(SEED, changed)
    assert suite_commitment(SEED, CONFIG) != suite_commitment(
        bytes.fromhex("ac" * 32), CONFIG
    )


def test_public_tasks_never_contain_expected_answer_or_seed() -> None:
    tasks = generate_suite(SEED, CONFIG)

    assert len(tasks) == 12
    for task in tasks:
        public = task.public.to_dict()
        rendered = repr(public).lower()
        assert "expected" not in public
        assert "scoring" not in public
        assert "seed" not in public
        assert SEED.hex() not in rendered
        assert task.expected is not None


def test_reference_run_passes_mechanics_without_becoming_agi_evidence() -> None:
    report = run_suite(
        SEED,
        CONFIG,
        DevelopmentReferenceAgent(),
        expected_commitment=suite_commitment(SEED, CONFIG),
    )

    assert report["task_count"] == 12
    assert report["passed_count"] == 12
    assert report["all_tasks_passed"] is True
    assert report["agi_evaluation"]["agi_claim_supported"] is False
    assert all(
        record["source_kind"] == "internal_test"
        for record in report["evidence_records"]
    )
    assert all(
        "expected" not in entry["request"]
        for entry in report["transcript"]
    )
    assert verify_report(report) == {"valid": True, "reasons": []}


def test_wrong_precommitted_seed_is_rejected_before_run() -> None:
    with pytest.raises(ValueError, match="does not match"):
        run_suite(
            SEED,
            CONFIG,
            DevelopmentReferenceAgent(),
            expected_commitment="00" * 32,
        )


def test_transcript_tampering_is_detected_after_reveal() -> None:
    report = run_suite(SEED, CONFIG, DevelopmentReferenceAgent())
    tampered = copy.deepcopy(report)
    tampered["transcript"][0]["output"] = {"answer": 999999}

    result = verify_report(tampered)

    assert result["valid"] is False
    assert any("output digest mismatch" in reason for reason in result["reasons"])
    assert any("locked transcript digest mismatch" in reason for reason in result["reasons"])
    assert any("task scoring mismatch" in reason for reason in result["reasons"])
    assert "report digest mismatch" in result["reasons"]


def test_seed_reveal_tampering_breaks_commitment() -> None:
    report = run_suite(SEED, CONFIG, DevelopmentReferenceAgent())
    tampered = copy.deepcopy(report)
    tampered["seed_reveal_hex"] = "ef" * 32

    result = verify_report(tampered)

    assert result["valid"] is False
    assert "commitment mismatch" in result["reasons"]


def test_public_commitment_document_does_not_reveal_private_seed() -> None:
    secret_document, public_document = prepare_suite(CONFIG, seed=SEED)

    assert secret_document["seed_hex"] == SEED.hex()
    assert "seed_hex" not in public_document
    assert public_document["commitment"] == suite_commitment(SEED, CONFIG)
    assert SEED.hex() not in repr(public_document)
