from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from continual.trial_ledger import (
    TrialLedgerError,
    TrialScopeExhausted,
    complete_trial_candidate,
    reserve_trial_candidate,
)


def _scope_key(scope: str) -> str:
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()[:16]


def test_exact_replay_is_idempotent_and_does_not_consume_budget(tmp_path: Path) -> None:
    digest = "a" * 64
    first = reserve_trial_candidate(
        tmp_path,
        candidate_id="candidate-a",
        scope="symbolic-transfer",
        candidate_integrity_sha256=digest,
        trial_input={"task_ids": ["a", "b"], "seed": 7},
        max_attempts=2,
    )
    replay = reserve_trial_candidate(
        tmp_path,
        candidate_id="candidate-a",
        scope="symbolic-transfer",
        candidate_integrity_sha256=digest,
        trial_input={"task_ids": ["a", "b"], "seed": 7},
        max_attempts=2,
    )

    assert first.attempt == 1
    assert first.replayed is False
    assert replay.trial_key == first.trial_key
    assert replay.attempt == 1
    assert replay.replayed is True

    ledger = json.loads(
        (
            tmp_path
            / ".continual"
            / "candidates"
            / "candidate-a"
            / "trials"
            / f"{_scope_key('symbolic-transfer')}.json"
        ).read_text()
    )
    assert len(ledger["attempts"]) == 1


def test_scope_budget_exhaustion_is_per_scope_and_distinct_inputs_consume_attempts(
    tmp_path: Path,
) -> None:
    digest = "b" * 64
    for seed in (1, 2):
        reservation = reserve_trial_candidate(
            tmp_path,
            candidate_id="candidate-a",
            scope="scope-a",
            candidate_integrity_sha256=digest,
            trial_input={"seed": seed},
            max_attempts=2,
        )
        assert reservation.attempt == seed

    with pytest.raises(TrialScopeExhausted):
        reserve_trial_candidate(
            tmp_path,
            candidate_id="candidate-a",
            scope="scope-a",
            candidate_integrity_sha256=digest,
            trial_input={"seed": 3},
            max_attempts=2,
        )

    other_scope = reserve_trial_candidate(
        tmp_path,
        candidate_id="candidate-a",
        scope="scope-b",
        candidate_integrity_sha256=digest,
        trial_input={"seed": 3},
        max_attempts=2,
    )
    assert other_scope.attempt == 1


def test_completion_replay_requires_exact_outcome(tmp_path: Path) -> None:
    reservation = reserve_trial_candidate(
        tmp_path,
        candidate_id="candidate-a",
        scope="scope-a",
        candidate_integrity_sha256="c" * 64,
        trial_input={"seed": 1},
        max_attempts=1,
    )
    first = complete_trial_candidate(
        tmp_path,
        candidate_id="candidate-a",
        scope="scope-a",
        trial_key=reservation.trial_key,
        outcome={"decision_ref": "regression/one.json", "adopt_candidate": False},
    )
    replay = complete_trial_candidate(
        tmp_path,
        candidate_id="candidate-a",
        scope="scope-a",
        trial_key=reservation.trial_key,
        outcome={"decision_ref": "regression/one.json", "adopt_candidate": False},
    )
    assert first["state"] == "COMPLETED"
    assert replay["outcome_sha256"] == first["outcome_sha256"]

    with pytest.raises(TrialLedgerError):
        complete_trial_candidate(
            tmp_path,
            candidate_id="candidate-a",
            scope="scope-a",
            trial_key=reservation.trial_key,
            outcome={"decision_ref": "regression/two.json", "adopt_candidate": True},
        )


def test_existing_scope_cannot_silently_change_budget(tmp_path: Path) -> None:
    args = dict(
        root=tmp_path,
        candidate_id="candidate-a",
        scope="scope-a",
        candidate_integrity_sha256="d" * 64,
        trial_input={"seed": 1},
    )
    reserve_trial_candidate(**args, max_attempts=2)
    with pytest.raises(TrialLedgerError):
        reserve_trial_candidate(**args, max_attempts=3)
