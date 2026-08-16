from __future__ import annotations

from agi.evaluation import CRITERIA, EvidenceRecord, EvaluationPolicy, evaluate_evidence


def production_records(*, include_failure: bool = False) -> list[EvidenceRecord]:
    records: list[EvidenceRecord] = []
    for criterion in CRITERIA:
        records.extend(
            [
                EvidenceRecord(
                    criterion=criterion.key,
                    task_id=f"{criterion.key}-task-a",
                    domain="software",
                    success=True,
                    run_id="run-a",
                    artifact_ref=f"evidence/{criterion.key}-a.json",
                    evaluator="independent-lab",
                    tier="production",
                    independent=True,
                ),
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
        records.append(
            EvidenceRecord(
                criterion="robustness",
                task_id="robustness-failed-production",
                domain="security",
                success=False,
                run_id="run-c",
                artifact_ref="evidence/failure.json",
                evaluator="red-team",
                tier="production",
                independent=True,
            )
        )
    return records


def test_legacy_boolean_assertions_cannot_claim_agi():
    result = evaluate_evidence({criterion.key: True for criterion in CRITERIA})
    assert result["agi_claim_supported"] is False
    assert set(result["missing"]) == {criterion.key for criterion in CRITERIA}


def test_all_claim_grade_thresholds_are_required():
    result = evaluate_evidence(production_records())
    assert result["agi_claim_supported"] is True
    assert result["missing"] == []


def test_unresolved_production_failure_blocks_claim():
    result = evaluate_evidence(production_records(include_failure=True))
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
