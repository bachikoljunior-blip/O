from agi.evaluation import CRITERIA, evaluate_evidence


def test_incomplete_evidence_cannot_claim_agi():
    result = evaluate_evidence({"continual_learning": True, "self_improvement": True})
    assert result["agi_claim_supported"] is False
    assert "breadth" in result["missing"]
    assert "transfer" in result["missing"]


def test_all_required_evidence_is_needed():
    evidence = {criterion.key: True for criterion in CRITERIA}
    result = evaluate_evidence(evidence)
    assert result["agi_claim_supported"] is True
    assert result["missing"] == []
