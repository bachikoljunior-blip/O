from __future__ import annotations

from pathlib import Path

import pytest

from continual.problem_solving import (
    ClaimConflictError,
    Decomposition,
    Evaluation,
    ExistingSolutionAudit,
    Forecast,
    ProblemSolvingController,
    ProblemSolvingHooks,
    ProblemSpec,
    SolutionCandidate,
)


class RoleRecorder:
    def __init__(self) -> None:
        self.mode = "normal"
        self.operations: list[str] = []

    def snapshot_role(self):
        return {"role": "optimizer", "mode": self.mode, "objective": "ordinary"}

    def enter_problem_solving(self, *, session_id, problem, operation_id):
        if operation_id not in self.operations:
            self.operations.append(operation_id)
            self.mode = "problem_solving"

    def restore_role(self, *, session_id, snapshot, operation_id):
        if operation_id not in self.operations:
            self.operations.append(operation_id)
            self.mode = snapshot["mode"]


class DirectHooks(ProblemSolvingHooks):
    def forecast(self, session, root):
        return Forecast(1, "one direct root")

    def audit_existing_solution(self, session, node, unaudited_node_ids):
        return ExistingSolutionAudit(tuple(unaudited_node_ids))

    def attempt_solution(self, session, node, audit):
        return SolutionCandidate("verified direct result", artifact={"answer": 42})

    def evaluate(self, session, node, candidate):
        return Evaluation(True, {item: True for item in node["success_criteria"]})


class TreeHooks(ProblemSolvingHooks):
    def forecast(self, session, root):
        return Forecast(3, "root plus two children")

    def audit_existing_solution(self, session, node, unaudited_node_ids):
        return ExistingSolutionAudit(tuple(unaudited_node_ids))

    def attempt_solution(self, session, node, audit):
        if node["problem_id"] == "root":
            return SolutionCandidate("unverified root shortcut")
        return SolutionCandidate(f"solve {node['problem_id']}")

    def evaluate(self, session, node, candidate):
        accepted = node["problem_id"] != "root" or candidate.source == "integration"
        return Evaluation(
            accepted,
            {item: accepted for item in node["success_criteria"]},
            "shortcut rejected" if not accepted else "",
        )

    def decompose(self, session, node, failed_candidate, failed_evaluation):
        if node["problem_id"] == "root":
            return Decomposition(
                (
                    ProblemSpec("left", "solve left", ("left-pass",)),
                    ProblemSpec("right", "solve right", ("right-pass",)),
                )
            )
        return Decomposition(())

    def integrate_children(self, session, node, solved_children):
        return SolutionCandidate("integrated child results", source="integration")


class RewriteHooks(TreeHooks):
    def forecast(self, session, root):
        return Forecast(2, "root plus one cross-cutting child")

    def decompose(self, session, node, failed_candidate, failed_evaluation):
        if node["problem_id"] == "root":
            return Decomposition(
                (
                    ProblemSpec("a", "local a", ("a",)),
                    ProblemSpec("b", "local b", ("b",)),
                    ProblemSpec("c", "local c", ("c",)),
                ),
                "naive split",
            )
        return Decomposition(())

    def rewrite_transversal(self, session, node, proposed, overflow_by):
        assert overflow_by == 2
        return Decomposition(
            (ProblemSpec("cross", "solve shared cause", ("cross-pass",)),),
            "one transversal replacement",
        )

    def attempt_solution(self, session, node, audit):
        if node["problem_id"] == "root":
            return SolutionCandidate("unverified root shortcut")
        return SolutionCandidate("cross-cutting result")


class RejectHooks(DirectHooks):
    def evaluate(self, session, node, candidate):
        return Evaluation(
            False,
            {item: False for item in node["success_criteria"]},
            "not proven",
        )

    def decompose(self, session, node, failed_candidate, failed_evaluation):
        return Decomposition((), "no justified split")


class InterruptOnceHooks(DirectHooks):
    def __init__(self):
        self.failed = False

    def attempt_solution(self, session, node, audit):
        if not self.failed:
            self.failed = True
            raise RuntimeError("simulated process interruption")
        return super().attempt_solution(session, node, audit)


def test_transient_entry_direct_solve_and_role_restoration(tmp_path: Path):
    role = RoleRecorder()
    controller = ProblemSolvingController(tmp_path, role)
    session = controller.start(
        ProblemSpec("root", "answer the concrete problem", ("correct",)),
        session_id="direct",
    )
    assert role.mode == "problem_solving"
    assert controller.store.read_control()["mode"] == "problem_solving"

    state = session.run(DirectHooks())

    assert state["status"] == "completed"
    assert state["nodes"]["root"]["status"] == "solved"
    assert role.mode == "normal"
    control = controller.store.read_control()
    assert control["mode"] == "normal"
    assert control["active_session_id"] is None
    replayed = controller.store.replay("direct")
    assert replayed["event_head_digest"] == state["event_head_digest"]


def test_direct_failure_decomposes_and_integrates_children(tmp_path: Path):
    role = RoleRecorder()
    session = ProblemSolvingController(tmp_path, role).start(
        ProblemSpec("root", "composite problem", ("root-pass",)),
        session_id="tree",
    )

    state = session.run(TreeHooks(), max_steps=10)

    assert state["status"] == "completed"
    assert state["nodes"]["root"]["status"] == "solved"
    assert state["nodes"]["left"]["status"] == "solved"
    assert state["nodes"]["right"]["status"] == "solved"
    assert state["nodes"]["root"]["solution"]["source"] == "integration"
    assert role.mode == "normal"


def test_forecast_overrun_requires_transversal_tree_rewrite(tmp_path: Path):
    session = ProblemSolvingController(tmp_path).start(
        ProblemSpec("root", "overwide problem", ("root-pass",)),
        session_id="rewrite",
        hard_node_limit=8,
    )

    state = session.run(RewriteHooks(), max_steps=10)

    assert state["status"] == "completed"
    assert set(state["nodes"]) == {"root", "cross"}
    assert state["archived_proposals"][0]["reason"] == "forecast_overrun_transversal_rewrite"
    assert [
        item["problem_id"]
        for item in state["archived_proposals"][0]["replaced_children"]
    ] == ["a", "b", "c"]


def test_evaluation_is_fail_closed_and_does_not_restore_early(tmp_path: Path):
    role = RoleRecorder()
    session = ProblemSolvingController(tmp_path, role).start(
        ProblemSpec("root", "must be proven", ("proof",)),
        session_id="reject",
    )

    state = session.run(RejectHooks())

    assert state["status"] == "active"
    assert state["nodes"]["root"]["status"] == "blocked"
    assert state["nodes"]["root"]["evaluation"]["verified"] is False
    assert role.mode == "problem_solving"
    session.abandon("no justified continuation")
    assert role.mode == "normal"
    assert session.state["status"] == "abandoned"


def test_parallel_claims_are_session_scoped_and_collision_safe(tmp_path: Path):
    session = ProblemSolvingController(tmp_path).start(
        ProblemSpec("root", "parallelizable", ("done",)),
        session_id="claims",
    )
    first = session.claim_work(
        worker_id="worker-a",
        node_id="root",
        scope="root/shared",
        reserved_paths=("src/feature",),
        claim_id="claim-a",
    )
    assert first["claim_id"] == "claim-a"
    with pytest.raises(ClaimConflictError):
        session.claim_work(
            worker_id="worker-b",
            node_id="root",
            scope="root/other",
            reserved_paths=("tests",),
            claim_id="claim-b",
        )
    session.close_claim("claim-a")
    second = session.claim_work(
        worker_id="worker-b",
        node_id="root",
        scope="root/other",
        reserved_paths=("tests",),
        claim_id="claim-b",
    )
    assert second["worker_id"] == "worker-b"
    session.abandon("claim test complete")
    assert session.state["claims"]["claim-b"]["status"] == "closed_by_session"


def test_durable_recovery_resumes_interrupted_session(tmp_path: Path):
    role = RoleRecorder()
    controller = ProblemSolvingController(tmp_path, role)
    session = controller.start(
        ProblemSpec("root", "recoverable problem", ("correct",)),
        session_id="recover",
    )
    hooks = InterruptOnceHooks()
    with pytest.raises(RuntimeError, match="simulated process interruption"):
        session.step(hooks)
    assert session.state["status"] == "interrupted"
    assert role.mode == "problem_solving"

    recovered = ProblemSolvingController(tmp_path, role).recover()
    assert recovered is not None
    state = recovered.run(hooks)

    assert state["status"] == "completed"
    assert state["nodes"]["root"]["status"] == "solved"
    assert role.mode == "normal"
    assert role.operations.count("recover:enter-problem-solving") == 1
    assert role.operations.count("recover:restore-role") == 1
