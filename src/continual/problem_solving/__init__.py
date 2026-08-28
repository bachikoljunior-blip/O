"""Transient, objective-neutral recursive problem-solving for O.

The optimizer remains ordinary until a concrete problem starts one explicit
session. The full durable loop runs inside that session and restores the exact
prior role on verified completion or explicit abandonment.
"""

from .controller import ProblemSolvingController
from .model import (
    ALL_PHASES, EXCLUSIVE_PHASES, OBSERVE_PHASES, ClaimConflictError,
    CompletionPolicy, Decomposition, EvidenceReplayError, Evaluation,
    ExistingSolutionAudit, ExternalReceipt, Forecast, NullOptimizerRoleAdapter,
    OptimizerRoleAdapter, PhaseAdmissionError, ProblemSolvingError,
    ProblemSolvingHooks, ProblemSpec, SessionStateError, SolutionCandidate,
)
from .session import ProblemSolvingSession
from .store import Store

__all__ = [
    "ALL_PHASES", "OBSERVE_PHASES", "EXCLUSIVE_PHASES", "ClaimConflictError",
    "CompletionPolicy", "Decomposition", "EvidenceReplayError", "Evaluation",
    "ExistingSolutionAudit", "ExternalReceipt", "Forecast",
    "NullOptimizerRoleAdapter", "OptimizerRoleAdapter", "PhaseAdmissionError",
    "ProblemSolvingController", "ProblemSolvingError", "ProblemSolvingHooks",
    "ProblemSolvingSession", "ProblemSpec", "SessionStateError",
    "SolutionCandidate", "Store",
]
