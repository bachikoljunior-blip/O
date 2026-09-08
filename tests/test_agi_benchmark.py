from __future__ import annotations

from agi.benchmark import ReferenceAgent, core_suite, run_suite, validate_suite
from agi.evaluation import evaluate_evidence


def test_core_suite_has_repeated_coverage_for_every_criterion():
    result = validate_suite(core_suite())
    assert result["valid"] is True
    assert result["task_count"] == 12
    assert all(count >= 2 for count in result["coverage"].values())


def test_reference_agent_validates_harness_only():
    report = run_suite(ReferenceAgent())
    assert report.passed is True
    assert len(report.task_results) == 12
    evidence = report.evidence_records(
        run_id="reference-run",
        artifact_ref="reference-report.json",
        tier="development",
        independent=False,
    )
    assert evaluate_evidence(evidence)["agi_claim_supported"] is False
