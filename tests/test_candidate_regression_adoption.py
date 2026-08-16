from __future__ import annotations

import json
from pathlib import Path

from continual.candidate_regression import record_candidate_regression


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _measurement(task: str, repeat: int, score: float, *, passed: bool = True) -> dict:
    return {
        "task_id": task,
        "criterion": "transfer",
        "domain": "symbolic",
        "repeat_index": repeat,
        "passed": passed,
        "score": score,
        "artifact_sha256": f"{repeat + 1:02x}" * 32,
    }


def _candidate_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    candidate_dir = root / ".continual" / "candidates" / "candidate-a"
    candidate_dir.mkdir(parents=True)
    _write(
        candidate_dir / "candidate.json",
        {
            "candidate_id": "candidate-a",
            "target_component": "execute",
            "status": "candidate",
            "scope_states": {},
            "supporting_evidence": [],
            "contradictory_evidence": [],
        },
    )
    return root


def test_passing_regression_promotes_only_the_measured_scope(tmp_path: Path) -> None:
    root = _candidate_root(tmp_path)
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    _write(
        baseline,
        [
            _measurement("target", 0, 0.4),
            _measurement("target", 1, 0.4),
            _measurement("protected", 0, 0.8),
        ],
    )
    _write(
        candidate,
        [
            _measurement("target", 0, 0.7),
            _measurement("target", 1, 0.7),
            _measurement("protected", 0, 0.8),
        ],
    )

    report = record_candidate_regression(
        root,
        candidate_id="candidate-a",
        scope="symbolic-transfer",
        baseline_path=baseline,
        candidate_path=candidate,
        target_task_ids=["target"],
    )

    assert report["decision"]["adopt_candidate"] is True
    state = json.loads(
        (root / ".continual" / "candidates" / "candidate-a" / "candidate.json").read_text()
    )
    assert state["status"] == "active-for-scope"
    assert state["scope_states"] == {"symbolic-transfer": "VERIFIED_FOR_SCOPE"}
    assert set(state["verified_scope_states"]) == {"symbolic-transfer"}
    assert state["verified_scope_states"]["symbolic-transfer"]["decision_ref"] == report["decision_ref"]
    decision_path = root / report["decision_ref"]
    assert decision_path.is_file()
    persisted = json.loads(decision_path.read_text())
    assert persisted["decision"]["regression_evidence_sha256"] == report["decision"][
        "regression_evidence_sha256"
    ]


def test_failed_regression_remains_candidate_and_negative_evidence_survives_later_success(
    tmp_path: Path,
) -> None:
    root = _candidate_root(tmp_path)
    baseline = tmp_path / "baseline.json"
    failed = tmp_path / "failed.json"
    passed = tmp_path / "passed.json"
    _write(
        baseline,
        [
            _measurement("target", 0, 0.4),
            _measurement("target", 1, 0.4),
            _measurement("protected", 0, 0.9),
        ],
    )
    _write(
        failed,
        [
            _measurement("target", 0, 0.8),
            _measurement("target", 1, 0.8),
            _measurement("protected", 0, 0.2, passed=False),
        ],
    )
    first = record_candidate_regression(
        root,
        candidate_id="candidate-a",
        scope="symbolic-transfer",
        baseline_path=baseline,
        candidate_path=failed,
        target_task_ids=["target"],
    )
    assert first["decision"]["adopt_candidate"] is False

    state_path = root / ".continual" / "candidates" / "candidate-a" / "candidate.json"
    after_failure = json.loads(state_path.read_text())
    assert after_failure["status"] == "candidate"
    assert after_failure["scope_states"]["symbolic-transfer"] == "REMAIN_CANDIDATE"
    assert len(after_failure["contradictory_evidence"]) == 1
    failed_ref = first["decision_ref"]

    _write(
        passed,
        [
            _measurement("target", 0, 0.8),
            _measurement("target", 1, 0.8),
            _measurement("protected", 0, 0.9),
        ],
    )
    second = record_candidate_regression(
        root,
        candidate_id="candidate-a",
        scope="symbolic-transfer",
        baseline_path=baseline,
        candidate_path=passed,
        target_task_ids=["target"],
    )
    assert second["decision"]["adopt_candidate"] is True

    final = json.loads(state_path.read_text())
    assert final["status"] == "active-for-scope"
    assert final["scope_states"]["symbolic-transfer"] == "VERIFIED_FOR_SCOPE"
    assert len(final["contradictory_evidence"]) == 1
    assert final["contradictory_evidence"][0]["decision_ref"] == failed_ref
    assert final["regression_decision_refs"] == [failed_ref, second["decision_ref"]]
    assert (root / failed_ref).is_file()


def test_candidate_id_cannot_escape_candidate_store(tmp_path: Path) -> None:
    root = _candidate_root(tmp_path)
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    values = [_measurement("target", 0, 0.4), _measurement("target", 1, 0.4)]
    _write(baseline, values)
    _write(candidate, values)

    try:
        record_candidate_regression(
            root,
            candidate_id="../escape",
            scope="scope",
            baseline_path=baseline,
            candidate_path=candidate,
            target_task_ids=["target"],
        )
    except ValueError as exc:
        assert "candidate_id" in str(exc)
    else:
        raise AssertionError("path traversal candidate_id was accepted")
