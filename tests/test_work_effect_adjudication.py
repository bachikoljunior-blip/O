from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from continual.work_effect_adjudication import adjudicate_work_effect
from continual.work_effect_dispatch import (
    AuthorizedWorkEffect,
    dispatch_work_effect,
    load_authorized_work_effect,
)
from continual.work_effect_reconciliation import reconcile_work_effect
from continual.work_effects import (
    WorkEffectError,
    authorize_work_effect,
    prepare_work_effect,
    verify_work_effect,
)
from continual.work_session import WorkSession, submit_work_response


def _root(tmp_path: Path) -> Path:
    shutil.copytree(Path("prompts"), tmp_path / "prompts")
    return tmp_path


def _output(component: str) -> dict:
    result = (
        {"objective": "adjudicate one effect"}
        if component == "entry"
        else {"component": "execute", "goal": "adjudicate one effect"}
    )
    return {
        "result": result,
        "local_learn": {"decision": "NO_CHANGE", "candidates": []},
        "fragment": {"component": component, "observations": ["fixture"]},
    }


def _pending(root: Path) -> dict:
    session = WorkSession(
        root, executor_binding="session-a", model_identity="model-a"
    )
    state = session.start("adjudicate an effect", run_id="run-adjudicate-effect")
    for component in ("entry", "root"):
        request = state["pending"][0]
        submit_work_response(
            root,
            request["invocation_id"],
            _output(component),
            executor_binding="session-a",
            model_identity="model-a",
        )
        state = session.resume("run-adjudicate-effect")
    request = state["pending"][0]
    assert request["component"] == "execute"
    return request


def _action() -> dict:
    return {
        "kind": "github_update_file",
        "target": {
            "repository": "owner/repo",
            "branch": "work/secondary",
            "path": "safe.txt",
        },
        "parameters": {"expected_blob_sha": "a" * 40},
    }


def _authorized(root: Path, request: dict, effect_id: str) -> AuthorizedWorkEffect:
    prepare_work_effect(
        root,
        run_id=request["run_id"],
        effect_id=effect_id,
        invocation_id=request["invocation_id"],
        action=_action(),
        executor_binding="session-a",
        model_identity="model-a",
    )
    authorize_work_effect(
        root,
        run_id=request["run_id"],
        effect_id=effect_id,
        invocation_id=request["invocation_id"],
        request_digest=request["request_digest"],
        action=_action(),
        executor_binding="session-a",
        model_identity="model-a",
    )
    return load_authorized_work_effect(
        root,
        run_id=request["run_id"],
        effect_id=effect_id,
        executor_binding="session-a",
        model_identity="model-a",
    )


def _ambiguous(root: Path, request: dict, effect_id: str) -> AuthorizedWorkEffect:
    authorized = _authorized(root, request, effect_id)
    effect_calls = 0

    def issued(_: dict) -> dict:
        nonlocal effect_calls
        effect_calls += 1
        raise RuntimeError("effect outcome lost")

    with pytest.raises(RuntimeError, match="effect outcome lost"):
        dispatch_work_effect(root, authorization=authorized, callback=issued)
    assert effect_calls == 1
    return authorized


def _failed_reconciliation(
    root: Path, request: dict, effect_id: str
) -> tuple[AuthorizedWorkEffect, str]:
    authorized = _ambiguous(root, request, effect_id)
    readback_calls = 0

    def failed(_: dict) -> dict:
        nonlocal readback_calls
        readback_calls += 1
        raise RuntimeError("readback lost")

    with pytest.raises(RuntimeError, match="readback lost"):
        reconcile_work_effect(root, authorization=authorized, callback=failed)
    assert readback_calls == 1
    claim_path = (
        root
        / ".continual"
        / "runs"
        / request["run_id"]
        / "external-effects"
        / effect_id
        / "reconciliation-claim.json"
    )
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    return authorized, claim["claim_digest"]


def _evidence(kind: str = "authoritative_provider_readback") -> dict:
    return {
        "kind": kind,
        "reference": "provider://owner/repo/effect/safe.txt",
        "digest": "d" * 64,
    }


def test_confirmed_success_adjudication_is_bound_receipted_and_replayed(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    request = _pending(root)
    authorized, claim_digest = _failed_reconciliation(
        root, request, "success-v1"
    )
    readback = {"remote_commit_sha": "c" * 40}

    first = adjudicate_work_effect(
        root,
        authorization=authorized,
        reconciliation_claim_digest=claim_digest,
        outcome="confirmed_succeeded",
        readback=readback,
        evidence=_evidence(),
    )
    assert first["adjudicated"] is True
    assert first["replayed"] is False
    assert first["outcome"] == "confirmed_succeeded"
    assert first["receipt_digest"]

    effect_dir = (
        root
        / ".continual"
        / "runs"
        / request["run_id"]
        / "external-effects"
        / "success-v1"
    )
    record = json.loads(
        (effect_dir / "adjudication.json").read_text(encoding="utf-8")
    )
    authorization = json.loads(
        (effect_dir / "authorization.json").read_text(encoding="utf-8")
    )
    claim = json.loads(
        (effect_dir / "reconciliation-claim.json").read_text(encoding="utf-8")
    )
    assert record["record_type"] == "effect_reconciliation_adjudication"
    assert authorization["record_type"] == "effect_authorization"
    assert claim["record_type"] == "effect_reconciliation_claim"
    assert record["reconciliation_claim_digest"] == claim_digest
    state = verify_work_effect(
        root, run_id=request["run_id"], effect_id="success-v1"
    )
    assert state["status"] == "completed"
    assert state["result"] == {
        "adjudicated": True,
        "outcome": "confirmed_succeeded",
        "adjudication_digest": first["adjudication_digest"],
        "evidence": _evidence(),
        "provider_readback": readback,
    }

    replay = adjudicate_work_effect(
        root,
        authorization=authorized,
        reconciliation_claim_digest=claim_digest,
        outcome="confirmed_succeeded",
        readback=readback,
        evidence=_evidence(),
    )
    assert replay["replayed"] is True
    assert replay["adjudication_digest"] == first["adjudication_digest"]

    # Simulate a crash after the adjudication record was persisted but before
    # the success receipt became durable. Exact replay repairs only the local
    # receipt; it has no callback with which to repeat a provider operation.
    (effect_dir / "receipt.json").unlink()
    recovered = adjudicate_work_effect(
        root,
        authorization=authorized,
        reconciliation_claim_digest=claim_digest,
        outcome="confirmed_succeeded",
        readback=readback,
        evidence=_evidence(),
    )
    assert recovered["replayed"] is True
    assert recovered["receipt_digest"] == first["receipt_digest"]
    assert verify_work_effect(
        root, run_id=request["run_id"], effect_id="success-v1"
    )["status"] == "completed"

    with pytest.raises(WorkEffectError, match="immutable effect adjudication conflict"):
        adjudicate_work_effect(
            root,
            authorization=authorized,
            reconciliation_claim_digest=claim_digest,
            outcome="confirmed_succeeded",
            readback={"remote_commit_sha": "e" * 40},
            evidence=_evidence(),
        )


@pytest.mark.parametrize("outcome", ["unknown", "confirmed_not_applied"])
def test_non_success_adjudication_never_creates_receipt(
    tmp_path: Path, outcome: str
) -> None:
    root = _root(tmp_path)
    request = _pending(root)
    authorized, claim_digest = _failed_reconciliation(
        root, request, f"{outcome}-v1"
    )
    first = adjudicate_work_effect(
        root,
        authorization=authorized,
        reconciliation_claim_digest=claim_digest,
        outcome=outcome,
        readback={"observed": False},
        evidence=_evidence("manual_review"),
    )
    assert first["outcome"] == outcome
    assert first["receipt_digest"] is None
    assert verify_work_effect(
        root, run_id=request["run_id"], effect_id=f"{outcome}-v1"
    )["status"] == "authorized"
    replay = adjudicate_work_effect(
        root,
        authorization=authorized,
        reconciliation_claim_digest=claim_digest,
        outcome=outcome,
        readback={"observed": False},
        evidence=_evidence("manual_review"),
    )
    assert replay["replayed"] is True
    assert replay["receipt_digest"] is None


def test_success_requires_complete_authoritative_evidence_before_record(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    request = _pending(root)
    authorized, claim_digest = _failed_reconciliation(
        root, request, "evidence-v1"
    )
    effect_dir = (
        root
        / ".continual"
        / "runs"
        / request["run_id"]
        / "external-effects"
        / "evidence-v1"
    )
    for evidence in (
        _evidence("manual_review"),
        {"kind": "authoritative_provider_readback", "reference": "x"},
        {**_evidence(), "digest": "not-a-digest"},
    ):
        with pytest.raises(WorkEffectError):
            adjudicate_work_effect(
                root,
                authorization=authorized,
                reconciliation_claim_digest=claim_digest,
                outcome="confirmed_succeeded",
                readback={"remote_commit_sha": "c" * 40},
                evidence=evidence,
            )
        assert not (effect_dir / "adjudication.json").exists()
        assert not (effect_dir / "receipt.json").exists()


def test_invalid_tampered_private_and_already_resolved_states_fail_closed(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    request = _pending(root)

    no_claim = _ambiguous(root, request, "no-reconciliation-claim-v1")
    with pytest.raises(WorkEffectError, match="reconciliation claim"):
        adjudicate_work_effect(
            root,
            authorization=no_claim,
            reconciliation_claim_digest="a" * 64,
            outcome="unknown",
            readback={},
            evidence=_evidence("insufficient_evidence"),
        )

    completed = _authorized(root, request, "completed-v1")
    dispatch_work_effect(
        root,
        authorization=completed,
        callback=lambda _: {"verified_readback": True},
    )
    with pytest.raises(WorkEffectError):
        adjudicate_work_effect(
            root,
            authorization=completed,
            reconciliation_claim_digest="a" * 64,
            outcome="unknown",
            readback={},
            evidence=_evidence("insufficient_evidence"),
        )

    reconciled = _ambiguous(root, request, "reconciled-v1")
    reconcile_work_effect(
        root,
        authorization=reconciled,
        callback=lambda _: {"outcome": "unknown", "readback": {}},
    )
    reconciled_claim = json.loads(
        (
            root
            / ".continual"
            / "runs"
            / request["run_id"]
            / "external-effects"
            / "reconciled-v1"
            / "reconciliation-claim.json"
        ).read_text(encoding="utf-8")
    )
    with pytest.raises(WorkEffectError, match="already reconciled"):
        adjudicate_work_effect(
            root,
            authorization=reconciled,
            reconciliation_claim_digest=reconciled_claim["claim_digest"],
            outcome="unknown",
            readback={},
            evidence=_evidence("insufficient_evidence"),
        )

    forged, forged_claim = _failed_reconciliation(root, request, "forged-v1")
    with pytest.raises(WorkEffectError, match="typed authorization identity"):
        adjudicate_work_effect(
            root,
            authorization=replace(forged, request_digest="f" * 64),
            reconciliation_claim_digest=forged_claim,
            outcome="unknown",
            readback={},
            evidence=_evidence("insufficient_evidence"),
        )

    tampered, tampered_claim = _failed_reconciliation(
        root, request, "tampered-v1"
    )
    claim_path = (
        root
        / ".continual"
        / "runs"
        / request["run_id"]
        / "external-effects"
        / "tampered-v1"
        / "reconciliation-claim.json"
    )
    value = json.loads(claim_path.read_text(encoding="utf-8"))
    value["dispatch_digest"] = "0" * 64
    claim_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(WorkEffectError, match="tampered effect reconciliation claim"):
        adjudicate_work_effect(
            root,
            authorization=tampered,
            reconciliation_claim_digest=tampered_claim,
            outcome="unknown",
            readback={},
            evidence=_evidence("insufficient_evidence"),
        )

    private, private_claim = _failed_reconciliation(root, request, "private-v1")
    with pytest.raises(WorkEffectError, match="forbidden private field"):
        adjudicate_work_effect(
            root,
            authorization=private,
            reconciliation_claim_digest=private_claim,
            outcome="unknown",
            readback={"credentials": "must-not-persist"},
            evidence=_evidence("insufficient_evidence"),
        )
