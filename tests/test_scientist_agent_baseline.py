from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from agi.scientist_agent_baseline import load_scientist_agent_baseline, validate_scientist_agent_baseline


ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "agi" / "SCIENTIST_AGENT_BASELINE.json"


def _baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def test_checked_in_baseline_is_valid_and_requires_experiment() -> None:
    value = load_scientist_agent_baseline(BASELINE_PATH)
    assert value["decision"]["verdict"] == "EXPERIMENT_REQUIRED"
    assert value["decision"]["implementation_authorized"] is False
    assert value["claim_boundary"]["agi_claim_supported"] is False


def test_frozen_rubric_rejects_missing_dimension() -> None:
    value = _baseline()
    del value["rubric"]["failure_containment"]
    with pytest.raises(ValueError, match="exact frozen dimensions"):
        validate_scientist_agent_baseline(value)


def test_source_without_supported_claims_is_rejected() -> None:
    value = _baseline()
    value["baseline"]["sources"][0]["supports"] = []
    with pytest.raises(ValueError, match="supported claims"):
        validate_scientist_agent_baseline(value)


def test_unmeasured_advantage_cannot_authorize_adoption() -> None:
    value = _baseline()
    value["decision"]["verdict"] = "ADOPT_MINIMAL_CHANGE"
    value["decision"]["implementation_authorized"] = True
    with pytest.raises(ValueError, match="measured advantage"):
        validate_scientist_agent_baseline(value)


def test_experiment_required_cannot_authorize_implementation() -> None:
    value = _baseline()
    value["decision"]["implementation_authorized"] = True
    with pytest.raises(ValueError, match="cannot authorize implementation"):
        validate_scientist_agent_baseline(value)


@pytest.mark.parametrize(
    "field",
    ["agi_claim_supported", "baseline_success_is_o_success", "comparison_is_external_production_evidence"],
)
def test_claim_boundary_cannot_be_promoted_by_comparison(field: str) -> None:
    value = deepcopy(_baseline())
    value["claim_boundary"][field] = True
    with pytest.raises(ValueError, match="claim boundary"):
        validate_scientist_agent_baseline(value)
