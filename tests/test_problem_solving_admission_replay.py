from __future__ import annotations

import json
from pathlib import Path

import pytest

from continual.problem_solving import (
    Evaluation,
    EvidenceReplayError,
    ExistingSolutionAudit,
    Forecast,
    ProblemSolvingController,
    ProblemSolvingHooks,
    ProblemSpec,
    SolutionCandidate,
)
from continual.problem_solving.model import _canon, _hash


class DirectHooks(ProblemSolvingHooks):
    def forecast(self, session, root):
        return Forecast(1, "one direct root")

    def audit_existing_solution(self, session, node, unaudited_node_ids):
        return ExistingSolutionAudit(tuple(unaudited_node_ids))

    def attempt_solution(self, session, node, audit):
        return SolutionCandidate("verified direct result", artifact={"answer": 42})

    def evaluate(self, session, node, candidate):
        return Evaluation(True, {item: True for item in node["success_criteria"]})


def test_hash_valid_but_semantically_invalid_admission_fails_replay(tmp_path: Path):
    controller = ProblemSolvingController(tmp_path)
    controller.start(
        ProblemSpec("root", "admission replay", ("correct",)),
        session_id="admission",
    )
    session = controller.load("admission")
    session._forecast(DirectHooks(), None, None)

    path = controller.store.event_path("admission")
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    admitted_index = next(
        index for index, record in enumerate(records) if record["stage"] == "admitted"
    )
    record = records[admitted_index]
    record["payload"]["admission"]["mode"] = "exclusive"
    body = {key: value for key, value in record.items() if key != "event_digest"}
    record["event_digest"] = _hash(body)
    prefix = records[: admitted_index + 1]
    prefix[admitted_index] = record
    path.write_text(
        "\n".join(_canon(item) for item in prefix) + "\n", encoding="utf-8"
    )

    with pytest.raises(EvidenceReplayError, match="admission mode mismatch"):
        controller.store.replay("admission", repair=True)


def test_stale_claim_is_closed_before_takeover_and_heartbeat_is_durable(tmp_path: Path):
    session = ProblemSolvingController(tmp_path).start(
        ProblemSpec("root", "claim lifecycle", ("done",)),
        session_id="claim-lifecycle",
    )
    session.claim_work(
        worker_id="worker-a",
        node_id="root",
        scope="root",
        claim_id="old",
        stale_after_seconds=1,
    )
    session.state["claims"]["old"]["heartbeat_at"] = "2000-01-01T00:00:00Z"

    new = session.claim_work(
        worker_id="worker-b",
        node_id="root",
        scope="root",
        claim_id="new",
    )

    assert session.state["claims"]["old"]["status"] == "stale"
    assert new["status"] == "active"
    before = new["heartbeat_at"]
    session.heartbeat_claim("new")
    assert session.state["claims"]["new"]["heartbeat_at"] >= before
    replayed = session.store.replay("claim-lifecycle")
    assert replayed["claims"]["old"]["status"] == "stale"
    assert replayed["claims"]["new"]["status"] == "active"
    session.abandon("claim lifecycle verified")
