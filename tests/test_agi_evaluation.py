from __future__ import annotations

from dataclasses import replace

from agi.evaluation import (
    CRITERIA,
    EvidenceRecord,
    EvaluationPolicy,
    evaluate_evidence,
    sign_evidence,
)


TRUSTED = {"attestor-lab": "test-only-secret"}


def production_records(*, include_failure: bool = False) -> list[EvidenceRecord]:
    records: list[EvidenceRecord] = []
    for criterion in CRITERIA:
        independent = EvidenceRecord(
            criterion=criterion.key,
            task_id=f"{criterion.key}-task-a",
            domain="software",
            success=True,
            run_id="run-a",
            artifact_ref=f"evidence/{criterion.key}-a.json",
            evaluator="independent-lab",
            tier="production",
            independent=True,
            producer="system-under-test",
        )
        records.extend(
            [
                sign_evidence(independent, attestor_id="attestor-lab", secret=TRUSTED["attestor-lab"]),
                EvidenceRecord(
                    criterion=criterion.key,
                    task_id=f"{criterion.key}-task-b",
                    domain="research",
                    success=True,
                    run_id="run-b",
                    artifact_ref=f"evidence/{criterion.key}-b.json",
                    evaluator="external-suite",
                    tier="production",
                    independent=False,
                ),
                EvidenceRecord(
                    criterion=criterion.key,
                    task_id=f"{criterion.key}-task-c",
                    domain="software",
                    success=True,
                    run_id="run-b",
                    artifact_ref=f"evidence/{criterion.key}-c.json",
                    evaluator="external-suite",
                    tier="production",
                    independent=False,
                ),
            ]
        )
    if include_failure:
        failure = EvidenceRecord(
            criterion="robustness",
            task_id="robustness-failed-production",
            domain="security",
            success=False,
            run_id="run-c",
            artifact_ref="evidence/failure.json",
            evaluator="red-team",
            tier="production",
            independent=True,
            producer="system-under-test",
        )
        records.append(sign_evidence(failure, attestor_id="attestor-lab", secret=TRUSTED["attestor-lab"]))
    return records


def test_legacy_boolean_assertions_cannot_claim_agi():
    result = evaluate_evidence({criterion.key: True for criterion in CRITERIA})
    assert result["agi_claim_supported"] is False
    assert set(result["missing"]) == {criterion.key for criterion in CRITERIA}


def test_all_claim_grade_thresholds_are_required_and_attested():
    result = evaluate_evidence(production_records(), trusted_attestors=TRUSTED)
    assert result["agi_claim_supported"] is True
    assert result["missing"] == []


def test_bare_independent_flag_cannot_claim_agi_without_trust_key():
    result = evaluate_evidence(production_records())
    assert result["agi_claim_supported"] is False
    assert set(result["missing"]) == {criterion.key for criterion in CRITERIA}


def test_tampered_attestation_fails_closed():
    records = production_records()
    first = records[0]
    records[0] = replace(first, artifact_ref="evidence/tampered.json")
    result = evaluate_evidence(records, trusted_attestors=TRUSTED)
    assert result["agi_claim_supported"] is False
    assert first.criterion in result["missing"]


def test_unresolved_production_failure_blocks_claim():
    result = evaluate_evidence(production_records(include_failure=True), trusted_attestors=TRUSTED)
    assert result["agi_claim_supported"] is False
    assert "robustness" in result["missing"]


def test_development_evidence_is_progress_not_claim_grade():
    records = [
        EvidenceRecord(
            criterion=criterion.key,
            task_id=f"dev-{criterion.key}",
            domain="development",
            success=True,
            run_id="dev-run",
            artifact_ref="report.json",
            evaluator="local-benchmark",
            tier="development",
            independent=False,
        )
        for criterion in CRITERIA
    ]
    result = evaluate_evidence(records, EvaluationPolicy(min_successes_per_criterion=1))
    assert result["agi_claim_supported"] is False
