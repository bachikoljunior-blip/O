from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from continual.paired_route_isolation import PairedRouteIsolationError
from continual.revision22_paired_route_inputs import (
    EXPECTED_INVOCATION,
    EXPECTED_ROUTE_IDS,
    EXPECTED_SCENARIO_IDS,
    PUBLIC_INPUTS_REF,
    load_revision22_public_inputs,
    prepare_revision22_paired_route_isolation,
)


ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-08-25T17:10:00Z"


def _commitments() -> dict[str, dict]:
    return {
        scenario_id: {
            "judge_kind": "exact_canonical_json",
            "judge_version": "exact-canonical-json-v1",
            "commitment_digest": f"{index + 1:x}" * 64,
            "success_threshold": 1.0,
        }
        for index, scenario_id in enumerate(EXPECTED_SCENARIO_IDS)
    }


def _state() -> dict:
    return {
        "status": "running",
        "owner_kind": "work_recovery_automation",
        "execution_id": "work-recovery-generation-15-test",
        "lease_generation": 15,
        "fence_token": "opaque-generation-15-test-fence",
        "heartbeat_at": NOW,
        "stale_after_seconds": 900,
        "user_input_inbox": {"highest_acknowledged_revision": 24},
    }


def _copy_public_inputs(tmp_path: Path) -> None:
    source = ROOT / PUBLIC_INPUTS_REF
    target = tmp_path / PUBLIC_INPUTS_REF
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    value = json.loads(source.read_text(encoding="utf-8"))
    for route in value["routes"]:
        route_source = ROOT / route["context_ref"]
        route_target = tmp_path / route["context_ref"]
        route_target.parent.mkdir(parents=True, exist_ok=True)
        route_target.write_bytes(route_source.read_bytes())


def test_public_inputs_bind_exact_frozen_unit_without_results_or_reveals(
    tmp_path: Path,
) -> None:
    _copy_public_inputs(tmp_path)
    inputs = load_revision22_public_inputs(
        tmp_path, rubric_commitments=_commitments()
    )

    assert [route["route_id"] for route in inputs["routes"]] == list(
        EXPECTED_ROUTE_IDS
    )
    assert [scenario["scenario_id"] for scenario in inputs["scenarios"]] == list(
        EXPECTED_SCENARIO_IDS
    )
    assert all(len(route["context_digest"]) == 64 for route in inputs["routes"])
    rendered = json.dumps(json.loads((tmp_path / PUBLIC_INPUTS_REF).read_text()))
    for forbidden in (
        "expected_answer",
        "nonce",
        "observations",
        "scores",
        "route_output",
        "judgment",
    ):
        assert forbidden not in rendered
    assert EXPECTED_INVOCATION in rendered


def test_prepare_derives_six_bound_children_and_no_result(tmp_path: Path) -> None:
    _copy_public_inputs(tmp_path)
    record = prepare_revision22_paired_route_isolation(
        tmp_path,
        run_id="run-revision22-heldout-test",
        state=_state(),
        rubric_commitments=_commitments(),
        now=NOW,
    )

    assert record["status"] == "PRECOMMITTED_AWAITING_ISOLATED_CHILDREN"
    assert len(record["routes"]) == 2
    assert len(record["scenarios"]) == 3
    assert record["finalization_requirements"]["required_child_count"] == 6
    assert "observations" not in record
    assert "scores" not in record


def test_missing_commitment_or_route_tamper_fails_closed(tmp_path: Path) -> None:
    _copy_public_inputs(tmp_path)
    missing = _commitments()
    missing.pop(EXPECTED_SCENARIO_IDS[-1])
    with pytest.raises(PairedRouteIsolationError, match="all and only three"):
        load_revision22_public_inputs(tmp_path, rubric_commitments=missing)

    route_path = (
        tmp_path
        / "agi/evaluations/revision22_action_adherence/routes/current-context-kernel.json"
    )
    route = json.loads(route_path.read_text(encoding="utf-8"))
    route["mechanism"] = "tampered-route"
    route_path.write_text(json.dumps(route), encoding="utf-8")
    with pytest.raises(PairedRouteIsolationError, match="digest mismatch"):
        load_revision22_public_inputs(
            tmp_path, rubric_commitments=_commitments()
        )


def test_judge_binding_mismatch_fails_closed(tmp_path: Path) -> None:
    _copy_public_inputs(tmp_path)
    commitments = deepcopy(_commitments())
    commitments[EXPECTED_SCENARIO_IDS[0]]["judge_version"] = "other-judge-v1"
    with pytest.raises(PairedRouteIsolationError, match="judge binding mismatch"):
        load_revision22_public_inputs(
            tmp_path, rubric_commitments=commitments
        )
