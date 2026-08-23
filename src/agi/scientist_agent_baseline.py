from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


RUBRIC_DIMENSIONS = {
    "hypothesis_generation",
    "experiment_execution",
    "mechanical_evaluation",
    "result_inheritance",
    "cost_and_latency",
    "failure_containment",
}
VERDICTS = {"ADOPT_MINIMAL_CHANGE", "EXPERIMENT_REQUIRED", "NO_CHANGE"}
EVIDENCE_CLASSES = {
    "author_system_report",
    "official_implementation",
    "peer_reviewed_author_report",
}


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def validate_scientist_agent_baseline(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a comparison without turning source claims into adoption authority."""

    if value.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    baseline = value.get("baseline")
    if not isinstance(baseline, Mapping):
        raise ValueError("baseline must be an object")
    _nonempty(baseline.get("system"), "baseline.system")
    sources = baseline.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("baseline.sources must be a non-empty array")
    for index, source in enumerate(sources):
        if not isinstance(source, Mapping):
            raise ValueError(f"baseline.sources[{index}] must be an object")
        url = _nonempty(source.get("url"), f"baseline.sources[{index}].url")
        if not url.startswith("https://"):
            raise ValueError("baseline sources must use https URLs")
        if source.get("evidence_class") not in EVIDENCE_CLASSES:
            raise ValueError("unsupported baseline evidence_class")
        supports = source.get("supports")
        if not isinstance(supports, list) or not supports or not all(
            isinstance(item, str) and item.strip() for item in supports
        ):
            raise ValueError("every source must name supported claims")

    inventory = value.get("o_inventory")
    if not isinstance(inventory, Mapping):
        raise ValueError("o_inventory must be an object")
    for dimension in (
        "hypothesis_generation",
        "experiment_execution",
        "mechanical_evaluation",
        "result_inheritance",
    ):
        refs = inventory.get(dimension)
        if not isinstance(refs, list) or not refs or not all(
            isinstance(item, str) and item.strip() for item in refs
        ):
            raise ValueError(f"o_inventory.{dimension} must contain exact references")

    rubric = value.get("rubric")
    if not isinstance(rubric, Mapping) or set(rubric) != RUBRIC_DIMENSIONS:
        raise ValueError("rubric must contain the exact frozen dimensions")
    for dimension, comparison in rubric.items():
        if not isinstance(comparison, Mapping):
            raise ValueError(f"rubric.{dimension} must be an object")
        for field in ("baseline", "o", "comparative_finding"):
            _nonempty(comparison.get(field), f"rubric.{dimension}.{field}")

    decision = value.get("decision")
    if not isinstance(decision, Mapping) or decision.get("verdict") not in VERDICTS:
        raise ValueError("decision.verdict is invalid")
    _nonempty(decision.get("reason"), "decision.reason")
    if decision.get("verdict") == "ADOPT_MINIMAL_CHANGE":
        if decision.get("measured_advantage") in (None, "", "not_yet_measured"):
            raise ValueError("adoption requires a measured advantage")
        if decision.get("implementation_authorized") is not True:
            raise ValueError("adoption must explicitly authorize implementation")
    if decision.get("verdict") == "EXPERIMENT_REQUIRED":
        for field in (
            "candidate_mechanism",
            "minimal_reversible_experiment",
            "prediction",
            "falsifier",
            "rollback",
        ):
            _nonempty(decision.get(field), f"decision.{field}")
        if decision.get("measured_advantage") != "not_yet_measured":
            raise ValueError("an experiment-required decision cannot claim measured advantage")
        if decision.get("implementation_authorized") is not False:
            raise ValueError("an experiment-required decision cannot authorize implementation")

    boundary = value.get("claim_boundary")
    if not isinstance(boundary, Mapping) or any(boundary.get(key) is not False for key in (
        "agi_claim_supported",
        "baseline_success_is_o_success",
        "comparison_is_external_production_evidence",
    )):
        raise ValueError("claim boundary must remain fail-closed")
    return dict(value)


def load_scientist_agent_baseline(path: Path) -> dict[str, Any]:
    return validate_scientist_agent_baseline(json.loads(path.read_text(encoding="utf-8")))
