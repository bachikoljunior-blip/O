from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from continual.candidate_regression import record_candidate_regression
from continual.trial_integration import apply_runtime_candidate_trial_ledger
from continual.trial_ledger import TrialLedgerError, TrialScopeExhausted


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


def _measurements(target_score: float) -> list[dict]:
    return [
        _measurement("target", 0, target_score),
        _measurement("target", 1, target_score),
        _measurement("protected", 0, 0.9),
    ]


def _regression_candidate(root: Path) -> Path:
    candidate_dir = root / ".continual" / "candidates" / "candidate-a"
    candidate_dir.mkdir(parents=True)
    _write(
        candidate_dir / "candidate.json",
        {
            "candidate_id": "candidate-a",
            "target_component": "execute",
            "expected_scope": "symbolic-transfer",
            "what_it_changes": "bounded deterministic symbolic execution",
            "status": "candidate",
            "scope_states": {},
            "supporting_evidence": [],
            "contradictory_evidence": [],
        },
    )
    return candidate_dir


def _scope_key(scope: str) -> str:
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()[:16]


def test_real_regression_path_replays_persistently_and_exhausts_distinct_trials(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    candidate_dir = _regression_candidate(root)
    baseline = tmp_path / "baseline.json"
    first_candidate = tmp_path / "candidate-first.json"
    second_candidate = tmp_path / "candidate-second.json"
    third_candidate = tmp_path / "candidate-third.json"
    _write(baseline, _measurements(0.4))
    _write(first_candidate, _measurements(0.7))
    _write(second_candidate, _measurements(0.75))
    _write(third_candidate, _measurements(0.8))

    first = record_candidate_regression(
        root,
        candidate_id="candidate-a",
        scope="symbolic-transfer",
        baseline_path=baseline,
        candidate_path=first_candidate,
        target_task_ids=["target"],
        max_trial_attempts=2,
    )
    replay_after_restart = record_candidate_regression(
        root,
        candidate_id="candidate-a",
        scope="symbolic-transfer",
        baseline_path=baseline,
        candidate_path=first_candidate,
        target_task_ids=["target"],
        max_trial_attempts=2,
    )
    second = record_candidate_regression(
        root,
        candidate_id="candidate-a",
        scope="symbolic-transfer",
        baseline_path=baseline,
        candidate_path=second_candidate,
        target_task_ids=["target"],
        max_trial_attempts=2,
    )

    assert first["decision"]["adopt_candidate"] is True
    assert first["trial"]["attempt"] == 1
    assert first["trial"]["replayed"] is False
    assert first["trial"]["state"] == "COMPLETED"
    assert replay_after_restart["trial"]["trial_key"] == first["trial"]["trial_key"]
    assert replay_after_restart["trial"]["attempt"] == 1
    assert replay_after_restart["trial"]["replayed"] is True
    assert second["trial"]["attempt"] == 2

    with pytest.raises(TrialScopeExhausted):
        record_candidate_regression(
            root,
            candidate_id="candidate-a",
            scope="symbolic-transfer",
            baseline_path=baseline,
            candidate_path=third_candidate,
            target_task_ids=["target"],
            max_trial_attempts=2,
        )

    ledger = json.loads(
        (
            candidate_dir
            / "trials"
            / f"{_scope_key('symbolic-transfer')}.json"
        ).read_text()
    )
    assert len(ledger["attempts"]) == 2
    assert [item["state"] for item in ledger["attempts"]] == ["COMPLETED", "COMPLETED"]
    state = json.loads((candidate_dir / "candidate.json").read_text())
    assert state["scope_states"]["symbolic-transfer"] == "VERIFIED_FOR_SCOPE"


def _runtime_candidate(root: Path) -> Path:
    candidate_dir = root / ".continual" / "candidates" / "candidate-runtime"
    candidate_dir.mkdir(parents=True)
    (candidate_dir / "prompt.md").write_text("Use the scoped strategy.\n", encoding="utf-8")
    _write(
        candidate_dir / "candidate.json",
        {
            "candidate_id": "candidate-runtime",
            "target_component": "execute",
            "expected_scope": "runtime-scope",
            "prompt_path": ".continual/candidates/candidate-runtime/prompt.md",
            "prompt_mode": "overlay",
            "max_trial_attempts": 1,
            "status": "candidate",
            "scope_states": {},
            "supporting_evidence": [],
            "contradictory_evidence": [],
        },
    )
    return candidate_dir


def test_live_model_trial_path_is_bounded_idempotent_and_never_promotes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    candidate_dir = _runtime_candidate(root)
    pre_payload = {
        "mode": "pre-application",
        "run_id": "run-one",
        "target_component": "execute",
        "execution_unit": {"unit_id": "unit-one", "scope": "runtime-scope", "goal": "trial"},
    }
    pre_output = {
        "result": {
            "decision": "TRIAL_CANDIDATE",
            "candidate_id": "candidate-runtime",
            "scope": "runtime-scope",
        }
    }
    first = apply_runtime_candidate_trial_ledger(
        root,
        component="candidate_evaluate",
        payload=pre_payload,
        output=pre_output,
    )
    replay = apply_runtime_candidate_trial_ledger(
        root,
        component="candidate_evaluate",
        payload=pre_payload,
        output=pre_output,
    )
    reservation = first["result"]["trial_reservation"]
    assert reservation["attempt"] == 1
    assert reservation["replayed"] is False
    assert replay["result"]["trial_reservation"]["trial_key"] == reservation["trial_key"]
    assert replay["result"]["trial_reservation"]["replayed"] is True

    with pytest.raises(TrialScopeExhausted):
        apply_runtime_candidate_trial_ledger(
            root,
            component="candidate_evaluate",
            payload={**pre_payload, "run_id": "run-two"},
            output=pre_output,
        )

    post_payload = {
        "mode": "post-result",
        "run_id": "run-one",
        "target_component": "execute",
        "execution_unit": pre_payload["execution_unit"],
        "pre_application": first["result"],
        "candidate": {"candidate_id": "candidate-runtime"},
        "actual_result": {"status": "completed", "score": 0.7},
    }
    post_output = {
        "result": {
            "decision": "PROPOSE_ACTIVATION",
            "candidate_id": "candidate-runtime",
            "scope": "runtime-scope",
            "evidence": ["observed result only"],
        }
    }
    completed = apply_runtime_candidate_trial_ledger(
        root,
        component="candidate_evaluate",
        payload=post_payload,
        output=post_output,
    )
    completion_replay = apply_runtime_candidate_trial_ledger(
        root,
        component="candidate_evaluate",
        payload=post_payload,
        output=post_output,
    )
    assert completed["result"]["trial_completion"]["state"] == "COMPLETED"
    assert completion_replay["result"]["trial_completion"]["outcome_sha256"] == completed[
        "result"
    ]["trial_completion"]["outcome_sha256"]

    with pytest.raises(TrialLedgerError):
        apply_runtime_candidate_trial_ledger(
            root,
            component="candidate_evaluate",
            payload={**post_payload, "actual_result": {"status": "completed", "score": 0.8}},
            output=post_output,
        )

    state = json.loads((candidate_dir / "candidate.json").read_text())
    assert state["status"] == "candidate"
    assert state["scope_states"] == {}
    assert "verified_scope_states" not in state
