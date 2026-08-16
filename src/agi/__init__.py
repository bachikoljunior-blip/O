"""Evidence gates and benchmark interfaces for AGI research."""

from .evaluation import CRITERIA, EvidenceRecord, EvaluationPolicy, evaluate_evidence

__all__ = ["CRITERIA", "EvidenceRecord", "EvaluationPolicy", "evaluate_evidence"]
