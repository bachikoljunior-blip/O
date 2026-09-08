from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class TrialLedgerError(ValueError):
    pass


class TrialScopeExhausted(TrialLedgerError):
    pass


@dataclass(frozen=True)
class TrialReservation:
    candidate_id: str
    scope: str
    trial_key: str
    attempt: int
    max_attempts: int
    replayed: bool


def _stable_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _scope_key(scope: str) -> str:
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()[:16]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _validate(candidate_id: str, scope: str, max_attempts: int) -> None:
    if not isinstance(candidate_id, str) or not _SAFE_ID.fullmatch(candidate_id):
        raise TrialLedgerError("candidate_id must contain only letters, digits, '.', '_' or '-'")
    if not isinstance(scope, str) or not scope.strip():
        raise TrialLedgerError("scope must be a non-empty string")
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts <= 0:
        raise TrialLedgerError("max_attempts must be a positive integer")


def _ledger_path(root: Path, candidate_id: str, scope: str) -> Path:
    return (
        root.resolve()
        / ".continual"
        / "candidates"
        / candidate_id
        / "trials"
        / f"{_scope_key(scope)}.json"
    )


def reserve_trial_candidate(
    root: Path,
    *,
    candidate_id: str,
    scope: str,
    candidate_integrity_sha256: str,
    trial_input: Mapping[str, Any],
    max_attempts: int,
) -> TrialReservation:
    """Idempotently reserve one bounded Candidate trial for an exact scope/input.

    Replaying the exact same candidate integrity + scope + trial input returns the original
    reservation without consuming another attempt. Distinct trial inputs consume one attempt until
    the per-scope budget is exhausted. The ledger is deterministic except for audit timestamps.
    """

    _validate(candidate_id, scope, max_attempts)
    if not isinstance(candidate_integrity_sha256, str) or len(candidate_integrity_sha256) != 64:
        raise TrialLedgerError("candidate_integrity_sha256 must be a 64-character digest")
    if not isinstance(trial_input, Mapping):
        raise TrialLedgerError("trial_input must be an object")

    trial_key = _stable_sha256(
        {
            "candidate_id": candidate_id,
            "candidate_integrity_sha256": candidate_integrity_sha256,
            "scope": scope,
            "trial_input": dict(trial_input),
        }
    )
    path = _ledger_path(root, candidate_id, scope)
    now = datetime.now(UTC).isoformat()

    if path.exists():
        ledger = _read_json(path)
        if not isinstance(ledger, dict):
            raise TrialLedgerError("trial ledger must be an object")
        if ledger.get("candidate_id") != candidate_id or ledger.get("scope") != scope:
            raise TrialLedgerError("trial ledger identity mismatch")
        persisted_max = ledger.get("max_attempts")
        if persisted_max != max_attempts:
            raise TrialLedgerError("max_attempts cannot change for an existing scope ledger")
    else:
        ledger = {
            "schema_version": 1,
            "candidate_id": candidate_id,
            "scope": scope,
            "max_attempts": max_attempts,
            "attempts": [],
            "created_at": now,
            "updated_at": now,
        }

    attempts = ledger.get("attempts")
    if not isinstance(attempts, list):
        raise TrialLedgerError("trial ledger attempts must be an array")
    for item in attempts:
        if isinstance(item, Mapping) and item.get("trial_key") == trial_key:
            return TrialReservation(
                candidate_id=candidate_id,
                scope=scope,
                trial_key=trial_key,
                attempt=int(item["attempt"]),
                max_attempts=max_attempts,
                replayed=True,
            )

    if len(attempts) >= max_attempts:
        raise TrialScopeExhausted(
            f"TRIAL_CANDIDATE scope exhausted for {candidate_id!r} / {scope!r}: "
            f"{len(attempts)}/{max_attempts} attempts consumed"
        )

    attempt = len(attempts) + 1
    attempts.append(
        {
            "attempt": attempt,
            "trial_key": trial_key,
            "candidate_integrity_sha256": candidate_integrity_sha256,
            "trial_input_sha256": _stable_sha256(dict(trial_input)),
            "state": "RESERVED",
            "reserved_at": now,
        }
    )
    ledger["attempts"] = attempts
    ledger["updated_at"] = now
    _atomic_json(path, ledger)
    return TrialReservation(
        candidate_id=candidate_id,
        scope=scope,
        trial_key=trial_key,
        attempt=attempt,
        max_attempts=max_attempts,
        replayed=False,
    )


def complete_trial_candidate(
    root: Path,
    *,
    candidate_id: str,
    scope: str,
    trial_key: str,
    outcome: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist a deterministic completion exactly once; exact replay is a no-op."""

    if not isinstance(trial_key, str) or len(trial_key) != 64:
        raise TrialLedgerError("trial_key must be a 64-character digest")
    if not isinstance(outcome, Mapping):
        raise TrialLedgerError("outcome must be an object")
    path = _ledger_path(root, candidate_id, scope)
    if not path.is_file():
        raise TrialLedgerError("trial ledger does not exist")
    ledger = _read_json(path)
    attempts = ledger.get("attempts") if isinstance(ledger, dict) else None
    if not isinstance(attempts, list):
        raise TrialLedgerError("trial ledger attempts must be an array")

    outcome_value = dict(outcome)
    outcome_sha256 = _stable_sha256(outcome_value)
    for item in attempts:
        if not isinstance(item, dict) or item.get("trial_key") != trial_key:
            continue
        if item.get("state") == "COMPLETED":
            if item.get("outcome_sha256") != outcome_sha256:
                raise TrialLedgerError("completed trial replay conflicts with persisted outcome")
            return dict(item)
        if item.get("state") != "RESERVED":
            raise TrialLedgerError("trial is not reservable/completable")
        item["state"] = "COMPLETED"
        item["outcome"] = outcome_value
        item["outcome_sha256"] = outcome_sha256
        item["completed_at"] = datetime.now(UTC).isoformat()
        ledger["updated_at"] = item["completed_at"]
        _atomic_json(path, ledger)
        return dict(item)
    raise TrialLedgerError("trial_key is not reserved in this scope ledger")
