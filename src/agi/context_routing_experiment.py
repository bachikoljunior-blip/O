from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from .context_skill_catalog import RepositorySkillCatalog
from .context_skill_router import (
    ContextRoutingError,
    RecursiveContextRouter,
    _validated_skill_id,
)


VERDICTS = {
    "INSUFFICIENT_EVIDENCE",
    "ADOPT_FOR_SCOPED_WORK",
    "REJECT_ROUTING",
}
_TOP_LEVEL_FIELDS = {
    "schema_version",
    "status",
    "mechanism",
    "frozen_before_routing",
    "skill_graph",
    "cases",
    "budgets",
    "thresholds",
    "safety",
    "observations",
    "decision",
    "user_level_verdict",
    "claim_boundary",
}
_SKILL_FIELDS = {"skill_id", "content", "content_sha256", "children"}
_CHILD_FIELDS = {"skill_id", "summary"}
_CASE_FIELDS = {
    "case_id",
    "situation",
    "root_skill_id",
    "required_paths",
    "forbidden_skill_ids",
    "eager_context_chars",
}


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value


def _positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def context_routing_protocol_digest(value: Mapping[str, Any]) -> str:
    """Bind every pre-routing input while excluding observations and decision."""

    frozen = {
        field: value.get(field)
        for field in (
            "schema_version",
            "mechanism",
            "frozen_before_routing",
            "skill_graph",
            "cases",
            "budgets",
            "thresholds",
            "safety",
            "user_level_verdict",
            "claim_boundary",
        )
    }
    return _canonical_digest(frozen)


def _validated_skill_graph(value: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, set[str]]]:
    raw_skills = value.get("skill_graph")
    if not isinstance(raw_skills, list) or len(raw_skills) < 12:
        raise ValueError("skill_graph must contain at least twelve frozen Skills")
    skills: dict[str, Any] = {}
    edges: dict[str, set[str]] = {}
    for index, raw in enumerate(raw_skills):
        label = f"skill_graph[{index}]"
        if not isinstance(raw, Mapping) or set(raw) != _SKILL_FIELDS:
            raise ValueError(f"{label} has an unexpected schema")
        skill_id = _validated_skill_id(raw.get("skill_id"), f"{label}.skill_id")
        if skill_id in skills:
            raise ValueError("skill IDs must be unique")
        content = _nonempty(raw.get("content"), f"{label}.content")
        expected_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if raw.get("content_sha256") != expected_digest:
            raise ValueError(f"{label}.content_sha256 does not bind its content")
        raw_children = raw.get("children")
        if not isinstance(raw_children, list):
            raise ValueError(f"{label}.children must be an array")
        child_ids: set[str] = set()
        for child_index, child in enumerate(raw_children):
            child_label = f"{label}.children[{child_index}]"
            if not isinstance(child, Mapping) or set(child) != _CHILD_FIELDS:
                raise ValueError(f"{child_label} has an unexpected schema")
            child_id = _validated_skill_id(
                child.get("skill_id"), f"{child_label}.skill_id"
            )
            _nonempty(child.get("summary"), f"{child_label}.summary")
            if child_id in child_ids:
                raise ValueError("one Skill cannot expose a duplicate child")
            child_ids.add(child_id)
        skills[skill_id] = deepcopy(dict(raw))
        edges[skill_id] = child_ids
    for skill_id, children in edges.items():
        missing = sorted(children - skills.keys())
        if missing:
            raise ValueError(f"Skill {skill_id!r} exposes unknown children: {missing}")
    return skills, edges


def _reachable(root: str, edges: Mapping[str, set[str]]) -> set[str]:
    pending = [root]
    reached: set[str] = set()
    while pending:
        skill_id = pending.pop()
        if skill_id in reached:
            continue
        reached.add(skill_id)
        pending.extend(sorted(edges[skill_id] - reached))
    return reached


def _required_edges(paths: Sequence[Sequence[str]]) -> set[tuple[str, str]]:
    return {
        (str(parent), str(child))
        for path in paths
        for parent, child in zip(path, path[1:])
    }


def validate_context_routing_experiment(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a frozen, synthetic, fail-closed recursive-routing experiment."""

    if set(value) != _TOP_LEVEL_FIELDS:
        raise ValueError("context-routing experiment has an unexpected top-level schema")
    if value.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    if value.get("status") not in {"HARNESS_READY", "MEASURED"}:
        raise ValueError("status must be HARNESS_READY or MEASURED")
    if value.get("mechanism") != "recursive_skill_context_routing_heldout_v1":
        raise ValueError("unexpected context-routing mechanism")
    if value.get("frozen_before_routing") is not True:
        raise ValueError("the protocol must be frozen before routing outputs")

    skills, edges = _validated_skill_graph(value)
    raw_cases = value.get("cases")
    if not isinstance(raw_cases, list) or len(raw_cases) < 6:
        raise ValueError("at least six held-out cases are required")
    case_ids: set[str] = set()
    has_depth_three = False
    has_multi_child = False
    shared_required_parents: dict[str, set[str]] = {}
    for index, case in enumerate(raw_cases):
        label = f"cases[{index}]"
        if not isinstance(case, Mapping) or set(case) != _CASE_FIELDS:
            raise ValueError(f"{label} has an unexpected schema")
        case_id = _nonempty(case.get("case_id"), f"{label}.case_id")
        if case_id in case_ids:
            raise ValueError("case IDs must be unique")
        case_ids.add(case_id)
        situation = case.get("situation")
        if not isinstance(situation, Mapping) or not situation:
            raise ValueError(f"{label}.situation must be a non-empty object")
        root = _nonempty(case.get("root_skill_id"), f"{label}.root_skill_id")
        if root not in skills:
            raise ValueError(f"{label} references an unknown root Skill")
        reachable = _reachable(root, edges)
        raw_paths = case.get("required_paths")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise ValueError(f"{label}.required_paths must be a non-empty array")
        required_ids: set[str] = set()
        case_edges: set[tuple[str, str]] = set()
        selected_by_parent: dict[str, set[str]] = {}
        for path_index, path in enumerate(raw_paths):
            path_label = f"{label}.required_paths[{path_index}]"
            if not isinstance(path, list) or len(path) < 2 or path[0] != root:
                raise ValueError(f"{path_label} must start at root and contain an edge")
            if len(set(path)) != len(path):
                raise ValueError(f"{path_label} cannot contain a cycle")
            for parent, child in zip(path, path[1:]):
                if parent not in skills or child not in edges[parent]:
                    raise ValueError(f"{path_label} contains a non-exposed edge")
                case_edges.add((parent, child))
                selected_by_parent.setdefault(parent, set()).add(child)
                shared_required_parents.setdefault(child, set()).add(parent)
            required_ids.update(path[1:])
            has_depth_three = has_depth_three or len(path) - 1 >= 3
        has_multi_child = has_multi_child or any(
            len(children) >= 2 for children in selected_by_parent.values()
        )
        forbidden = case.get("forbidden_skill_ids")
        if (
            not isinstance(forbidden, list)
            or any(not isinstance(item, str) for item in forbidden)
            or len(forbidden) != len(set(forbidden))
        ):
            raise ValueError(f"{label}.forbidden_skill_ids must be unique strings")
        forbidden_ids = set(forbidden)
        if root in forbidden_ids or required_ids & forbidden_ids:
            raise ValueError("required and forbidden Skill labels must be disjoint")
        if required_ids | forbidden_ids != reachable - {root}:
            raise ValueError("required and forbidden labels must partition reachable branches")
        eager_chars = sum(len(skills[skill_id]["content"]) for skill_id in reachable)
        if case.get("eager_context_chars") != eager_chars:
            raise ValueError(f"{label}.eager_context_chars is not bound to the graph")
        if not case_edges:
            raise ValueError("each case must require at least one branch")
    if not has_depth_three:
        raise ValueError("the frozen cases must include useful depth of at least three")
    if not has_multi_child:
        raise ValueError("the frozen cases must include multi-child selection")
    if not any(len(parents) >= 2 for parents in shared_required_parents.values()):
        raise ValueError("the frozen cases must exercise a shared descendant")

    budgets = value.get("budgets")
    required_budget_fields = {
        "max_depth",
        "max_nodes",
        "max_selected_children",
        "max_context_chars",
    }
    if not isinstance(budgets, Mapping) or set(budgets) != required_budget_fields:
        raise ValueError("budgets have an unexpected schema")
    for field in required_budget_fields:
        _positive_int(budgets.get(field), f"budgets.{field}")

    thresholds = value.get("thresholds")
    expected_thresholds = {
        "required_branch_recall": 1.0,
        "forbidden_full_content_reads": 0,
        "deterministic_replays": 2,
        "minimum_successful_depth": 3,
        "minimum_multichild_cases": 1,
        "maximum_routed_to_eager_ratio": 0.5,
    }
    if not isinstance(thresholds, Mapping) or dict(thresholds) != expected_thresholds:
        raise ValueError("thresholds must equal the frozen fail-closed contract")

    safety = value.get("safety")
    expected_safety = {
        "only_child_summaries_before_selection": True,
        "post_output_relabeling_allowed": False,
        "global_activation_allowed": False,
        "main_writes_allowed": False,
        "external_effects_allowed": False,
        "protected_router_regressions_required": True,
        "manifest_and_content_binding_required": True,
    }
    if not isinstance(safety, Mapping) or dict(safety) != expected_safety:
        raise ValueError("safety boundary must remain fail-closed")

    observations = value.get("observations")
    if not isinstance(observations, list):
        raise ValueError("observations must be an array")
    observed_case_ids: set[str] = set()
    for index, observation in enumerate(observations):
        if not isinstance(observation, Mapping):
            raise ValueError(f"observations[{index}] must be an object")
        case_id = observation.get("case_id")
        if case_id not in case_ids or case_id in observed_case_ids:
            raise ValueError("observations must reference each frozen case at most once")
        observed_case_ids.add(str(case_id))
        if observation.get("protocol_digest") != context_routing_protocol_digest(value):
            raise ValueError("observation protocol digest does not match the frozen harness")
        replays = observation.get("replays")
        if not isinstance(replays, list) or len(replays) != 2:
            raise ValueError("every observation must contain exactly two replays")

    decision = value.get("decision")
    if not isinstance(decision, Mapping) or set(decision) != {
        "verdict",
        "reason",
        "scoped_use_authorized",
        "global_activation_authorized",
    }:
        raise ValueError("decision has an unexpected schema")
    if decision.get("verdict") not in VERDICTS:
        raise ValueError("decision verdict is invalid")
    _nonempty(decision.get("reason"), "decision.reason")
    if decision.get("global_activation_authorized") is not False:
        raise ValueError("the experiment can never authorize global activation")
    complete = len(observed_case_ids) == len(case_ids)
    if decision.get("verdict") != "INSUFFICIENT_EVIDENCE" and not complete:
        raise ValueError("a measured verdict requires every frozen case")
    if decision.get("scoped_use_authorized") is not (
        decision.get("verdict") == "ADOPT_FOR_SCOPED_WORK"
    ):
        raise ValueError("scoped authorization must exactly follow the measured verdict")
    if value.get("status") == "HARNESS_READY" and (observations or complete):
        raise ValueError("HARNESS_READY cannot contain routing observations")
    if value.get("status") == "MEASURED" and not complete:
        raise ValueError("MEASURED requires every frozen case")
    if value.get("user_level_verdict") != "FAIL":
        raise ValueError("the user-level verdict must remain FAIL")
    boundary = value.get("claim_boundary")
    expected_boundary = {
        "agi_claim_supported": False,
        "user_goal_completed": False,
        "harness_is_external_evidence": False,
        "scoped_routing_is_global_activation": False,
    }
    if not isinstance(boundary, Mapping) or dict(boundary) != expected_boundary:
        raise ValueError("claim boundary must remain fail-closed")
    return deepcopy(dict(value))


def build_context_routing_selection_packet(value: Mapping[str, Any]) -> dict[str, Any]:
    """Expose situations and lightweight graph metadata, never labels or full content."""

    validated = validate_context_routing_experiment(value)
    return {
        "schema_version": 1,
        "mechanism": validated["mechanism"],
        "protocol_digest": context_routing_protocol_digest(validated),
        "instruction": (
            "For each case, select zero or more exposed child skill_ids for every "
            "Skill that could be reached. Use only the situation and child summaries; "
            "do not request or infer hidden labels."
        ),
        "budgets": deepcopy(validated["budgets"]),
        "skills": [
            {
                "skill_id": skill["skill_id"],
                "children": deepcopy(skill["children"]),
            }
            for skill in validated["skill_graph"]
        ],
        "cases": [
            {
                "case_id": case["case_id"],
                "situation": deepcopy(case["situation"]),
                "root_skill_id": case["root_skill_id"],
            }
            for case in validated["cases"]
        ],
        "response_schema": {
            "schema_version": 1,
            "protocol_digest": "lowercase SHA-256 copied from this packet",
            "cases": [
                {
                    "case_id": "frozen case ID",
                    "selections": [
                        {
                            "skill_id": "parent Skill ID",
                            "selected_child_ids": ["zero or more exposed child IDs"],
                        }
                    ],
                }
            ],
        },
    }


def validate_context_routing_selection_plan(
    value: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, dict[str, tuple[str, ...]]]:
    validated = validate_context_routing_experiment(value)
    if not isinstance(plan, Mapping) or set(plan) != {
        "schema_version",
        "protocol_digest",
        "cases",
    }:
        raise ValueError("selection plan has an unexpected schema")
    if plan.get("schema_version") != 1:
        raise ValueError("selection plan schema_version must be 1")
    if plan.get("protocol_digest") != context_routing_protocol_digest(validated):
        raise ValueError("selection plan does not bind the frozen protocol")
    raw_cases = plan.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("selection plan cases must be an array")
    expected_cases = {case["case_id"] for case in validated["cases"]}
    graph = {skill["skill_id"]: skill for skill in validated["skill_graph"]}
    parsed: dict[str, dict[str, tuple[str, ...]]] = {}
    for raw_case in raw_cases:
        if not isinstance(raw_case, Mapping) or set(raw_case) != {"case_id", "selections"}:
            raise ValueError("selection plan case has an unexpected schema")
        case_id = raw_case.get("case_id")
        if case_id not in expected_cases or case_id in parsed:
            raise ValueError("selection plan case IDs must exactly match frozen cases")
        raw_selections = raw_case.get("selections")
        if not isinstance(raw_selections, list):
            raise ValueError("selections must be an array")
        selections: dict[str, tuple[str, ...]] = {}
        for raw_selection in raw_selections:
            if not isinstance(raw_selection, Mapping) or set(raw_selection) != {
                "skill_id",
                "selected_child_ids",
            }:
                raise ValueError("selection entry has an unexpected schema")
            skill_id = raw_selection.get("skill_id")
            if skill_id not in graph or skill_id in selections:
                raise ValueError("selection parent IDs must be unique known Skills")
            selected = raw_selection.get("selected_child_ids")
            if (
                not isinstance(selected, list)
                or any(not isinstance(item, str) for item in selected)
                or len(selected) != len(set(selected))
            ):
                raise ValueError("selected_child_ids must be unique strings")
            exposed = {child["skill_id"] for child in graph[str(skill_id)]["children"]}
            if not set(selected) <= exposed:
                raise ValueError("selection plan chose a child not exposed by its parent")
            if len(selected) > validated["budgets"]["max_selected_children"]:
                raise ValueError("selection plan exceeds the frozen fan-out budget")
            selections[str(skill_id)] = tuple(selected)
        parsed[str(case_id)] = selections
    if set(parsed) != expected_cases:
        raise ValueError("selection plan must cover every frozen case")
    return parsed


def _materialize_fixture(root: Path, value: Mapping[str, Any]) -> Path:
    root.mkdir(parents=True, exist_ok=False)
    skills_dir = root / "skills"
    skills_dir.mkdir()
    manifest_skills = []
    for skill in value["skill_graph"]:
        content_path = f"skills/{skill['skill_id']}.md"
        (root / content_path).write_text(skill["content"], encoding="utf-8")
        manifest_skills.append(
            {
                "skill_id": skill["skill_id"],
                "content_path": content_path,
                "content_sha256": skill["content_sha256"],
                "children": deepcopy(skill["children"]),
            }
        )
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {"schema_version": 1, "skills": manifest_skills},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _run_replay(
    fixture_root: Path,
    case: Mapping[str, Any],
    selections: Mapping[str, tuple[str, ...]],
    budgets: Mapping[str, int],
) -> dict[str, Any]:
    reads: list[str] = []

    def reader(path: Path) -> bytes:
        reads.append(path.stem)
        return path.read_bytes()

    catalog = RepositorySkillCatalog(
        fixture_root,
        "manifest.json",
        content_reader=reader,
    )
    router = RecursiveContextRouter(
        catalog.load,
        lambda frame: selections.get(frame.skill.skill_id, ()),
        max_depth=budgets["max_depth"],
        max_nodes=budgets["max_nodes"],
        max_selected_children=budgets["max_selected_children"],
        max_context_chars=budgets["max_context_chars"],
    )
    try:
        result = router.route(case["root_skill_id"], case["situation"])
    except ContextRoutingError as exc:
        replay = {
            "status": "ERROR",
            "error": str(exc),
            "content_read_skill_ids": reads,
            "selected_edges": [],
            "materialized": [],
            "routed_context_chars": 0,
            "maximum_depth": 0,
            "multi_child_selected": False,
            "manifest_binding_verified": False,
        }
        replay["replay_digest"] = _canonical_digest(replay)
        return replay
    selected_edges = sorted(
        [
            [event["skill_id"], child_id]
            for event in result.trace
            if event["event"] == "selected"
            for child_id in event["selected_child_ids"]
        ]
    )
    materialized = [
        {
            "skill_id": item.skill_id,
            "parent_skill_id": item.parent_skill_id,
            "depth": item.depth,
            "path": list(item.path),
            "content_sha256": item.content_sha256,
            "manifest_sha256": item.manifest_sha256,
            "context_chars": len(item.content),
        }
        for item in result.materialized
    ]
    replay = {
        "status": "OK",
        "error": None,
        "content_read_skill_ids": reads,
        "selected_edges": selected_edges,
        "materialized": materialized,
        "routed_context_chars": result.total_context_chars,
        "maximum_depth": max(item.depth for item in result.materialized),
        "multi_child_selected": any(
            event["event"] == "selected" and len(event["selected_child_ids"]) >= 2
            for event in result.trace
        ),
        "manifest_binding_verified": all(
            item.manifest_sha256 == catalog.manifest_sha256
            and item.content_sha256 is not None
            for item in result.materialized
        ),
    }
    replay["replay_digest"] = _canonical_digest(replay)
    return replay


def measure_context_routing_experiment(
    value: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    fixture_root: Path,
) -> dict[str, Any]:
    """Execute two exact replays per frozen case and mechanically score them."""

    validated = validate_context_routing_experiment(value)
    if validated["status"] != "HARNESS_READY":
        raise ValueError("only a HARNESS_READY artifact can be measured")
    selections_by_case = validate_context_routing_selection_plan(validated, plan)
    _materialize_fixture(fixture_root, validated)
    protocol_digest = context_routing_protocol_digest(validated)
    expected_content = {
        skill["skill_id"]: skill["content_sha256"]
        for skill in validated["skill_graph"]
    }
    observations = []
    for case in validated["cases"]:
        replays = [
            _run_replay(
                fixture_root,
                case,
                selections_by_case[case["case_id"]],
                validated["budgets"],
            )
            for _ in range(validated["thresholds"]["deterministic_replays"])
        ]
        required_edges = _required_edges(case["required_paths"])
        observed_edges = {
            tuple(edge) for edge in replays[0]["selected_edges"]
        }
        required_recall = (
            len(required_edges & observed_edges) / len(required_edges)
            if required_edges
            else 1.0
        )
        forbidden_reads = sorted(
            set(replays[0]["content_read_skill_ids"])
            & set(case["forbidden_skill_ids"])
        )
        materialized_digest_binding = all(
            item["content_sha256"] == expected_content[item["skill_id"]]
            for replay in replays
            for item in replay["materialized"]
        )
        replay_verified = replays[0]["replay_digest"] == replays[1]["replay_digest"]
        routed_chars = replays[0]["routed_context_chars"]
        eager_chars = case["eager_context_chars"]
        observations.append(
            {
                "case_id": case["case_id"],
                "protocol_digest": protocol_digest,
                "selection_plan_digest": _canonical_digest(
                    selections_by_case[case["case_id"]]
                ),
                "replays": replays,
                "required_branch_recall": required_recall,
                "missing_required_edges": sorted(
                    [list(edge) for edge in required_edges - observed_edges]
                ),
                "forbidden_full_content_read_skill_ids": forbidden_reads,
                "deterministic_replay_verified": replay_verified,
                "manifest_and_content_binding_verified": (
                    materialized_digest_binding
                    and all(replay["manifest_binding_verified"] for replay in replays)
                ),
                "maximum_depth": replays[0]["maximum_depth"],
                "multi_child_selected": replays[0]["multi_child_selected"],
                "routed_context_chars": routed_chars,
                "eager_context_chars": eager_chars,
                "routed_to_eager_ratio": routed_chars / eager_chars,
            }
        )
    thresholds = validated["thresholds"]
    total_routed = sum(item["routed_context_chars"] for item in observations)
    total_eager = sum(item["eager_context_chars"] for item in observations)
    threshold_failures = []
    if any(item["required_branch_recall"] != 1.0 for item in observations):
        threshold_failures.append("required_branch_recall")
    if any(item["forbidden_full_content_read_skill_ids"] for item in observations):
        threshold_failures.append("forbidden_full_content_reads")
    if any(not item["deterministic_replay_verified"] for item in observations):
        threshold_failures.append("deterministic_replay")
    if any(not item["manifest_and_content_binding_verified"] for item in observations):
        threshold_failures.append("manifest_or_content_binding")
    if max(item["maximum_depth"] for item in observations) < thresholds[
        "minimum_successful_depth"
    ]:
        threshold_failures.append("minimum_successful_depth")
    if sum(item["multi_child_selected"] for item in observations) < thresholds[
        "minimum_multichild_cases"
    ]:
        threshold_failures.append("minimum_multichild_cases")
    if total_routed / total_eager > thresholds["maximum_routed_to_eager_ratio"]:
        threshold_failures.append("maximum_routed_to_eager_ratio")
    if any(replay["status"] != "OK" for item in observations for replay in item["replays"]):
        threshold_failures.append("routing_error")

    measured = deepcopy(validated)
    measured["status"] = "MEASURED"
    measured["observations"] = observations
    if threshold_failures:
        verdict = "REJECT_ROUTING"
        reason = "Frozen thresholds failed: " + ", ".join(sorted(set(threshold_failures)))
    else:
        verdict = "ADOPT_FOR_SCOPED_WORK"
        reason = (
            "All frozen cases retained required branches, avoided forbidden full-content "
            "reads, replayed exactly, preserved integrity, and met the context ratio."
        )
    measured["decision"] = {
        "verdict": verdict,
        "reason": reason,
        "scoped_use_authorized": verdict == "ADOPT_FOR_SCOPED_WORK",
        "global_activation_authorized": False,
    }
    return validate_context_routing_experiment(measured)


def load_context_routing_experiment(path: Path) -> dict[str, Any]:
    return validate_context_routing_experiment(
        json.loads(path.read_text(encoding="utf-8"))
    )
