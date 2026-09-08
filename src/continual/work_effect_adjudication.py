from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .store import Store
from .work_effect_dispatch import (
    AuthorizedWorkEffect,
    _verified_state_and_plan,
)
from .work_effect_reconciliation import (
    _dispatch_claim,
    _reconciliation_record,
)
from .work_effects import (
    WorkEffectError,
    _create_or_replay,
    _effect_dir,
    _public_object,
    _read_record,
    complete_work_effect,
)


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_OUTCOMES = {"confirmed_succeeded", "confirmed_not_applied", "unknown"}
_EVIDENCE_KINDS = {
    "authoritative_provider_readback",
    "manual_review",
    "insufficient_evidence",
}


def _reconciliation_claim(
    root: Path, authorization: AuthorizedWorkEffect
) -> dict[str, Any]:
    store = Store(root)
    path = (
        _effect_dir(root, authorization.run_id, authorization.effect_id)
        / "reconciliation-claim.json"
    )
    if not path.exists():
        raise WorkEffectError("reconciliation claim is required")
    claim = _read_record(
        store,
        path,
        record_type="effect_reconciliation_claim",
        digest_field="claim_digest",
        timestamp_field="claimed_at",
    )
    if (
        claim.get("run_id") != authorization.run_id
        or claim.get("effect_id") != authorization.effect_id
        or claim.get("authorization_digest")
        != authorization.authorization_digest
        or claim.get("idempotency_key") != authorization.idempotency_key
    ):
        raise WorkEffectError("effect reconciliation claim identity mismatch")
    return claim


def _adjudication_record(
    root: Path, authorization: AuthorizedWorkEffect
) -> dict[str, Any] | None:
    store = Store(root)
    path = (
        _effect_dir(root, authorization.run_id, authorization.effect_id)
        / "adjudication.json"
    )
    if not path.exists():
        return None
    record = _read_record(
        store,
        path,
        record_type="effect_reconciliation_adjudication",
        digest_field="adjudication_digest",
        timestamp_field="adjudicated_at",
    )
    if (
        record.get("run_id") != authorization.run_id
        or record.get("effect_id") != authorization.effect_id
        or record.get("authorization_digest")
        != authorization.authorization_digest
        or record.get("idempotency_key") != authorization.idempotency_key
    ):
        raise WorkEffectError("effect adjudication identity mismatch")
    return record


def _validated_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    evidence = _public_object(value, "adjudication.evidence")
    if set(evidence) != {"kind", "reference", "digest"}:
        raise WorkEffectError(
            "adjudication evidence must contain kind, reference, and digest"
        )
    if evidence["kind"] not in _EVIDENCE_KINDS:
        raise WorkEffectError("invalid adjudication evidence kind")
    if not isinstance(evidence["reference"], str) or not evidence[
        "reference"
    ].strip():
        raise WorkEffectError("adjudication evidence reference must be non-empty")
    if not isinstance(evidence["digest"], str) or not _DIGEST.fullmatch(
        evidence["digest"]
    ):
        raise WorkEffectError("invalid adjudication evidence digest")
    return evidence


def _receipt_result(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "adjudicated": True,
        "outcome": "confirmed_succeeded",
        "adjudication_digest": record["adjudication_digest"],
        "evidence": deepcopy(record["evidence"]),
        "provider_readback": deepcopy(record["readback"]),
    }


def _result(
    record: Mapping[str, Any], *, replayed: bool, receipt_digest: str | None
) -> dict[str, Any]:
    return {
        "adjudicated": True,
        "replayed": replayed,
        "run_id": record["run_id"],
        "effect_id": record["effect_id"],
        "outcome": record["outcome"],
        "authorization_digest": record["authorization_digest"],
        "idempotency_key": record["idempotency_key"],
        "dispatch_digest": record["dispatch_digest"],
        "reconciliation_claim_digest": record["reconciliation_claim_digest"],
        "adjudication_digest": record["adjudication_digest"],
        "readback": deepcopy(record["readback"]),
        "evidence": deepcopy(record["evidence"]),
        "receipt_digest": receipt_digest,
    }


def _same_decision(
    record: Mapping[str, Any],
    *,
    claim_digest: str,
    outcome: str,
    readback: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> bool:
    return (
        record.get("reconciliation_claim_digest") == claim_digest
        and record.get("outcome") == outcome
        and record.get("readback") == readback
        and record.get("evidence") == evidence
    )


def adjudicate_work_effect(
    root: Path,
    *,
    authorization: AuthorizedWorkEffect,
    reconciliation_claim_digest: str,
    outcome: str,
    readback: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve a failed reconciliation from already-collected evidence.

    This boundary accepts no callback and therefore cannot issue the original
    action or repeat provider readback.  It binds one immutable decision to the
    action authorization, dispatch claim, failed reconciliation claim, and an
    explicit public evidence reference.  Only authoritative confirmed success
    may create a receipt, and the adjudication record is persisted first.

    Authenticating provider-specific evidence is intentionally outside this
    generic boundary; callers must supply an independently verified digest and
    reference.
    """

    if not isinstance(authorization, AuthorizedWorkEffect):
        raise WorkEffectError("authorization must be an AuthorizedWorkEffect")
    if not isinstance(reconciliation_claim_digest, str) or not _DIGEST.fullmatch(
        reconciliation_claim_digest
    ):
        raise WorkEffectError("invalid reconciliation claim digest")
    if outcome not in _OUTCOMES:
        raise WorkEffectError("invalid adjudication outcome")
    public_readback = _public_object(readback, "adjudication.readback")
    public_evidence = _validated_evidence(evidence)
    if outcome == "confirmed_succeeded" and (
        public_evidence["kind"] != "authoritative_provider_readback"
        or not public_readback
    ):
        raise WorkEffectError(
            "confirmed success requires authoritative provider readback evidence"
        )

    root = root.resolve()
    state, _ = _verified_state_and_plan(root, authorization)
    dispatch = _dispatch_claim(root, authorization)
    claim = _reconciliation_claim(root, authorization)
    if claim.get("dispatch_digest") != dispatch.get("dispatch_digest"):
        raise WorkEffectError("effect reconciliation claim dispatch mismatch")
    if claim.get("claim_digest") != reconciliation_claim_digest:
        raise WorkEffectError("effect reconciliation claim digest mismatch")
    if _reconciliation_record(root, authorization) is not None:
        raise WorkEffectError("effect is already reconciled")

    existing = _adjudication_record(root, authorization)
    if existing is not None:
        if existing.get("dispatch_digest") != dispatch.get("dispatch_digest"):
            raise WorkEffectError("effect adjudication dispatch mismatch")
        if not _same_decision(
            existing,
            claim_digest=reconciliation_claim_digest,
            outcome=outcome,
            readback=public_readback,
            evidence=public_evidence,
        ):
            raise WorkEffectError("immutable effect adjudication conflict")
        if outcome == "confirmed_succeeded":
            expected_result = _receipt_result(existing)
            if state["status"] == "completed":
                if state.get("result") != expected_result:
                    raise WorkEffectError("adjudicated effect receipt mismatch")
                receipt_digest = state["receipt_digest"]
            elif state["status"] == "authorized":
                receipt = complete_work_effect(
                    root,
                    run_id=authorization.run_id,
                    effect_id=authorization.effect_id,
                    authorization_digest=authorization.authorization_digest,
                    result=expected_result,
                )
                receipt_digest = receipt["receipt_digest"]
            else:  # pragma: no cover - verified states are closed above
                raise WorkEffectError("invalid adjudicated effect state")
        else:
            if state["status"] == "completed":
                raise WorkEffectError("non-success adjudication has a receipt")
            receipt_digest = None
        return _result(existing, replayed=True, receipt_digest=receipt_digest)

    if state["status"] == "completed":
        raise WorkEffectError("effect is already completed without adjudication")
    if state["status"] != "authorized":
        raise WorkEffectError("effect is not authorized")

    store = Store(root)
    record = {
        "schema_version": 1,
        "record_type": "effect_reconciliation_adjudication",
        "run_id": authorization.run_id,
        "effect_id": authorization.effect_id,
        "authorization_digest": authorization.authorization_digest,
        "idempotency_key": authorization.idempotency_key,
        "dispatch_digest": dispatch["dispatch_digest"],
        "reconciliation_claim_digest": reconciliation_claim_digest,
        "outcome": outcome,
        "readback_digest": store.stable_digest(public_readback, length=64),
        "readback": public_readback,
        "evidence_digest": store.stable_digest(public_evidence, length=64),
        "evidence": public_evidence,
    }
    record["adjudication_digest"] = store.stable_digest(record, length=64)
    record["adjudicated_at"] = store.utc_now()
    record = _create_or_replay(
        store,
        _effect_dir(root, authorization.run_id, authorization.effect_id)
        / "adjudication.json",
        record,
        record_type="effect_reconciliation_adjudication",
        digest_field="adjudication_digest",
        timestamp_field="adjudicated_at",
        conflict="immutable effect adjudication conflict",
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
