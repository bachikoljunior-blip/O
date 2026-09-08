from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from agi.context_routing_experiment import (
    build_context_routing_selection_packet,
    context_routing_protocol_digest,
    load_context_routing_experiment,
    measure_context_routing_experiment,
    validate_context_routing_experiment,
    validate_context_routing_selection_plan,
)


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_PATH = ROOT / "agi" / "CONTEXT_ROUTING_EXPERIMENT.json"


def _experiment() -> dict:
    return json.loads(EXPERIMENT_PATH.read_text(encoding="utf-8"))


def _plan(value: dict) -> dict:
    cases = []
    for case in value["cases"]:
        selected_by_parent: dict[str, list[str]] = {}
        for path in case["required_paths"]:
            for parent, child in zip(path, path[1:]):
                children = selected_by_parent.setdefault(parent, [])
                if child not in children:
                    children.append(child)
        cases.append(
            {
                "case_id": case["case_id"],
                "selections": [
                    {"skill_id": parent, "selected_child_ids": children}
                    for parent, children in selected_by_parent.items()
                ],
            }
        )
    return {
        "schema_version": 1,
        "protocol_digest": context_routing_protocol_digest(value),
        "cases": cases,
    }


def _selection(plan: dict, case_id: str, skill_id: str) -> dict:
    case = next(item for item in plan["cases"] if item["case_id"] == case_id)
    return next(
        item for item in case["selections"] if item["skill_id"] == skill_id
    )


def _contains_key(value: object, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


def test_checked_in_harness_is_frozen_valid_and_unmeasured() -> None:
    value = load_context_routing_experiment(EXPERIMENT_PATH)

    assert value["status"] == "HARNESS_READY"
    assert len(value["cases"]) == 6
    assert value["observations"] == []
    assert value["decision"]["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert value["decision"]["scoped_use_authorized"] is False
    assert value["decision"]["global_activation_authorized"] is False
    assert value["user_level_verdict"] == "FAIL"
    assert value["claim_boundary"]["agi_claim_supported"] is False


def test_public_selection_packet_excludes_full_content_and_hidden_labels() -> None:
    value = load_context_routing_experiment(EXPERIMENT_PATH)
    packet = build_context_routing_selection_packet(value)

    assert packet["protocol_digest"] == context_routing_protocol_digest(value)
    assert not _contains_key(packet, "content")
    assert not _contains_key(packet, "content_sha256")
    assert not _contains_key(packet, "required_paths")
    assert not _contains_key(packet, "forbidden_skill_ids")
    assert not _contains_key(packet, "eager_context_chars")
    assert all(set(skill) == {"skill_id", "children"} for skill in packet["skills"])


def test_protocol_digest_binds_hidden_labels_and_eager_baseline_only_before_measurement() -> None:
    value = _experiment()
    original = context_routing_protocol_digest(value)

    relabeled = deepcopy(value)
    relabeled["cases"][0]["required_paths"][0][-1] = "controls"
    assert context_routing_protocol_digest(relabeled) != original
    baseline_changed = deepcopy(value)
    baseline_changed["cases"][0]["eager_context_chars"] += 1
    assert context_routing_protocol_digest(baseline_changed) != original
    observations_changed = deepcopy(value)
    observations_changed["decision"]["reason"] = "not part of the frozen digest"
    assert context_routing_protocol_digest(observations_changed) == original


def test_hidden_labels_must_partition_every_reachable_branch() -> None:
    value = _experiment()
    value["cases"][0]["forbidden_skill_ids"].pop()
    with pytest.raises(ValueError, match="partition reachable"):
        validate_context_routing_experiment(value)

    value = _experiment()
    value["cases"][0]["required_paths"][0] = ["root", "research", "metrics"]
    with pytest.raises(ValueError, match="non-exposed edge"):
        validate_context_routing_experiment(value)


def test_content_digest_and_eager_baseline_are_immutable_bindings() -> None:
    value = _experiment()
    value["skill_graph"][0]["content"] += "changed"
    with pytest.raises(ValueError, match="does not bind"):
        validate_context_routing_experiment(value)

    value = _experiment()
    value["cases"][0]["eager_context_chars"] += 1
    with pytest.raises(ValueError, match="not bound"):
        validate_context_routing_experiment(value)


def test_correct_frozen_plan_meets_every_mechanical_threshold(tmp_path: Path) -> None:
    value = load_context_routing_experiment(EXPERIMENT_PATH)
    measured = measure_context_routing_experiment(
        value,
        _plan(value),
        fixture_root=tmp_path / "fixture",
    )

    assert measured["status"] == "MEASURED"
    assert measured["decision"]["verdict"] == "ADOPT_FOR_SCOPED_WORK"
    assert measured["decision"]["scoped_use_authorized"] is True
    assert measured["decision"]["global_activation_authorized"] is False
    assert measured["user_level_verdict"] == "FAIL"
    assert all(item["required_branch_recall"] == 1.0 for item in measured["observations"])
    assert all(
        item["forbidden_full_content_read_skill_ids"] == []
        for item in measured["observations"]
    )
    assert all(item["deterministic_replay_verified"] for item in measured["observations"])
    assert all(
        item["manifest_and_content_binding_verified"]
        for item in measured["observations"]
    )
    assert max(item["maximum_depth"] for item in measured["observations"]) >= 3
    assert any(item["multi_child_selected"] for item in measured["observations"])
    assert sum(item["routed_context_chars"] for item in measured["observations"]) <= (
        0.5 * sum(item["eager_context_chars"] for item in measured["observations"])
    )
    for item in measured["observations"]:
        assert len(item["replays"]) == 2
        assert item["replays"][0]["replay_digest"] == item["replays"][1]["replay_digest"]


def test_missing_required_branch_is_retained_as_rejection_evidence(tmp_path: Path) -> None:
    value = load_context_routing_experiment(EXPERIMENT_PATH)
    plan = _plan(value)
    selection = _selection(plan, "precommit-routing-evaluation", "experiment")
    selection["selected_child_ids"].remove("metrics")

    measured = measure_context_routing_experiment(
        value,
        plan,
        fixture_root=tmp_path / "fixture",
    )
    observation = next(
        item
        for item in measured["observations"]
        if item["case_id"] == "precommit-routing-evaluation"
    )

    assert measured["decision"]["verdict"] == "REJECT_ROUTING"
    assert measured["decision"]["scoped_use_authorized"] is False
    assert observation["required_branch_recall"] < 1.0
    assert ["experiment", "metrics"] in observation["missing_required_edges"]


def test_forbidden_full_content_read_fails_closed_and_is_named(tmp_path: Path) -> None:
    value = load_context_routing_experiment(EXPERIMENT_PATH)
    plan = _plan(value)
    selection = _selection(plan, "router-regression-suite", "root")
    selection["selected_child_ids"].append("marketing")

    measured = measure_context_routing_experiment(
        value,
        plan,
        fixture_root=tmp_path / "fixture",
    )
    observation = next(
        item
        for item in measured["observations"]
        if item["case_id"] == "router-regression-suite"
    )

    assert measured["decision"]["verdict"] == "REJECT_ROUTING"
    assert observation["forbidden_full_content_read_skill_ids"] == ["marketing"]
    assert "marketing" in observation["replays"][0]["content_read_skill_ids"]


def test_selection_plan_must_bind_protocol_and_exposed_edges() -> None:
    value = load_context_routing_experiment(EXPERIMENT_PATH)
    plan = _plan(value)
    plan["protocol_digest"] = "0" * 64
    with pytest.raises(ValueError, match="does not bind"):
        validate_context_routing_selection_plan(value, plan)

    plan = _plan(value)
    _selection(plan, "safe-effect-dispatch", "root")["selected_child_ids"].append(
        "rollback"
    )
    with pytest.raises(ValueError, match="not exposed"):
        validate_context_routing_selection_plan(value, plan)


def test_global_activation_and_user_level_success_cannot_be_claimed() -> None:
    value = _experiment()
    value["decision"]["global_activation_authorized"] = True
    with pytest.raises(ValueError, match="global activation"):
        validate_context_routing_experiment(value)

    value = _experiment()
    value["user_level_verdict"] = "PASS"
    with pytest.raises(ValueError, match="must remain FAIL"):
        validate_context_routing_experiment(value)
