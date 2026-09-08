from __future__ import annotations

import json
from pathlib import Path

import pytest

import agi.materialized_candidate_validation as validation


def _write_candidate(
    root: Path,
    candidate_id: str,
    *,
    status: str,
    verified_scopes: tuple[str, ...] = (),
) -> None:
    path = root / ".continual" / "candidates" / candidate_id / "candidate.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "candidate_id": candidate_id,
                "status": status,
                "verified_scope_states": {
                    scope: {"state": "VERIFIED_FOR_SCOPE"} for scope in verified_scopes
                },
            }
        ),
        encoding="utf-8",
    )


def test_materialized_validator_requires_candidate_state(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="no Candidate state"):
        validation.validate_materialized_candidates(tmp_path)


def test_materialized_validator_accepts_verified_and_retains_inactive_negative_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_candidate(
        tmp_path,
        "verified-candidate",
        status="active-for-scope",
        verified_scopes=("scope/a", "scope/b"),
    )
    _write_candidate(tmp_path, "rejected-candidate", status="candidate")
    calls: list[tuple[str, str]] = []

    def fake_verified(root: Path, candidate, scope: str) -> bool:
        calls.append((str(candidate["candidate_id"]), scope))
        return True

    monkeypatch.setattr(validation, "candidate_verified_for_scope", fake_verified)
    report = validation.validate_materialized_candidates(tmp_path)

    assert report["passed"] is True
    assert report["candidate_count"] == 2
    assert report["verified_binding_count"] == 2
    assert report["inactive_candidate_ids"] == ["rejected-candidate"]
    assert calls == [
        ("verified-candidate", "scope/a"),
        ("verified-candidate", "scope/b"),
    ]


def test_materialized_validator_fails_closed_on_broken_verified_binding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_candidate(
        tmp_path,
        "broken-candidate",
        status="active-for-scope",
        verified_scopes=("scope/a",),
    )
    monkeypatch.setattr(validation, "candidate_verified_for_scope", lambda *_: False)

    with pytest.raises(RuntimeError, match="not independently verified"):
        validation.validate_materialized_candidates(tmp_path)


def test_materialized_validator_rejects_active_candidate_without_verified_scope(tmp_path: Path) -> None:
    _write_candidate(tmp_path, "unbound-active", status="active-for-scope")

    with pytest.raises(RuntimeError, match="active Candidate has no verified scopes"):
        validation.validate_materialized_candidates(tmp_path)
