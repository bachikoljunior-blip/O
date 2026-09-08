from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from continual.paired_route_isolation import (
    CLAIM_SCOPE,
    PairedRouteIsolationError,
    compute_rubric_commitment_digest,
    finalize_paired_route_comparison,
    paired_route_child_binding,
    prepare_paired_route_isolation,
    record_paired_route_child_response,
    verify_paired_route_precommit,
)
from continual.store import Store


RUN_ID = "run-paired-route-isolation"
NOW = "2026-08-25T13:10:00Z"
ROUTE_IDS = ("current-context-kernel", "manifest-free-control")
SCENARIO_IDS = ("inventory-sort", "policy-choice", "state-transition")


def _state(*, heartbeat: str = NOW) -> dict:
    return {
        "status": "running",
        "owner_kind": "work_recovery_automation",
        "execution_id": "work-recovery-gen13-test",
        "lease_generation": 13,
        "fence_token": "opaque-fence-token-not-a-secret",
        "heartbeat_at": heartbeat,
        "stale_after_seconds": 900,
        "user_input_inbox": {"highest_acknowledged_revision": 23},
    }


def _successor_state(*, heartbeat: str) -> dict:
    value = _state(heartbeat=heartbeat)
    value["execution_id"] = "work-recovery-gen14-test"
    value["lease_generation"] = 14
    value["fence_token"] = "opaque-successor-fence-token"
    return value


def _routes() -> list[dict]:
    return [
        {
            "route_id": ROUTE_IDS[0],
            "context_ref": "fixtures/routes/current-context-kernel.json",
            "context_digest": "a" * 64,
        },
        {
            "route_id": ROUTE_IDS[1],
            "context_ref": "fixtures/routes/manifest-free-control.json",
            "context_digest": "b" * 64,
        },
    ]


def _scenarios() -> list[dict]:
    specifications = [
        (
            SCENARIO_IDS[0],
            "Return sorted SKUs and their integer quantity total.",
            {"rows": [{"sku": "b", "quantity": 2}, {"sku": "a", "quantity": 3}]},
            {"skus": ["a", "b"], "total": 5},
        ),
        (
            SCENARIO_IDS[1],
            "Select the permitted action under the supplied public policy.",
            {"policy": ["read is allowed", "delete is denied"], "request": "read"},
            {"decision": "allow", "action": "read"},
        ),
        (
            SCENARIO_IDS[2],
            "Apply the public transition once and return canonical state.",
            {"state": {"count": 4}, "event": {"increment": 2}},
            {"state": {"count": 6}},
        ),
    ]
    scenarios = []
    for index, (scenario_id, instruction, input_value, answer) in enumerate(
        specifications
    ):
        commitment = compute_rubric_commitment_digest(
            scenario_id=scenario_id,
            expected_answer=answer,
            nonce=f"sealed-rubric-nonce-{index}-" + "x" * 40,
        )
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "instruction": instruction,
                "input": input_value,
                "answer_format": "canonical_json",
                "response_pointer": ["result", "behavioral_answer"],
                "rubric_commitment": {
                    "judge_kind": "exact_canonical_json",
                    "judge_version": "exact-canonical-json-v1",
                    "commitment_digest": commitment,
                    "success_threshold": 1.0,
                },
            }
        )
    return scenarios


def _order() -> list[dict]:
    return [
        {"scenario_id": SCENARIO_IDS[0], "route_ids": list(ROUTE_IDS)},
        {"scenario_id": SCENARIO_IDS[1], "route_ids": list(reversed(ROUTE_IDS))},
        {"scenario_id": SCENARIO_IDS[2], "route_ids": list(ROUTE_IDS)},
    ]


def _prepare(
    root: Path,
    *,
    state: dict | None = None,
    routes: list[dict] | None = None,
    scenarios: list[dict] | None = None,
    order: list[dict] | None = None,
    now: str = NOW,
) -> dict:
    return prepare_paired_route_isolation(
        root,
        run_id=RUN_ID,
        state=state or _state(),
        routes=routes or _routes(),
        scenarios=scenarios or _scenarios(),
        execution_order=order or _order(),
        shared_budget={
            "max_response_bytes": 4096,
            "max_model_calls": 1,
            "max_tool_calls": 0,
            "timeout_seconds": 180,
        },
        tool_permissions=["public_fixture_read"],
        executor_class="isolated_child_executor",
        model_class="same_frozen_model_class",
        now=now,
    )


def _record_path(root: Path, record: dict) -> Path:
    return (
        root
        / ".continual"
        / "runs"
        / RUN_ID
        / "paired-route-isolation"
        / record["comparison_id"]
        / "precommit.json"
    )


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for child in value.values() for key in _keys(child)}
    if isinstance(value, list):
        return {key for child in value for key in _keys(child)}
    return set()


def test_precommit_is_idempotent_and_derives_six_isolated_bindings(
    tmp_path: Path,
) -> None:
    first = _prepare(tmp_path)
    path = _record_path(tmp_path, first)
    before = path.read_bytes()
    later = "2026-08-25T13:11:00Z"
    replay = _prepare(tmp_path, state=_state(heartbeat=later), now=later)

    assert replay == first
    assert path.read_bytes() == before
    assert first["status"] == "PRECOMMITTED_AWAITING_ISOLATED_CHILDREN"
    assert first["claim_scope"] == CLAIM_SCOPE
    assert first["finalization_requirements"]["required_child_count"] == 6

    verified = verify_paired_route_precommit(
        tmp_path,
        run_id=RUN_ID,
        comparison_id=first["comparison_id"],
    )
    bindings = verified["child_bindings"]
    assert len(bindings) == 6
    assert len({binding["binding_digest"] for binding in bindings}) == 6
    assert verified["observations"] == []
    assert verified["scores"] == []
    assert verified["comparison_ready"] is False
    assert all(
        binding["shared_budget"] == first["shared_budget"] for binding in bindings
    )
    assert {tuple(binding["tool_permissions"]) for binding in bindings} == {
        ("public_fixture_read",)
    }
    assert {binding["executor_class"] for binding in bindings} == {
        "isolated_child_executor"
    }
    assert {binding["model_class"] for binding in bindings} == {
        "same_frozen_model_class"
    }
    assert set(path.parent.iterdir()) == {path}


def test_each_child_binding_reveals_only_its_route_and_scenario(tmp_path: Path) -> None:
    record = _prepare(tmp_path)
    forbidden_result_keys = {
        "expected_answer",
        "nonce",
        "observation",
        "observations",
        "route_output",
        "score",
        "scores",
        "judgment",
        "conclusion",
    }

    for scenario_id in SCENARIO_IDS:
        for route_id in ROUTE_IDS:
            binding = paired_route_child_binding(
                tmp_path,
                run_id=RUN_ID,
                comparison_id=record["comparison_id"],
                scenario_id=scenario_id,
                route_id=route_id,
            )
            rendered = json.dumps(binding, sort_keys=True)
            other_route = next(value for value in ROUTE_IDS if value != route_id)
            other_scenarios = [value for value in SCENARIO_IDS if value != scenario_id]
            assert binding["route"]["route_id"] == route_id
            assert binding["scenario"]["scenario_id"] == scenario_id
            assert other_route not in rendered
            assert all(value not in rendered for value in other_scenarios)
            assert forbidden_result_keys.isdisjoint(_keys(binding))


@pytest.mark.parametrize("mutation", ["frozen_route", "extra_field"])
def test_rehashed_tampering_still_fails_closed(tmp_path: Path, mutation: str) -> None:
    record = _prepare(tmp_path)
    path = _record_path(tmp_path, record)
    value = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "frozen_route":
        value["routes"][0]["context_digest"] = "c" * 64
        match = "frozen identity"
    else:
        value["unexpected"] = True
        match = "unexpected schema"
    body = deepcopy(value)
    body.pop("precommit_digest")
    value["precommit_digest"] = Store(tmp_path).stable_digest(body, length=64)
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(PairedRouteIsolationError, match=match):
        verify_paired_route_precommit(
            tmp_path,
            run_id=RUN_ID,
            comparison_id=record["comparison_id"],
        )


def test_stale_authority_duplicate_context_unbalanced_order_and_secret_fail_closed(
    tmp_path: Path,
) -> None:
    stale = _state(
        heartbeat=(datetime.fromisoformat(NOW.replace("Z", "+00:00")) - timedelta(hours=1))
        .astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )
    with pytest.raises(PairedRouteIsolationError, match="stale"):
        _prepare(tmp_path / "stale", state=stale)

    duplicate = _routes()
    duplicate[1]["context_ref"] = duplicate[0]["context_ref"]
    with pytest.raises(PairedRouteIsolationError, match="refs must be distinct"):
        _prepare(tmp_path / "duplicate", routes=duplicate)

    unbalanced = _order()
    for row in unbalanced:
        row["route_ids"] = list(ROUTE_IDS)
    with pytest.raises(PairedRouteIsolationError, match="position balanced"):
        _prepare(tmp_path / "unbalanced", order=unbalanced)

    secret = _scenarios()
    secret[0]["input"] = {"authorization": "Bearer abcdefghijklmnopqrstuvwxyz"}
    with pytest.raises(PairedRouteIsolationError, match="forbidden private field"):
        _prepare(tmp_path / "secret", scenarios=secret)

    for root in ("stale", "duplicate", "unbalanced", "secret"):
        assert not (tmp_path / root / ".continual").exists()


def test_commitment_requires_a_strong_nonce_and_canonical_public_answer() -> None:
    with pytest.raises(PairedRouteIsolationError, match="at least 32 bytes"):
        compute_rubric_commitment_digest(
            scenario_id="short-nonce-scenario",
            expected_answer={"answer": 1},
            nonce="too-short",
        )
    with pytest.raises(PairedRouteIsolationError, match="forbidden private field"):
        compute_rubric_commitment_digest(
            scenario_id="private-answer-scenario",
            expected_answer={"hidden_reasoning": "do not persist this"},
            nonce="x" * 32,
        )


def _reveals() -> dict[str, dict]:
    answers = (
        {"skus": ["a", "b"], "total": 5},
        {"decision": "allow", "action": "read"},
        {"state": {"count": 6}},
    )
    return {
        scenario_id: {
            "expected_answer": answer,
            "nonce": f"sealed-rubric-nonce-{index}-" + "x" * 40,
        }
        for index, (scenario_id, answer) in enumerate(zip(SCENARIO_IDS, answers))
    }


def _record_all(tmp_path: Path, record: dict, *, one_wrong: bool = False) -> None:
    reveals = _reveals()
    for scenario_id in SCENARIO_IDS:
        for route_id in ROUTE_IDS:
            binding = paired_route_child_binding(
                tmp_path,
                run_id=RUN_ID,
                comparison_id=record["comparison_id"],
                scenario_id=scenario_id,
                route_id=route_id,
            )
            answer = deepcopy(reveals[scenario_id]["expected_answer"])
            if one_wrong and scenario_id == SCENARIO_IDS[0] and route_id == ROUTE_IDS[1]:
                answer = {"skus": ["b", "a"], "total": 5}
            record_paired_route_child_response(
                tmp_path,
                run_id=RUN_ID,
                comparison_id=record["comparison_id"],
                scenario_id=scenario_id,
                route_id=route_id,
                binding_digest=binding["binding_digest"],
                response={"result": {"behavioral_answer": answer}},
                state=_state(),
                now=NOW,
            )


def test_six_responses_are_write_once_and_reveal_cannot_run_early(
    tmp_path: Path,
) -> None:
    record = _prepare(tmp_path)
    binding = paired_route_child_binding(
        tmp_path,
        run_id=RUN_ID,
        comparison_id=record["comparison_id"],
        scenario_id=SCENARIO_IDS[0],
        route_id=ROUTE_IDS[0],
    )
    first = record_paired_route_child_response(
        tmp_path,
        run_id=RUN_ID,
        comparison_id=record["comparison_id"],
        scenario_id=SCENARIO_IDS[0],
        route_id=ROUTE_IDS[0],
        binding_digest=binding["binding_digest"],
        response={"result": {"behavioral_answer": {"skus": ["a", "b"], "total": 5}}},
        state=_state(),
        now=NOW,
    )
    replay = record_paired_route_child_response(
        tmp_path,
        run_id=RUN_ID,
        comparison_id=record["comparison_id"],
        scenario_id=SCENARIO_IDS[0],
        route_id=ROUTE_IDS[0],
        binding_digest=binding["binding_digest"],
        response={"result": {"behavioral_answer": {"skus": ["a", "b"], "total": 5}}},
        state=_successor_state(heartbeat="2026-08-25T13:11:00Z"),
        now="2026-08-25T13:11:00Z",
    )
    assert replay == first
    with pytest.raises(PairedRouteIsolationError, match="immutable child response conflict"):
        record_paired_route_child_response(
            tmp_path,
            run_id=RUN_ID,
            comparison_id=record["comparison_id"],
            scenario_id=SCENARIO_IDS[0],
            route_id=ROUTE_IDS[0],
            binding_digest=binding["binding_digest"],
            response={"result": {"behavioral_answer": {"wrong": True}}},
            state=_state(),
            now=NOW,
        )
    with pytest.raises(PairedRouteIsolationError, match="all six immutable"):
        finalize_paired_route_comparison(
            tmp_path,
            run_id=RUN_ID,
            comparison_id=record["comparison_id"],
            rubric_reveals=_reveals(),
            state=_state(),
            now=NOW,
        )


def test_finalization_verifies_reveals_after_all_responses_and_never_persists_them(
    tmp_path: Path,
) -> None:
    record = _prepare(tmp_path)
    _record_all(tmp_path, record, one_wrong=True)
    final = finalize_paired_route_comparison(
        tmp_path,
        run_id=RUN_ID,
        comparison_id=record["comparison_id"],
        rubric_reveals=_reveals(),
        state=_state(),
        now=NOW,
    )
    assert final["status"] == "FINALIZED_DETERMINISTIC_EXACT_JUDGMENTS"
    assert len(final["response_digests"]) == 6
    assert len(final["judgments"]) == 6
    assert [row["passed_count"] for row in final["route_summaries"]] == [3, 2]
    final_path = _record_path(tmp_path, record).parent / "finalization.json"
    persisted = final_path.read_text(encoding="utf-8")
    assert "expected_answer" not in persisted
    assert "sealed-rubric-nonce" not in persisted
    assert "behavioral_answer" not in persisted
    replay = finalize_paired_route_comparison(
        tmp_path,
        run_id=RUN_ID,
        comparison_id=record["comparison_id"],
        rubric_reveals=_reveals(),
        state=_successor_state(heartbeat="2026-08-25T13:11:00Z"),
        now="2026-08-25T13:11:00Z",
    )
    assert replay == final


def test_response_binding_private_fields_and_tampered_reveal_fail_closed(
    tmp_path: Path,
) -> None:
    record = _prepare(tmp_path)
    binding = paired_route_child_binding(
        tmp_path,
        run_id=RUN_ID,
        comparison_id=record["comparison_id"],
        scenario_id=SCENARIO_IDS[0],
        route_id=ROUTE_IDS[0],
    )
    with pytest.raises(PairedRouteIsolationError, match="binding digest mismatch"):
        record_paired_route_child_response(
            tmp_path,
            run_id=RUN_ID,
            comparison_id=record["comparison_id"],
            scenario_id=SCENARIO_IDS[0],
            route_id=ROUTE_IDS[0],
            binding_digest="f" * 64,
            response={"result": {"behavioral_answer": {"ok": True}}},
            state=_state(),
            now=NOW,
        )
    with pytest.raises(PairedRouteIsolationError, match="forbidden private field"):
        record_paired_route_child_response(
            tmp_path,
            run_id=RUN_ID,
            comparison_id=record["comparison_id"],
            scenario_id=SCENARIO_IDS[0],
            route_id=ROUTE_IDS[0],
            binding_digest=binding["binding_digest"],
            response={"result": {"behavioral_answer": {"secret": "no"}}},
            state=_state(),
            now=NOW,
        )
    _record_all(tmp_path, record)
    bad = _reveals()
    bad[SCENARIO_IDS[0]]["expected_answer"] = {"wrong": True}
    with pytest.raises(PairedRouteIsolationError, match="commitment mismatch"):
        finalize_paired_route_comparison(
            tmp_path,
            run_id=RUN_ID,
            comparison_id=record["comparison_id"],
            rubric_reveals=bad,
            state=_state(),
            now=NOW,
        )
