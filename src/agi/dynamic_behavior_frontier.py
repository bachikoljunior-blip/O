from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import AcquiredProgramError, ProgramExample
from agi.adaptive_depth_composition import (
    AdaptiveDepthCompositionError,
    _bounded_chain_layers,
    _execute_descriptor_chain,
    synthesize_shallowest_verified_route,
)
from agi.durable_state_rehydration import _tree_snapshot
from agi.dynamic_skillgraph_input_domains import (
    _behavior_record,
    _domain_challenge_inputs,
    _domain_planning_inputs,
    _execute_dynamic_target,
    _observed_generator_input_domains,
)
from agi.heterogeneous_retention_campaign import _digest
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.verified_macro_library_discovery import discover_verified_acquired_program_ids
from agi.verified_macro_synthesis import _load_verified_macro_items


class DynamicBehaviorFrontierError(RuntimeError):
    pass


def _public_target(index: int, item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "target_index": index,
        "input_domain": str(item["input_domain"]),
        "output_domain": str(item["output_domain"]),
        "support_examples": [
            {"input": value, "output": output}
            for value, output in zip(
                item["support_inputs"], item["support_outputs"], strict=True
            )
        ],
        "behavior_fingerprint": str(item["behavior_fingerprint"]),
        "generator_hidden_candidate_ids": list(item["candidate_ids"]),
        "generator_hidden_depth": int(item["depth"]),
        "unique_at_minimal_behavior_depth": True,
    }


def _runtime_admissible_behavior_frontier(
    root: Path,
    seed: str,
    *,
    max_generation_depth: int = 3,
    max_generation_search_nodes: int = 8192,
) -> dict[str, Any]:
    """Enumerate unique multistage behavior semantics from the observed verified graph.

    No fixed source-domain or input/output-pair quota determines which semantics enter the frontier.
    Every candidate behavior comes from the current durable verified skill graph, is nonconstant, is
    unique at its minimal behavior depth, and must execute a commitment-derived challenge shape under
    each program's already-declared deterministic runtime budget. Runtime limits are never widened.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    library_ids = discover_verified_acquired_program_ids(root, max_candidates=192)
    if not library_ids:
        raise DynamicBehaviorFrontierError("verified skill graph is empty")
    items = tuple(_load_verified_macro_items(root, library_ids))
    input_domains = _observed_generator_input_domains(items)
    if len(input_domains) < 2:
        raise DynamicBehaviorFrontierError(
            "verified graph exposed fewer than two probeable input domains"
        )

    layers, search_nodes = _bounded_chain_layers(
        items,
        start_domains=set(input_domains),
        max_depth=max_generation_depth,
        max_search_nodes=max_generation_search_nodes,
    )
    support_inputs = {
        domain: _domain_planning_inputs(domain, f"{seed}:frontier-support")
        for domain in input_domains
    }

    by_semantics: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    runtime_shape_rejections: list[dict[str, Any]] = []
    for depth, chains in sorted(layers.items()):
        if depth < 2:
            continue
        for chain in chains:
            input_domain = str(chain[0]["input_domain"])
            if input_domain not in support_inputs:
                continue
            try:
                record = _behavior_record(chain, support_inputs=support_inputs)
            except Exception:
                continue
            if not bool(record["nonconstant"]):
                continue
            viability_commitment = _digest(
                {
                    "seed": seed,
                    "input_domain": record["input_domain"],
                    "output_domain": record["output_domain"],
                    "behavior_fingerprint": record["behavior_fingerprint"],
                    "candidate_ids": list(record["candidate_ids"]),
                    "phase": "dynamic-behavior-frontier-runtime-shape-v1",
                }
            )
            try:
                for value in _domain_challenge_inputs(input_domain, viability_commitment):
                    _execute_descriptor_chain(chain, value)
            except AcquiredProgramError as exc:
                runtime_shape_rejections.append(
                    {
                        "input_domain": input_domain,
                        "output_domain": str(record["output_domain"]),
                        "candidate_ids": list(record["candidate_ids"]),
                        "behavior_fingerprint": str(record["behavior_fingerprint"]),
                        "failure": str(exc),
                    }
                )
                continue
            by_semantics[
                (
                    str(record["input_domain"]),
                    str(record["output_domain"]),
                    str(record["behavior_fingerprint"]),
                )
            ].append(record)

    frontier: list[dict[str, Any]] = []
    for semantic_records in by_semantics.values():
        minimal_depth = min(int(item["depth"]) for item in semantic_records)
        minimal = [item for item in semantic_records if int(item["depth"]) == minimal_depth]
        if minimal_depth < 2 or len(minimal) != 1:
            continue
        selected = dict(minimal[0])
        selected["unique_at_minimal_behavior_depth"] = True
        frontier.append(selected)
    if len(frontier) < 3:
        raise DynamicBehaviorFrontierError(
            "runtime-admissible unique multistage behavior frontier has fewer than three semantics"
        )

    ordered = sorted(
        frontier,
        key=lambda item: _digest(
            {
                "seed": seed,
                "behavior_fingerprint": item["behavior_fingerprint"],
                "candidate_ids": list(item["candidate_ids"]),
                "phase": "dynamic-behavior-frontier-order-v1",
            }
        ),
    )
    registry = [
        {
            "input_domain": str(item["input_domain"]),
            "output_domain": str(item["output_domain"]),
            "depth": int(item["depth"]),
            "behavior_fingerprint": str(item["behavior_fingerprint"]),
        }
        for item in ordered
    ]
    return {
        "library_candidate_ids": list(library_ids),
        "observed_generator_input_domains": list(input_domains),
        "frontier": ordered,
        "frontier_registry": registry,
        "frontier_registry_digest": _digest(
            {
                "seed": seed,
                "registry": registry,
                "phase": "dynamic-behavior-frontier-registry-v1",
            }
        ),
        "frontier_behavior_count": len(ordered),
        "generation_search_nodes": search_nodes,
        "generation_search_node_budget": max_generation_search_nodes,
        "runtime_shape_rejections": runtime_shape_rejections,
        "runtime_budget_unchanged": True,
        "heldback_challenge_shape_unchanged": True,
        "fixed_domain_or_pair_quota_used": False,
    }


def _subset_is_diverse(items: Sequence[Mapping[str, Any]]) -> bool:
    input_domains = {str(item["input_domain"]) for item in items}
    output_domains = {str(item["output_domain"]) for item in items}
    return (
        len(input_domains) >= 2
        and len(output_domains) >= 2
        and any(
            str(item["input_domain"]) != str(item["output_domain"])
            for item in items
        )
    )


def _precommit_frontier_target_plan(
    root: Path,
    seed: str,
    *,
    target_count: int = 4,
    max_subset_attempts: int = 1024,
) -> dict[str, Any]:
    """Choose a bounded diverse subset from the full behavior frontier before solver execution."""

    if target_count < 3:
        raise ValueError("target_count must be at least 3")
    if max_subset_attempts < 1:
        raise ValueError("max_subset_attempts must be positive")
    frontier_state = _runtime_admissible_behavior_frontier(root, seed)
    frontier = list(frontier_state["frontier"])
    choose = min(target_count, len(frontier))
    if choose < 3:
        raise DynamicBehaviorFrontierError("frontier cannot supply three targets")

    candidate_subsets: list[tuple[dict[str, Any], ...]] = []
    for index, subset in enumerate(combinations(frontier, choose)):
        if index >= max_subset_attempts:
            break
        if _subset_is_diverse(subset):
            candidate_subsets.append(tuple(dict(item) for item in subset))
    if not candidate_subsets:
        raise DynamicBehaviorFrontierError(
            "bounded behavior frontier exposed no diverse target subset"
        )
    candidate_subsets.sort(
        key=lambda subset: _digest(
            {
                "seed": seed,
                "frontier_registry_digest": frontier_state["frontier_registry_digest"],
                "behaviors": [item["behavior_fingerprint"] for item in subset],
                "phase": "dynamic-behavior-frontier-subset-rank-v1",
            }
        )
    )

    exact_runtime_rejections: list[dict[str, Any]] = []
    for subset in candidate_subsets:
        targets = [_public_target(index, item) for index, item in enumerate(subset)]
        commitment = _digest(
            {
                "seed": seed,
                "frontier_registry_digest": frontier_state["frontier_registry_digest"],
                "targets": targets,
                "phase": "dynamic-behavior-frontier-plan-v1",
            }
        )
        failed = False
        for target in targets:
            target_commitment = _digest(
                {
                    "plan_commitment": commitment,
                    "target_index": int(target["target_index"]),
                    "behavior_fingerprint": target["behavior_fingerprint"],
                    "phase": "dynamic-skillgraph-target-instance-v1",
                }
            )
            hidden_items = tuple(
                _load_verified_macro_items(
                    root,
                    [str(value) for value in target["generator_hidden_candidate_ids"]],
                )
            )
            try:
                for value in _domain_challenge_inputs(
                    str(target["input_domain"]), target_commitment
                ):
                    _execute_descriptor_chain(hidden_items, value)
            except AcquiredProgramError as exc:
                exact_runtime_rejections.append(
                    {
                        "input_domain": str(target["input_domain"]),
                        "output_domain": str(target["output_domain"]),
                        "behavior_fingerprint": str(target["behavior_fingerprint"]),
                        "candidate_ids": list(target["generator_hidden_candidate_ids"]),
                        "failure": str(exc),
                    }
                )
                failed = True
                break
        if failed:
            continue
        return {
            **{key: value for key, value in frontier_state.items() if key != "frontier"},
            "target_plan": targets,
            "target_plan_commitment": commitment,
            "target_plan_precommitted_before_solver_execution": True,
            "selected_target_count": len(targets),
            "selected_distinct_input_domains": len(
                {str(item["input_domain"]) for item in targets}
            ),
            "selected_distinct_output_domains": len(
                {str(item["output_domain"]) for item in targets}
            ),
            "selected_cross_domain_behavior": any(
                str(item["input_domain"]) != str(item["output_domain"])
                for item in targets
            ),
            "exact_runtime_rejections": exact_runtime_rejections,
        }

    raise DynamicBehaviorFrontierError(
        "bounded target selection exhausted exact runtime-admissible frontier subsets"
    )


def _mutate_output(value: Any, domain: str) -> Any:
    if domain == "numeric":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise DynamicBehaviorFrontierError("numeric counterfactual received non-numeric value")
        return value + 1
    if domain == "string":
        return f"{value}|counterfactual"
    if domain == "sequence":
        if not isinstance(value, (list, tuple)):
            raise DynamicBehaviorFrontierError("sequence counterfactual received non-sequence value")
        items = list(value)
        if len(items) > 1:
            mutated = list(reversed(items))
            if mutated != items:
                return mutated
        return [*items, 0]
    if domain == "boolean":
        return not bool(value)
    if domain == "object":
        if not isinstance(value, dict):
            raise DynamicBehaviorFrontierError("object counterfactual received non-object value")
        mutated = dict(value)
        mutated["__counterfactual__"] = 0
        return mutated
    raise DynamicBehaviorFrontierError(f"unsupported counterfactual output domain: {domain}")


def run_dynamic_behavior_frontier(
    root: Path,
    seed: str,
    *,
    target_count: int = 4,
) -> dict[str, Any]:
    """Transfer a precommitted diverse sample from the observed multistage behavior frontier.

    Target selection is performed over the full runtime-admissible unique behavior registry instead of
    choosing one target per hard-coded domain or type pair. The only selection constraints are generic
    diversity and a fixed resource bound. Generator Candidate IDs remain hidden, every selected behavior
    must be rediscovered from examples and types, held-back challenges run through fresh Engines, and
    source Candidate/trial state stays immutable. An inconsistent counterfactual built from a selected
    behavior is precommitted as a domain-agnostic fail-closed control.

    This is bounded repository-authored internal development evidence. It does not establish open-domain
    task generation, independent production evaluation, or AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    source_ids = _all_candidate_ids(root)
    source_tree = _tree_snapshot(root, source_ids)
    source_trials = _trial_snapshot(root, source_ids)
    if not source_ids or not all(source_trials.get(candidate_id) for candidate_id in source_ids):
        raise DynamicBehaviorFrontierError(
            "behavior-frontier source graph lacks durable Candidate trial evidence"
        )

    plan = _precommit_frontier_target_plan(root, seed, target_count=target_count)
    records = [
        _execute_dynamic_target(
            root,
            target,
            plan_commitment=str(plan["target_plan_commitment"]),
            source_ids=source_ids,
            source_tree=source_tree,
            source_trials=source_trials,
        )
        for target in plan["target_plan"]
    ]
    if _tree_snapshot(root, source_ids) != source_tree:
        raise DynamicBehaviorFrontierError("frontier campaign changed source Candidate bytes")
    if _trial_snapshot(root, source_ids) != source_trials:
        raise DynamicBehaviorFrontierError("frontier campaign changed source Candidate trials")

    control_target = plan["target_plan"][0]
    support = list(control_target["support_examples"])
    if not support:
        raise DynamicBehaviorFrontierError("selected control target has no support examples")
    original = support[0]
    mutated_output = _mutate_output(original["output"], str(control_target["output_domain"]))
    if mutated_output == original["output"]:
        raise DynamicBehaviorFrontierError("counterfactual output mutation was ineffective")
    contradictory_examples = (
        ProgramExample(original["input"], original["output"]),
        ProgramExample(original["input"], mutated_output),
    )
    control_commitment = _digest(
        {
            "frontier_plan_commitment": plan["target_plan_commitment"],
            "input_domain": control_target["input_domain"],
            "output_domain": control_target["output_domain"],
            "input": original["input"],
            "outputs": [original["output"], mutated_output],
            "phase": "dynamic-behavior-frontier-counterfactual-control-v1",
        }
    )
    control_failure: str | None = None
    try:
        synthesize_shallowest_verified_route(
            root,
            input_domain=str(control_target["input_domain"]),
            output_domain=str(control_target["output_domain"]),
            examples=contradictory_examples,
            max_depth=4,
            max_candidates=192,
            max_search_nodes=8192,
            max_behavior_evaluations=4096,
        )
    except (AdaptiveDepthCompositionError, ValueError) as exc:
        control_failure = f"{type(exc).__name__}: {exc}"
    if control_failure is None:
        raise DynamicBehaviorFrontierError(
            "inconsistent counterfactual control unexpectedly became supported"
        )
    if _tree_snapshot(root, source_ids) != source_tree:
        raise DynamicBehaviorFrontierError("counterfactual control changed Candidate bytes")
    if _trial_snapshot(root, source_ids) != source_trials:
        raise DynamicBehaviorFrontierError("counterfactual control changed Candidate trials")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "dynamic-behavior-frontier-v1",
        "seed": seed,
        "frontier_registry_digest": str(plan["frontier_registry_digest"]),
        "frontier_behavior_count": int(plan["frontier_behavior_count"]),
        "frontier_registry": list(plan["frontier_registry"]),
        "fixed_domain_or_pair_quota_used": bool(plan["fixed_domain_or_pair_quota_used"]),
        "target_plan_precommitted_before_solver_execution": bool(
            plan["target_plan_precommitted_before_solver_execution"]
        ),
        "target_plan_commitment": str(plan["target_plan_commitment"]),
        "selected_target_count": int(plan["selected_target_count"]),
        "selected_distinct_input_domains": int(plan["selected_distinct_input_domains"]),
        "selected_distinct_output_domains": int(plan["selected_distinct_output_domains"]),
        "selected_cross_domain_behavior": bool(plan["selected_cross_domain_behavior"]),
        "runtime_shape_rejections": list(plan["runtime_shape_rejections"]),
        "exact_runtime_rejections": list(plan["exact_runtime_rejections"]),
        "runtime_budget_unchanged": bool(plan["runtime_budget_unchanged"]),
        "heldback_challenge_shape_unchanged": bool(plan["heldback_challenge_shape_unchanged"]),
        "target_records": records,
        "all_generator_hidden_ids_withheld": all(
            record["generator_hidden_candidate_ids_supplied_to_solver"] is False
            for record in records
        ),
        "all_solver_calls_candidate_id_free": all(
            record["solver_candidate_ids_supplied_by_caller"] is False for record in records
        ),
        "all_generated_routes_multistage": all(
            int(record["solver_selected_chain_depth"]) >= 2 for record in records
        ),
        "all_fresh_engine_runs_unique": all(
            bool(record["fresh_engine_runs_unique"]) for record in records
        ),
        "all_candidate_state_unchanged": all(
            bool(record["candidate_state_unchanged"]) for record in records
        ),
        "all_trial_state_unchanged": all(
            bool(record["trial_state_unchanged"]) for record in records
        ),
        "source_candidate_state_unchanged": True,
        "source_trial_state_unchanged": True,
        "counterfactual_control": {
            "commitment": control_commitment,
            "input_domain": str(control_target["input_domain"]),
            "output_domain": str(control_target["output_domain"]),
            "failed_closed": True,
            "failure": control_failure,
        },
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded behavior-frontier evidence only. Target selection is drawn from the full "
            "observed runtime-admissible unique multistage behavior registry without a fixed per-domain "
            "or per-type-pair quota; hidden generator IDs are withheld and fresh Engines replay held-back "
            "semantics while Candidate bytes and trial ledgers remain unchanged. The graph, behavior "
            "enumeration, resource bounds, solver, control, scoring, and evaluator remain repository-"
            "authored; this does not establish open-domain task generation, independent production "
            "evaluation, or AGI."
        ),
    }
    if not all(
        (
            report["frontier_behavior_count"] >= report["selected_target_count"] >= 3,
            report["fixed_domain_or_pair_quota_used"] is False,
            report["target_plan_precommitted_before_solver_execution"],
            report["selected_distinct_input_domains"] >= 2,
            report["selected_distinct_output_domains"] >= 2,
            report["selected_cross_domain_behavior"],
            report["runtime_budget_unchanged"],
            report["heldback_challenge_shape_unchanged"],
            report["all_generator_hidden_ids_withheld"],
            report["all_solver_calls_candidate_id_free"],
            report["all_generated_routes_multistage"],
            report["all_fresh_engine_runs_unique"],
            report["all_candidate_state_unchanged"],
            report["all_trial_state_unchanged"],
            report["source_candidate_state_unchanged"],
            report["source_trial_state_unchanged"],
            report["counterfactual_control"]["failed_closed"],
        )
    ):
        raise DynamicBehaviorFrontierError("behavior-frontier aggregate invariant failed")

    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "dynamic-behavior-frontier"
        / f"frontier-{_digest(seed)[:16]}.json",
        report,
    )
    return report
