from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Criterion:
    key: str
    description: str


CRITERIA = (
    Criterion("breadth", "Succeeds across materially different task domains without task-specific retraining."),
    Criterion("transfer", "Transfers knowledge and skills to novel tasks and environments."),
    Criterion("autonomy", "Plans and completes long-horizon tasks while recovering from failures."),
    Criterion("continual_learning", "Improves from experience while preserving previously verified capabilities."),
    Criterion("self_improvement", "Proposes, tests, and safely adopts improvements to its own procedures."),
    Criterion("robustness", "Maintains performance under distribution shift, adversarial cases, and tool failures."),
)


def evaluate_evidence(evidence: Mapping[str, bool]) -> dict[str, object]:
    """Return a conservative AGI-readiness result from externally verified evidence.

    A missing criterion is a failure, not an implicit success. This deliberately prevents
    the system from declaring AGI based on self-report or a narrow benchmark.
    """
    results = {criterion.key: bool(evidence.get(criterion.key, False)) for criterion in CRITERIA}
    passed = all(results.values())
    return {
        "agi_claim_supported": passed,
        "criteria": results,
        "missing": [key for key, ok in results.items() if not ok],
    }
