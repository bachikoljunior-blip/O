from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from continual.work_session import (
    WorkModelClient,
    WorkModelPending,
    WorkSession,
    WorkSessionError,
    pending_work_invocations,
    submit_work_response,
    verified_work_invocation,
    verify_work_invocations,
)


def _root(tmp_path: Path) -> Path:
    shutil.copytree(Path("prompts"), tmp_path / "prompts")
    return tmp_path


def _output(component: str, request: dict) -> dict:
    fragment = {"component": component, "observations": [f"completed {component}"]}
    if component == "entry":
        result = {"objective": "finish the bounded Work bridge run"}
    elif component == "root":
        snapshot = request["payload"]["snapshot"]
        if snapshot.get("last_result_ref"):
            result = {"component": "task_evaluate", "goal": "evaluate the completed unit"}
        else:
            result = {"component": "execute", "goal": "execute the bounded unit"}
    elif component == "execute":
        result = {"status": "completed", "artifacts": ["work-bridge"]}
    elif component == "task_evaluate":
        result = {"verdict": "PASS", "evidence": ["bounded Work bridge completed"]}
    elif component == "consolidate_episode":
        result = {"outcome": "PASS", "summary": "all semantic components were externalized"}
    elif component == "learn":
        return {
            "result": {"decision": "NO_CHANGE", "candidates": []},
            "fragment": fragment,
        }
    else:  # pragma: no cover - protects the test fixture from silent expansion
        raise AssertionError(component)
    return {
        "result": result,
        "local_learn": {"decision": "NO_CHANGE", "candidates": []},
        "fragment": fragment,
    }


def test_work_start_freezes_without_counting_a_model_error(tmp_path: Path):
    root = _root(tmp_path)
    session = WorkSession(root)
    started = session.start(
        "exercise every semantic component", run_id="run-work-bridge-test"
    )

    snapshot = started["snapshot"]
    assert started["run_id"] == "run-work-bridge-test"
    assert snapshot["phase"] == "entry_pending"
    assert snapshot["error_count"] == 0
    assert "last_error" not in snapshot
    assert len(started["pending"]) == 1
    request = started["pending"][0]
    assert request["component"] == "entry"
    assert request["executor_binding"] == "current_chatgpt_work_session"
    assert request["contract"]["private_reasoning_forbidden"] is True
    assert request["prompt_content"]

    resumed = session.resume("run-work-bridge-test")
    assert resumed["pending"][0]["invocation_id"] == request["invocation_id"]
    native_journal_path = next(
        (root / ".continual" / "runs" / "run-work-bridge-test" / "invocations").glob(
            "*.json"
        )
    )
    native_journal = json.loads(native_journal_path.read_text(encoding="utf-8"))
    assert native_journal["status"] == "awaiting_work_model"
    assert native_journal["attempt"] == 1


def test_work_start_cannot_bypass_an_active_authoritative_run(tmp_path: Path) -> None:
    root = _root(tmp_path)
    session = WorkSession(root)
    active_run_id = "run-authoritative-active"
    session.start("freeze the authoritative request", run_id=active_run_id)
    state_path = root / "agi" / "WORK_EXECUTION_STATE.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "status": "running",
                "active_run_id": active_run_id,
                "exact_continuation": {
                    "pending_work_invocation_id": None,
                },
            }
        ),
        encoding="utf-8",
    )

    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in (root / ".continual").rglob("*")
        if path.is_file()
    }
    with pytest.raises(WorkSessionError, match="cannot start a new Work run"):
        session.start("bypass the broken continuation", run_id="run-unsafe-successor")

    assert not (root / ".continual" / "runs" / "run-unsafe-successor").exists()
    assert {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in (root / ".continual").rglob("*")
        if path.is_file()
    } == before


def test_engine_resume_consumes_the_native_bound_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    run_id = "run-work-bound-resume"
    session = WorkSession(root, model_identity="bound-resume-model")
    started = session.start("consume the exact frozen request", run_id=run_id)
    request = started["pending"][0]
    submit_work_response(
        root,
        request["invocation_id"],
        _output("entry", request),
        executor_binding="current_chatgpt_work_session",
        model_identity="bound-resume-model",
    )

    def reject_rebuild(*args: object, **kwargs: object) -> object:
        raise AssertionError("resume rebuilt a Work request instead of using its binding")

    monkeypatch.setattr(WorkModelClient, "call", reject_rebuild)
    resumed = session.resume(run_id, max_steps=1)
    assert resumed["snapshot"]["phase"] == "root_pending"
    assert resumed["snapshot"]["error_count"] == 0


def test_work_resume_rejects_identity_mismatch_before_any_native_mutation(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    run_id = "run-work-resume-identity-guard"
    session = WorkSession(
        root,
        executor_binding="session-a",
        model_identity="model-a",
    )
    started = session.start("freeze one identity-bound request", run_id=run_id)
    request = started["pending"][0]

    def persisted_bytes() -> dict[str, bytes]:
        bases = (
            root / ".continual" / "runs" / run_id,
            root / ".continual" / "work-model" / "invocations",
        )
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for base in bases
            for path in sorted(base.rglob("*"))
            if path.is_file()
        }

    before = persisted_bytes()
    with pytest.raises(WorkSessionError, match="executor_binding"):
        WorkSession(
            root,
            executor_binding="session-b",
            model_identity="model-a",
        ).resume(run_id)
    assert persisted_bytes() == before

    with pytest.raises(WorkSessionError, match="model_identity"):
        WorkSession(
            root,
            executor_binding="session-a",
            model_identity="model-b",
        ).resume(run_id)
    assert persisted_bytes() == before

    submit_work_response(
        root,
        request["invocation_id"],
        _output("entry", request),
        executor_binding="session-a",
        model_identity="model-a",
    )
    resumed = session.resume(run_id)
    assert len(resumed["pending"]) == 1
    assert resumed["pending"][0]["component"] == "root"
    assert resumed["pending"][0]["executor_binding"] == "session-a"
    assert resumed["pending"][0]["model_identity"] == "model-a"
    assert verify_work_invocations(root, run_id=run_id)["requests"] == 2


def test_work_resume_ignores_older_answered_awaiting_journal(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    run_id = "run-work-recovered-history"
    session = WorkSession(
        root,
        executor_binding="session-a",
        model_identity="model-a",
    )
    started = session.start("preserve one reconstructed history entry", run_id=run_id)
    entry_request = started["pending"][0]
    entry_journal_path = next(
        (root / ".continual" / "runs" / run_id / "invocations").glob("*.json")
    )
    historical_awaiting = json.loads(entry_journal_path.read_text(encoding="utf-8"))
    submit_work_response(
        root,
        entry_request["invocation_id"],
        _output("entry", entry_request),
        executor_binding="session-a",
        model_identity="model-a",
    )
    resumed = session.resume(run_id)
    root_request = resumed["pending"][0]

    # Recreate the exact durable shape left by a prior fenced recovery: an older
    # native journal is still marked awaiting, but its immutable response exists.
    historical_awaiting["invocation_id"] = "invoke-000000000000000000000000"
    historical_path = entry_journal_path.parent / "invoke-000000000000000000000000.json"
    historical_path.write_text(
        json.dumps(historical_awaiting, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    submit_work_response(
        root,
        root_request["invocation_id"],
        _output("root", root_request),
        executor_binding="session-a",
        model_identity="model-a",
    )

    with pytest.raises(WorkSessionError, match="executor_binding"):
        WorkSession(
            root,
            executor_binding="session-b",
            model_identity="model-a",
        ).resume(run_id)

    resumed = session.resume(run_id)
    assert resumed["pending"][0]["component"] == "execute"
    assert resumed["pending"][0]["executor_binding"] == "session-a"
    assert json.loads(historical_path.read_text(encoding="utf-8"))["status"] == (
        "awaiting_work_model"
    )


def test_work_resume_still_rejects_multiple_unanswered_journals(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    run_id = "run-work-duplicate-unanswered"
    session = WorkSession(
        root,
        executor_binding="session-a",
        model_identity="model-a",
    )
    session.start("reject duplicate unanswered requests", run_id=run_id)
    invocation_dir = root / ".continual" / "runs" / run_id / "invocations"
    journal_path = next(invocation_dir.glob("*.json"))
    duplicate = json.loads(journal_path.read_text(encoding="utf-8"))
    duplicate["invocation_id"] = "invoke-111111111111111111111111"
    duplicate_path = invocation_dir / "invoke-111111111111111111111111.json"
    duplicate_path.write_text(
        json.dumps(duplicate, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for base in (
            root / ".continual" / "runs" / run_id,
            root / ".continual" / "work-model" / "invocations",
        )
        for path in sorted(base.rglob("*"))
        if path.is_file()
    }

    with pytest.raises(WorkSessionError, match="multiple unanswered"):
        session.resume(run_id)

    after = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for base in (
            root / ".continual" / "runs" / run_id,
            root / ".continual" / "work-model" / "invocations",
        )
        for path in sorted(base.rglob("*"))
        if path.is_file()
    }
    assert after == before


def test_work_response_is_binding_checked_immutable_and_public(tmp_path: Path):
    root = _root(tmp_path)
    session = WorkSession(root, model_identity="work-model")
    started = session.start("freeze one request")
    request = started["pending"][0]
    output = _output("entry", request)

    with pytest.raises(WorkSessionError, match="executor_binding"):
        submit_work_response(
            root,
            request["invocation_id"],
            output,
            executor_binding="different-session",
            model_identity="work-model",
        )

    with pytest.raises(WorkSessionError, match="model_identity"):
        submit_work_response(
            root,
            request["invocation_id"],
            output,
            executor_binding="current_chatgpt_work_session",
            model_identity="different-model",
        )

    first = submit_work_response(
        root,
        request["invocation_id"],
        output,
        executor_binding="current_chatgpt_work_session",
        model_identity="work-model",
    )
    replay = submit_work_response(
        root,
        request["invocation_id"],
        output,
        executor_binding="current_chatgpt_work_session",
        model_identity="work-model",
    )
    assert replay["response_digest"] == first["response_digest"]
    verified = verified_work_invocation(root, request["invocation_id"])
    assert verified["request"]["request_digest"] == request["request_digest"]
    assert verified["response"]["response_digest"] == first["response_digest"]
    assert verified["output"] == output

    conflicting = _output("entry", request)
    conflicting["result"]["objective"] = "different"
    with pytest.raises(WorkSessionError, match="immutable Work response conflict"):
        submit_work_response(
            root,
            request["invocation_id"],
            conflicting,
            executor_binding="current_chatgpt_work_session",
            model_identity="work-model",
        )

    private = _output("entry", request)
    private["fragment"]["chain_of_thought"] = "must never be persisted"
    other = session.start("a second request")["pending"][-1]
    with pytest.raises(WorkSessionError, match="forbidden private field"):
        submit_work_response(
            root,
            other["invocation_id"],
            private,
            executor_binding="current_chatgpt_work_session",
            model_identity="work-model",
        )

    request_path = (
        root
        / ".continual"
        / "work-model"
        / "invocations"
        / other["invocation_id"]
        / "request.json"
    )
    tampered_request = json.loads(request_path.read_text(encoding="utf-8"))
    tampered_request["payload"]["tampered"] = True
    request_path.write_text(json.dumps(tampered_request), encoding="utf-8")
    with pytest.raises(WorkSessionError, match="tampered Work request"):
        pending_work_invocations(root)


def test_work_response_digest_detects_persisted_metadata_tampering(tmp_path: Path) -> None:
    root = _root(tmp_path)
    session = WorkSession(root, model_identity="work-model")
    request = session.start("freeze response metadata")["pending"][0]
    output = _output("entry", request)
    submit_work_response(
        root,
        request["invocation_id"],
        output,
        executor_binding="current_chatgpt_work_session",
        model_identity="work-model",
    )
    response_path = (
        root
        / ".continual"
        / "work-model"
        / "invocations"
        / request["invocation_id"]
        / "response.json"
    )
    tampered_response = json.loads(response_path.read_text(encoding="utf-8"))
    tampered_response["model_verified"] = True
    response_path.write_text(json.dumps(tampered_response), encoding="utf-8")

    with pytest.raises(WorkSessionError, match="immutable Work response conflict"):
        submit_work_response(
            root,
            request["invocation_id"],
            output,
            executor_binding="current_chatgpt_work_session",
            model_identity="work-model",
        )


def test_post_result_candidate_response_replays_with_frozen_evaluator_mode(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    client = WorkModelClient(
        root,
        run_id="run-work-candidate-post-test",
        executor_binding="current_chatgpt_work_session",
        model_identity="work-model",
    )
    payload = {
        "mode": "post-result",
        "candidate": {"candidate_id": "candidate-test-v1"},
        "execution_unit": {"scope": "test-scope"},
    }

    with pytest.raises(WorkModelPending) as pending:
        client.call(
            "candidate_evaluate",
            payload,
            prompt_path="prompts/candidate_evaluate.md",
        )

    output = {
        "result": {
            "decision": "REMAIN_CANDIDATE",
            "candidate_id": "candidate-test-v1",
            "scope": "test-scope",
        },
        "local_learn": {"decision": "NO_CHANGE", "candidates": []},
        "fragment": {
            "component": "candidate_evaluate",
            "observations": ["post-result evidence is still insufficient"],
        },
    }
    submit_work_response(
        root,
        pending.value.invocation_id,
        output,
        executor_binding="current_chatgpt_work_session",
        model_identity="work-model",
    )

    assert (
        client.call(
            "candidate_evaluate",
            payload,
            prompt_path="prompts/candidate_evaluate.md",
        )
        == output
    )


def test_work_model_drives_native_o_entry_through_learn(tmp_path: Path):
    root = _root(tmp_path)
    session = WorkSession(root, model_identity="work-model-under-test")
    state = session.start("exercise ENTRY Root Execute Task Evaluate Consolidate Episode Learn")
    run_id = state["run_id"]
    seen: list[str] = []

    for _ in range(12):
        pending = pending_work_invocations(root, run_id=run_id)
        if not pending:
            break
        assert len(pending) == 1
        request = pending[0]
        component = request["component"]
        seen.append(component)
        if component == "learn":
            assert request["payload"]["snapshot"]["phase"] == (
                "post_task_learn_pending"
            )
        submit_work_response(
            root,
            request["invocation_id"],
            _output(component, request),
            executor_binding="current_chatgpt_work_session",
            model_identity="work-model-under-test",
        )
        state = session.resume(run_id)
        if state["snapshot"]["status"] == "finished":
            break

    assert state["snapshot"]["status"] == "finished"
    assert seen == [
        "entry",
        "root",
        "execute",
        "root",
        "task_evaluate",
        "consolidate_episode",
        "learn",
    ]
    episode_id = state["snapshot"]["episode_id"]
    assert (root / ".continual" / "episodes" / episode_id / "episode.json").is_file()
    event_text = (root / ".continual" / "runs" / run_id / "events.jsonl").read_text()
    assert event_text.count('"type": "work_model_pending"') == len(seen)
    verification = verify_work_invocations(root, run_id=run_id)
    assert verification["valid"] is True
    assert verification["requests"] == len(seen)
    assert verification["responses"] == len(seen)
    assert verification["pending"] == 0


def test_work_model_consolidates_passing_unit_then_continues_unmet_task(tmp_path: Path):
    root = _root(tmp_path)
    session = WorkSession(root, model_identity="work-model-under-test")
    state = session.start("continue an ambitious task after learning each passing unit")
    run_id = state["run_id"]
    seen: list[str] = []

    for _ in range(12):
        pending = pending_work_invocations(root, run_id=run_id)
        assert len(pending) == 1
        request = pending[0]
        component = request["component"]
        if component == "root" and seen and seen[-1] == "learn":
            break
        seen.append(component)
        response = _output(component, request)
        if component == "task_evaluate":
            response["result"] = {
                "verdict": "FAIL",
                "unit_verdict": "PASS",
                "evidence": ["unit passed but original task remains unmet"],
            }
        first = submit_work_response(
            root,
            request["invocation_id"],
            response,
            executor_binding="current_chatgpt_work_session",
            model_identity="work-model-under-test",
        )
        if component in {"consolidate_episode", "learn"}:
            replay = submit_work_response(
                root,
                request["invocation_id"],
                response,
                executor_binding="current_chatgpt_work_session",
                model_identity="work-model-under-test",
            )
            assert replay["response_digest"] == first["response_digest"]
        state = session.resume(run_id)

    assert seen == [
        "entry",
        "root",
        "execute",
        "root",
        "task_evaluate",
        "consolidate_episode",
        "learn",
    ]
    assert state["snapshot"]["status"] == "continue"
    assert state["snapshot"]["phase"] == "root_pending"
    pending = pending_work_invocations(root, run_id=run_id)
    assert len(pending) == 1
    assert pending[0]["component"] == "root"
    verification = verify_work_invocations(root, run_id=run_id)
    assert verification["valid"] is True
    assert verification["responses"] == len(seen)
    assert verification["pending"] == 1


def test_work_verify_fails_closed_on_missing_completed_native_artifacts(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    run_id = "run-work-artifact-integrity"
    session = WorkSession(root, model_identity="work-model-under-test")
    started = session.start("verify completed native artifacts", run_id=run_id)
    request = started["pending"][0]
    submit_work_response(
        root,
        request["invocation_id"],
        _output("entry", request),
        executor_binding="current_chatgpt_work_session",
        model_identity="work-model-under-test",
    )
    session.resume(run_id, max_steps=1)

    journal = next((root / ".continual" / "runs" / run_id / "invocations").glob("*.json"))
    completed = json.loads(journal.read_text(encoding="utf-8"))
    fragment = root / completed["fragment_ref"]
    fragment_bytes = fragment.read_bytes()
    fragment.unlink()
    with pytest.raises(WorkSessionError, match="fragment is missing"):
        verify_work_invocations(root, run_id=run_id)

    fragment.write_bytes(fragment_bytes)
    local_learn = (
        root
        / ".continual"
        / "runs"
        / run_id
        / "local-learn"
        / f"{completed['invocation_id']}-{completed['component']}.json"
    )
    local_learn.unlink()
    with pytest.raises(WorkSessionError, match="Local Learn artifact is missing"):
        verify_work_invocations(root, run_id=run_id)
