from __future__ import annotations

import json
from pathlib import Path

import pytest

from continual.problem_solving import (
    CompletionPolicy,
    EvidenceReplayError,
    Evaluation,
    ExistingSolutionAudit,
    ExternalReceipt,
    Forecast,
    ProblemSolvingController,
    ProblemSolvingHooks,
    ProblemSpec,
    SessionStateError,
    SolutionCandidate,
)


class DirectHooks(ProblemSolvingHooks):
    def forecast(self, session, root):
        return Forecast(1, "one direct root")

    def audit_existing_solution(self, session, node, unaudited_node_ids):
        return ExistingSolutionAudit(tuple(unaudited_node_ids))

    def attempt_solution(self, session, node, audit):
        return SolutionCandidate("verified direct result", artifact={"answer": 42})

    def evaluate(self, session, node, candidate):
        return Evaluation(True, {item: True for item in node["success_criteria"]})


def test_projection_is_rebuilt_from_valid_append_only_log(tmp_path: Path):
    controller = ProblemSolvingController(tmp_path)
    session = controller.start(
        ProblemSpec("root", "repair projection", ("correct",)),
        session_id="projection",
    )
    session.checkpoint("durable before corruption")
    projection = controller.store.state_path("projection")
    projection.write_text(json.dumps({"corrupt": True}), encoding="utf-8")

    recovered = controller.recover()

    assert recovered is not None
    assert recovered.state["session_id"] == "projection"
    assert json.loads(projection.read_text(encoding="utf-8"))["session_id"] == "projection"
    recovered.abandon("repair test complete")


def test_second_session_is_rejected_until_first_is_terminal(tmp_path: Path):
    controller = ProblemSolvingController(tmp_path)
    first = controller.start(
        ProblemSpec("one", "first", ("done",)), session_id="one-session"
    )
    with pytest.raises(SessionStateError, match="unfinished"):
        controller.start(
            ProblemSpec("two", "second", ("done",)), session_id="two-session"
        )
    first.abandon("free the optimizer")
    second = controller.start(
        ProblemSpec("two", "second", ("done",)), session_id="two-session"
    )
    second.abandon("done")


class PublicationHooks(DirectHooks):
    def __init__(self):
        self.publish_calls = 0
        self.merge_calls = 0

    def publish(self, session, root):
        self.publish_calls += 1
        return ExternalReceipt(True, "artifact://published")

    def merge(self, session, root, publish_receipt):
        self.merge_calls += 1
        assert publish_receipt.reference == "artifact://published"
        return ExternalReceipt(True, "integration://accepted")


def test_optional_publish_and_merge_gates_are_replayable(tmp_path: Path):
    hooks = PublicationHooks()
    session = ProblemSolvingController(tmp_path).start(
        ProblemSpec("root", "publishable problem", ("correct",)),
        session_id="published",
        completion_policy=CompletionPolicy(require_publish=True, require_merge=True),
    )

    state = session.run(hooks)

    assert state["status"] == "completed"
    assert state["publication_receipt"]["verified"] is True
    assert state["merge_receipt"]["verified"] is True
    assert hooks.publish_calls == 1
    assert hooks.merge_calls == 1
    phases = [
        json.loads(line)["phase"]
        for line in session.store.event_path("published")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert "publish" in phases
    assert "merge" in phases


def test_parallel_worker_can_run_only_under_its_fresh_session_claim(tmp_path: Path):
    session = ProblemSolvingController(tmp_path).start(
        ProblemSpec("root", "claimed direct problem", ("correct",)),
        session_id="claimed-run",
    )
    session.claim_work(
        worker_id="worker-a",
        node_id="root",
        scope="root",
        claim_id="worker-a-root",
    )

    state = session.run(
        DirectHooks(), worker_id="worker-a", claim_id="worker-a-root"
    )

    assert state["status"] == "completed"
    assert state["claims"]["worker-a-root"]["status"] == "closed_by_session"


def test_hash_chain_tampering_fails_closed(tmp_path: Path):
    controller = ProblemSolvingController(tmp_path)
    session = controller.start(
        ProblemSpec("root", "tamper evidence", ("correct",)),
        session_id="tamper",
    )
    session.checkpoint("before tamper")
    path = controller.store.event_path("tamper")
    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[-1])
    record["payload"]["note"] = "altered"
    lines[-1] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(EvidenceReplayError, match="digest mismatch"):
        controller.store.replay("tamper", repair=True)
