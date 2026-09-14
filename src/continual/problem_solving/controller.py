"""Controller for transient role entry, recovery, and restoration."""

from __future__ import annotations

import copy
import uuid
from pathlib import Path

from .model import (
    CompletionPolicy,
    NullOptimizerRoleAdapter,
    OptimizerRoleAdapter,
    ProblemSpec,
    SessionStateError,
    _hash,
    _id,
    _now,
)
from .session import ProblemSolvingSession
from .store import Store


class ProblemSolvingController:
    def __init__(
        self,
        root: Path | str,
        role_adapter: OptimizerRoleAdapter | None = None,
    ):
        self.store = Store(root)
        self.role = role_adapter or NullOptimizerRoleAdapter()

    def start(
        self,
        problem: ProblemSpec,
        *,
        session_id: str | None = None,
        predicted_node_budget: int | None = None,
        hard_node_limit: int = 64,
        completion_policy: CompletionPolicy | None = None,
    ) -> ProblemSolvingSession:
        unfinished = self.store.unfinished()
        if unfinished:
            raise SessionStateError(
                "unfinished session already exists: "
                + ",".join(state["session_id"] for state in unfinished)
            )
        identifier = session_id or f"ps-{uuid.uuid4().hex}"
        _id(identifier, "session_id")
        stamp = _now()
        if hard_node_limit < 1 or (
            predicted_node_budget is not None
            and not 1 <= predicted_node_budget <= hard_node_limit
        ):
            raise ValueError("invalid node budget")
        snapshot = copy.deepcopy(dict(self.role.snapshot_role()))
        policy = completion_policy or CompletionPolicy()
        root = problem.dict()
        root.update(
            {
                "parent_id": None,
                "status": "open",
                "children": [],
                "direct_attempted": False,
                "solution": None,
                "evaluation": None,
                "blocked_reason": None,
                "replaced_by": [],
            }
        )
        state = {
            "schema_version": 1,
            "session_id": identifier,
            "root_id": problem.problem_id,
            "status": "active",
            "mode": "problem_solving",
            "created_at": stamp,
            "updated_at": stamp,
            "role_snapshot": snapshot,
            "role_entered": False,
            "restore_requested": False,
            "role_restored": False,
            "predicted_node_budget": predicted_node_budget,
            "hard_node_limit": hard_node_limit,
            "completion_policy": {
                "require_publish": policy.require_publish,
                "require_merge": policy.require_merge,
            },
            "nodes": {problem.problem_id: root},
            "forecast_history": [],
            "tree_revision": 0,
            "last_forecast_revision": -1,
            "audited_node_ids": [],
            "archived_proposals": [],
            "claims": {},
            "publication_receipt": None,
            "merge_receipt": None,
            "last_error": None,
            "event_count": 0,
            "event_head_digest": None,
        }
        self.store.append(
            state,
            "session",
            "started",
            {
                "problem": problem.dict(),
                "normal_role_snapshot_digest": _hash(snapshot),
            },
        )
        self.store.write_control("problem_solving", identifier, None)
        operation_id = f"{identifier}:enter-problem-solving"
        try:
            self.role.enter_problem_solving(
                session_id=identifier,
                problem=problem.dict(),
                operation_id=operation_id,
            )
        except Exception as exc:
            state["status"] = "interrupted"
            state["last_error"] = f"{type(exc).__name__}: {exc}"
            self.store.append(
                state,
                "session",
                "role_entry_failed",
                {"operation_id": operation_id, "error": state["last_error"]},
            )
            raise
        state["role_entered"] = True
        self.store.append(
            state,
            "session",
            "role_entered",
            {"operation_id": operation_id},
        )
        return ProblemSolvingSession(self.store, state, self.role)

    def load(self, session_id: str) -> ProblemSolvingSession:
        return ProblemSolvingSession(
            self.store,
            self.store.replay(session_id, True),
            self.role,
        )

    def recover(self) -> ProblemSolvingSession | None:
        unfinished = self.store.unfinished()
        if len(unfinished) > 1:
            raise SessionStateError("multiple unfinished sessions")
        if not unfinished:
            control = self.store.read_control()
            if control["mode"] != "normal" or control["active_session_id"] is not None:
                self.store.write_control(
                    "normal", None, control.get("last_session_id")
                )
            return None
        state = unfinished[0]
        session = ProblemSolvingSession(self.store, state, self.role)
        self.store.write_control("problem_solving", state["session_id"], None)
        if state["status"] in {"completing", "abandoning"} and not state["role_restored"]:
            session._restore(
                "completed" if state["status"] == "completing" else "abandoned",
                "recovered pending restoration",
            )
        elif not state["role_entered"]:
            root = state["nodes"][state["root_id"]]
            operation_id = f"{state['session_id']}:enter-problem-solving"
            self.role.enter_problem_solving(
                session_id=state["session_id"],
                problem={
                    key: copy.deepcopy(root[key])
                    for key in (
                        "problem_id",
                        "description",
                        "success_criteria",
                        "metadata",
                    )
                },
                operation_id=operation_id,
            )
            state["role_entered"] = True
            self.store.append(
                state,
                "session",
                "role_entry_recovered",
                {"operation_id": operation_id},
            )
        return session
