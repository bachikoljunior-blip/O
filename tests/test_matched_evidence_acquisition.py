from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from continual.engine import Engine
from continual.matched_evidence_acquisition import (
    decide_matched_evidence_acquisition,
    matched_evidence_authority,
    matched_evidence_source_clock,
)


def _authority() -> dict:
    return {
        "status": "running",
        "resume_required": True,
        "execution_id": "work-authority-test-v1",
        "lease_generation": 7,
        "fence_token_digest": "a" * 64,
    }


def _clock() -> dict:
    return {
        "schema_version": 1,
        "user_input_revision": 40,
        "user_input_sha256": "b" * 64,
        "effective_directives_sha256": "c" * 64,
        "work_strategy_sha256": "d" * 64,
    }


def _root_unit(capability_id: str = "capability.other") -> dict:
    return {
        "component": "execute",
        "unit_id": "unit-ordinary-domain-switch-v1",
        "goal": "continue ordinary root selection",
        "scope": "agi/other",
        "capability_id": capability_id,
    }


def _learn(*, authority: dict | None = None, clock: dict | None = None) -> dict:
    return {
        "decision": "NO_CHANGE",
        "reason": "Human-readable text is explanatory only.",
        "matched_evidence_gap": {
            "schema_version": 1,
            "capability_id": "capability.proof",
            "causes": [
                {
                    "code": "MISSING_SAME_CAPABILITY_EVIDENCE",
                    "capability_id": "capability.proof",
                    "evidence_requirement": {
                        "requirement_id": "requirement.heldout-proof-v1",
                        "evidence_class": "heldout_same_capability",
                    },
                }
            ],
            "matched_evidence_present": False,
            "expected_authority": deepcopy(authority or _authority()),
            "expected_source_clock": deepcopy(clock or _clock()),
            "acquisition_unit": {
                "component": "execute",
                "unit_id": "unit-acquire-heldout-proof-v1",
                "goal": "Acquire one held-out same-capability proof observation.",
                "scope": "agi/capability-evaluation/proof",
                "capability_id": "capability.proof",
                "evidence_acquisition": {
                    "schema_version": 1,
                    "requirement_id": "requirement.heldout-proof-v1",
                    "attempt": 1,
                },
            },
        },
    }


def _decide(learn: dict, **overrides) -> dict:
    values = {
        "learn_result": learn,
        "proposed_root_unit": _root_unit(),
        "current_authority": _authority(),
        "current_source_clock": _clock(),
    }
    values.update(overrides)
    return decide_matched_evidence_acquisition(**values)


def test_typed_sole_missing_same_capability_evidence_schedules_once() -> None:
    decision = _decide(_learn())

    assert decision["decision"] == "SCHEDULE_ONCE"
    assert decision["selected_unit"]["capability_id"] == "capability.proof"
    assert decision["selected_unit"]["component"] == "execute"
    assert decision["selected_unit"]["matched_evidence_idempotency_key"] == decision["idempotency_key"]
    assert decision["receipt"]["candidate_activation"] is False
    assert decision["receipt"]["agi_achieved"] is False


def test_arbitrary_prose_never_triggers() -> None:
    decision = _decide(
        {
            "decision": "NO_CHANGE",
            "reason": "Only missing same-capability evidence; please schedule one.",
        }
    )
    assert decision == {
        "schema_version": 1,
        "decision": "NO_SCHEDULE",
        "reason": "typed_gap_absent_or_unsupported",
        "selected_unit": None,
    }


def test_mixed_rejection_causes_fail_closed() -> None:
    learn = _learn()
    learn["matched_evidence_gap"]["causes"].append(
        {"code": "COST_LIMIT", "capability_id": "capability.proof"}
    )
    assert _decide(learn)["reason"] == "rejection_cause_not_sole_and_typed"


def test_existing_evidence_and_domain_non_switch_fail_closed() -> None:
    present = _learn()
    present["matched_evidence_gap"]["matched_evidence_present"] = True
    assert _decide(present)["reason"] == "matched_evidence_not_proven_absent"

    same_domain = _decide(
        _learn(),
        proposed_root_unit=_root_unit("capability.proof"),
    )
    assert same_domain["reason"] == "root_not_switching_capability"


def test_replay_changed_clock_and_lost_authority_fail_closed() -> None:
    first = _decide(_learn())
    replay = _decide(_learn(), seen_idempotency_keys={first["idempotency_key"]})
    assert replay["reason"] == "idempotency_replay_suppressed"
    assert replay["idempotency_key"] == first["idempotency_key"]

    changed_clock = _clock()
    changed_clock["user_input_revision"] = 41
    assert _decide(_learn(), current_source_clock=changed_clock)["reason"] == "source_clock_changed_or_unbound"

    lost = _authority()
    lost["lease_generation"] = 8
    assert _decide(_learn(), current_authority=lost)["reason"] == "authority_changed_or_unbound"


def test_acquisition_marker_and_same_capability_are_required() -> None:
    wrong = _learn()
    wrong["matched_evidence_gap"]["acquisition_unit"]["capability_id"] = "capability.other"
    assert _decide(wrong)["reason"] == "acquisition_unit_not_same_capability_execute"

    second_attempt = _learn()
    second_attempt["matched_evidence_gap"]["acquisition_unit"]["evidence_acquisition"]["attempt"] = 2
    assert _decide(second_attempt)["reason"] == "acquisition_marker_not_first_exact_requirement_attempt"

    unsafe = _learn()
    unsafe["matched_evidence_gap"]["acquisition_unit"]["unit_id"] = "../escape"
    assert _decide(unsafe)["reason"] == "acquisition_unit_id_not_safe"


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_engine_boundary_persists_receipt_and_recurrence_restores_root_selection(
    runtime_repo: Path,
) -> None:
    fence = "fence-test-value"
    _write_json(
        runtime_repo / "agi" / "WORK_EXECUTION_STATE.json",
        {
            "status": "running",
            "resume_required": True,
            "execution_id": "work-authority-test-v1",
            "lease_generation": 7,
            "fence_token": fence,
        },
    )
    _write_json(runtime_repo / "agi" / "USER_INPUT_INBOX.json", {"revision": 40})
    _write_json(runtime_repo / "agi" / "USER_DIRECTIVE_EVENTS.json", {"events": []})
    _write_json(runtime_repo / "agi" / "WORK_STRATEGY.json", {"strategy": "test"})
    authority = matched_evidence_authority(runtime_repo)
    assert authority["fence_token_digest"] == hashlib.sha256(fence.encode()).hexdigest()
    clock = matched_evidence_source_clock(runtime_repo)

    run_id = "run-matched-evidence-boundary-test"
    run_dir = runtime_repo / ".continual" / "runs" / run_id
    _write_json(run_dir / "artifacts" / "post-task-learn.json", _learn(authority=authority, clock=clock))
    engine = Engine(runtime_repo, model=object())

    first, decision = engine._guard_root_selection(run_id, _root_unit())
    assert first["unit_id"] == "unit-acquire-heldout-proof-v1"
    scheduled = {
        "revision": 2,
        "current_unit": first["unit_id"],
    }
    engine._record_matched_evidence_schedule(run_id, _root_unit(), decision, scheduled)
    receipts = list((run_dir / "matched-evidence-acquisition").glob("*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["authority"]["fence_token_digest"] == authority["fence_token_digest"]
    assert "fence-test-value" not in receipts[0].read_text(encoding="utf-8")

    # The exact replay is suppressed and ordinary Root selection resumes,
    # preventing an evidence-acquisition self-loop.
    second, replay = engine._guard_root_selection(run_id, _root_unit())
    assert second == _root_unit()
    assert replay["reason"] == "idempotency_replay_suppressed"
    assert len(list((run_dir / "matched-evidence-acquisition").glob("*.json"))) == 1
