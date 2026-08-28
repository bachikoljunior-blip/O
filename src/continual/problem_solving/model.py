"""Transient, objective-neutral recursive problem-solving for O.

The optimizer stays in its ordinary role until a concrete problem starts one
session.  The session runs J's durable loop (forecast, leaf selection, existing
solution audit, direct attempt, fail-closed evaluation, decomposition,
transversal tree rewrite, child integration, parent/root promotion, optional
publish/merge), then restores the exact prior role.  Parallel claims exist only
inside that session.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Mapping, Protocol, Sequence

UTC = timezone.utc
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
OBSERVE_PHASES = frozenset({"forecast", "select_leaf", "existing_solution_audit"})
EXCLUSIVE_PHASES = frozenset({
    "attempt_solution", "decompose", "evaluate", "integrate_children",
    "solve_parent", "solve_root", "update_problem_tree", "publish", "merge",
})
ALL_PHASES = OBSERVE_PHASES | EXCLUSIVE_PHASES
UNFINISHED = {"active", "interrupted", "completing", "abandoning"}
TERMINAL = {"completed", "abandoned"}


class ProblemSolvingError(RuntimeError):
    pass


class SessionStateError(ProblemSolvingError):
    pass


class EvidenceReplayError(ProblemSolvingError):
    pass


class ClaimConflictError(ProblemSolvingError):
    pass


class PhaseAdmissionError(ProblemSolvingError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _canon(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canon(value).encode()).hexdigest()


def _id(value: str, name: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ValueError(f"invalid {name}: {value!r}")
    return value


def _scope(value: str) -> str:
    parts = [p for p in value.strip().replace("\\", "/").split("/") if p]
    if not parts or any(p in {".", ".."} for p in parts):
        raise ValueError("scope must be a safe non-empty hierarchy")
    return "/".join(parts)


def _path(value: str) -> str:
    path = PurePosixPath(value.strip().replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(x in {"", ".", ".."} for x in path.parts):
        raise ValueError(f"unsafe repository path: {value!r}")
    return path.as_posix()


def _overlap(left: str, right: str) -> bool:
    a, b = left.split("/"), right.split("/")
    length = min(len(a), len(b))
    return a[:length] == b[:length]


@dataclass(frozen=True)
class ProblemSpec:
    problem_id: str
    description: str
    success_criteria: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _id(self.problem_id, "problem_id")
        if not self.description.strip() or any(not x.strip() for x in self.success_criteria):
            raise ValueError("description and criteria must be non-empty")

    def dict(self) -> dict[str, Any]:
        return {
            "problem_id": self.problem_id,
            "description": self.description,
            "success_criteria": list(self.success_criteria),
            "metadata": copy.deepcopy(dict(self.metadata)),
        }


@dataclass(frozen=True)
class Forecast:
    predicted_active_nodes: int
    rationale: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.predicted_active_nodes, bool) or self.predicted_active_nodes < 1:
            raise ValueError("predicted_active_nodes must be positive")


@dataclass(frozen=True)
class SolutionCandidate:
    summary: str
    artifact: Any = None
    source: str = "direct"
    evidence: tuple[Any, ...] = ()

    def dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "artifact": copy.deepcopy(self.artifact),
            "source": self.source,
            "evidence": copy.deepcopy(list(self.evidence)),
        }


@dataclass(frozen=True)
class ExistingSolutionAudit:
    audited_node_ids: tuple[str, ...]
    candidate: SolutionCandidate | None = None
    notes: str = ""


@dataclass(frozen=True)
class Evaluation:
    verified: bool
    criteria: Mapping[str, bool] = field(default_factory=dict)
    reason: str = ""
    evidence: tuple[Any, ...] = ()

    def accepts(self, required: Sequence[str]) -> bool:
        return self.verified is True and all(self.criteria.get(x) is True for x in required)

    def dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "criteria": dict(self.criteria),
            "reason": self.reason,
            "evidence": copy.deepcopy(list(self.evidence)),
        }


@dataclass(frozen=True)
class Decomposition:
    children: tuple[ProblemSpec, ...]
    rationale: str = ""


@dataclass(frozen=True)
class ExternalReceipt:
    verified: bool
    reference: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    def dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "reference": self.reference,
            "details": copy.deepcopy(dict(self.details)),
        }


@dataclass(frozen=True)
class CompletionPolicy:
    require_publish: bool = False
    require_merge: bool = False

    def __post_init__(self) -> None:
        if self.require_merge and not self.require_publish:
            raise ValueError("merge requires publish")


class OptimizerRoleAdapter(Protocol):
    def snapshot_role(self) -> Mapping[str, Any]: ...
    def enter_problem_solving(
        self, *, session_id: str, problem: Mapping[str, Any], operation_id: str
    ) -> None: ...
    def restore_role(
        self, *, session_id: str, snapshot: Mapping[str, Any], operation_id: str
    ) -> None: ...


class NullOptimizerRoleAdapter:
    def snapshot_role(self) -> Mapping[str, Any]:
        return {"role": "optimizer", "mode": "normal"}

    def enter_problem_solving(self, **_: Any) -> None:
        return None

    def restore_role(self, **_: Any) -> None:
        return None


class ProblemSolvingHooks:
    """Domain semantics. Defaults fail closed; Python owns only the control loop."""

    def forecast(self, session: Mapping[str, Any], root: Mapping[str, Any]) -> Forecast:
        return Forecast(max(1, len(session["nodes"])))

    def select_leaf(
        self, session: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]
    ) -> str:
        if not candidates:
            raise SessionStateError("no leaf")
        return str(candidates[0]["problem_id"])

    def audit_existing_solution(
        self, session: Mapping[str, Any], node: Mapping[str, Any], unaudited: Sequence[str]
    ) -> ExistingSolutionAudit:
        return ExistingSolutionAudit(tuple(unaudited))

    def attempt_solution(
        self, session: Mapping[str, Any], node: Mapping[str, Any], audit: ExistingSolutionAudit
    ) -> SolutionCandidate | None:
        return None

    def evaluate(
        self, session: Mapping[str, Any], node: Mapping[str, Any], candidate: SolutionCandidate
    ) -> Evaluation:
        return Evaluation(False, reason="no evaluator")

    def decompose(
        self,
        session: Mapping[str, Any],
        node: Mapping[str, Any],
        failed_candidate: SolutionCandidate | None,
        failed_evaluation: Evaluation | None,
    ) -> Decomposition:
        return Decomposition((), "no decomposition")

    def rewrite_transversal(
        self,
        session: Mapping[str, Any],
        node: Mapping[str, Any],
        proposed: Decomposition,
        overflow_by: int,
    ) -> Decomposition:
        return Decomposition((), "no transversal rewrite")

    def integrate_children(
        self,
        session: Mapping[str, Any],
        node: Mapping[str, Any],
        solved_children: Sequence[Mapping[str, Any]],
    ) -> SolutionCandidate | None:
        return None

    def publish(self, session: Mapping[str, Any], root: Mapping[str, Any]) -> ExternalReceipt:
        return ExternalReceipt(False)

    def merge(
        self,
        session: Mapping[str, Any],
        root: Mapping[str, Any],
        publish: ExternalReceipt,
    ) -> ExternalReceipt:
        return ExternalReceipt(False)
