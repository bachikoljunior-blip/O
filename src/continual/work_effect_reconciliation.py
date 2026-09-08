from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Mapping

from .store import Store
from .work_effect_dispatch import (
    AuthorizedWorkEffect,
    _verified_state_and_plan,
)
from .work_effects import (
    WorkEffectError,
    _create_or_replay,
    _effect_dir,
    _public_object,
    _read_record,
    complete_work_effect,
)


ReconciliationCallback = Callable[[dict[str, Any]], Mapping[str, Any]]
_OUTCOMES = {"confirmed_succeeded", "confirmed_not_applied", "unknown"}


def _dispatch_claim(
    root: Path, authorization: AuthorizedWorkEffect
) -> dict[str, Any]:
    store = Store(root)
    path = (
        _effect_dir(root, authorization.run_id, authorization.effect_id)
        / "dispatch.json"
    )
    if not path.exists():
        raise WorkEffectError("reconciliation requires a dispatch claim")
    claim = _read_record(
        store,
        path,
        record_type="effect_dispatch_claim",
        digest_field="dispatch_digest",
        timestamp_field="claimed_at",
    )
    if (
        claim.get("run_id") != authorization.run_id
        or claim.get("effect_id") != authorization.effect_id
        or claim.get("authorization_digest")
        != authorization.authorization_digest
        or claim.get("idempotency_key") != authorization.idempotency_key
    ):
        raise WorkEffectError("effect dispatch claim identity mismatch")
    return claim


def _reconciliation_record(
    root: Path, authorization: AuthorizedWorkEffect
) -> dict[str, Any] | None:
    store = Store(root)
    path = (
        _effect_dir(root, authorization.run_id, authorization.effect_id)
        / "reconciliation.json"
    )
    if not path.exists():
        return None
    record = _read_record(
        store,
        path,
        record_type="effect_reconciliation",
        digest_field="reconciliation_digest",
        timestamp_field="reconciled_at",
    )
    if (
        record.get("run_id") != authorization.run_id
        or record.get("effect_id") != authorization.effect_id
        or record.get("authorization_digest")
        != authorization.authorization_digest
        or record.get("idempotency_key") != authorization.idempotency_key
    ):
        raise WorkEffectError("effect reconciliation identity mismatch")
    return record


def _receipt_result(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "reconciled": True,
        "outcome": "confirmed_succeeded",
        "reconciliation_digest": record["reconciliation_digest"],
        "provider_readback": deepcopy(record["readback"]),
    }


def _result(
    record: Mapping[str, Any], *, replayed: bool, receipt_digest: str | None
) -> dict[str, Any]:
    return {
        "reconciled": True,
        "replayed": replayed,
        "run_id": record["run_id"],
        "effect_id": record["effect_id"],
        "outcome": record["outcome"],
        "authorization_digest": record["authorization_digest"],
        "idempotency_key": record["idempotency_key"],
        "dispatch_digest": record["dispatch_digest"],
        "reconciliation_digest": record["reconciliation_digest"],
        "readback": deepcopy(record["readback"]),
        "receipt_digest": receipt_digest,
    }


def reconcile_work_effect(
    root: Path,
    *,
    authorization: AuthorizedWorkEffect,
    callback: ReconciliationCallback,
) -> dict[str, Any]:
    """Record one provider readback for an ambiguous claimed Work effect.

    This function never issues the original action. It calls only the supplied
    readback callback, persists a semantically distinct reconciliation record,
    and completes the existing receipt only for ``confirmed_succeeded``.
    Provider-specific readback quality remains outside this generic boundary.
    """

    if not isinstance(authorization, AuthorizedWorkEffect):
        raise WorkEffectError("authorization must be an AuthorizedWorkEffect")
    if not callable(callback):
        raise WorkEffectError("callback must be callable")
    root = root.resolve()
    state, _ = _verified_state_and_plan(root, authorization)
    claim = _dispatch_claim(root, authorization)
    existing = _reconciliation_record(root, authorization)
    if existing is not None:
        if existing.get("dispatch_digest") != claim.get("dispatch_digest"):
            raise WorkEffectError("effect reconciliation dispatch mismatch")
        if existing.get("outcome") == "confirmed_succeeded":
            if state["status"] == "completed":
                expected_result = _receipt_result(existing)
                if state.get("result") != expected_result:
                    raise WorkEffectError("reconciled effect receipt mismatch")
                receipt_digest = state["receipt_digest"]
            elif state["status"] == "authorized":
                receipt = complete_work_effect(
                    root,
                    run_id=authorization.run_id,
                    effect_id=authorization.effect_id,
                    authorization_digest=authorization.authorization_digest,
                    result=_receipt_result(existing),
                )
                receipt_digest = receipt["receipt_digest"]
            else:  # pragma: no cover - verified states are closed above
                raise WorkEffectError("invalid reconciled effect state")
        else:
            if state["status"] == "completed":
                raise WorkEffectError("non-success reconciliation has a receipt")
            receipt_digest = None
        return _result(existing, replayed=True, receipt_digest=receipt_digest)
    if state["status"] == "completed":
        raise WorkEffectError("effect is already completed without reconciliation")
    if state["status"] != "authorized":
        raise WorkEffectError("effect is not authorized")

    store = Store(root)
    directory = _effect_dir(root, authorization.run_id, authorization.effect_id)
    attempt = {
        "schema_version": 1,
        "record_type": "effect_reconciliation_claim",
        "run_id": authorization.run_id,
        "effect_id": authorization.effect_id,
        "authorization_digest": authorization.authorization_digest,
        "idempotency_key": authorization.idempotency_key,
        "dispatch_digest": claim["dispatch_digest"],
        "attempt_id": store.new_id("reconcile"),
    }
    attempt["claim_digest"] = store.stable_digest(attempt, length=64)
    attempt["claimed_at"] = store.utc_now()
    _create_or_replay(
        store,
        directory / "reconciliation-claim.json",
        attempt,
        record_type="effect_reconciliation_claim",
        digest_field="claim_digest",
        timestamp_field="claimed_at",
        conflict="effect reconciliation already claimed",
    )

    envelope = {
        "action": deepcopy(dict(authorization.action)),
        "authorization_digest": authorization.authorization_digest,
        "idempotency_key": authorization.idempotency_key,
        "dispatch_digest": claim["dispatch_digest"],
    }
    observed = _public_object(callback(envelope), "reconciliation")
    if set(observed) != {"outcome", "readback"}:
        raise WorkEffectError("reconciliation must contain outcome and readback")
    outcome = observed["outcome"]
    if outcome not in _OUTCOMES:
        raise WorkEffectError("invalid reconciliation outcome")
    readback = _public_object(observed["readback"], "reconciliation.readback")
    record = {
        "schema_version": 1,
        "record_type": "effect_reconciliation",
        "run_id": authorization.run_id,
        "effect_id": authorization.effect_id,
        "authorization_digest": authorization.authorization_digest,
        "idempotency_key": authorization.idempotency_key,
        "dispatch_digest": claim["dispatch_digest"],
        "outcome": outcome,
        "readback_digest": store.stable_digest(readback, length=64),
        "readback": readback,
    }
    record["reconciliation_digest"] = store.stable_digest(record, length=64)
    record["reconciled_at"] = store.utc_now()
    record = _create_or_replay(
        store,
        directory / "reconciliation.json",
        record,
        record_type="effect_reconciliation",
        digest_field="reconciliation_digest",
        timestamp_field="reconciled_at",
        conflict="immutable effect reconciliation conflict",
    )
    receipt_digest: str | None = None
    if outcome == "confirmed_succeeded":
        receipt = complete_work_effect(
            root,
            run_id=authorization.run_id,
            effect_id=authorization.effect_id,
            authorization_digest=authorization.authorization_digest,
            result=_receipt_result(record),
        )
        receipt_digest = receipt["receipt_digest"]
    return _result(record, replayed=False, receipt_digest=receipt_digest)
