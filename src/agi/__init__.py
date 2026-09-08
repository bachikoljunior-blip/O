"""Evidence gates and benchmark interfaces for AGI research."""

from .campaign import CampaignReport, run_campaign
from .evaluation import CRITERIA, EvidenceRecord, EvaluationPolicy, evaluate_evidence
from .workspace import WorkspaceReport, run_workspace_suite, workspace_suite

__all__ = [
    "CRITERIA",
    "CampaignReport",
    "EvidenceRecord",
    "EvaluationPolicy",
    "WorkspaceReport",
    "evaluate_evidence",
    "run_campaign",
    "run_workspace_suite",
    "workspace_suite",
]
