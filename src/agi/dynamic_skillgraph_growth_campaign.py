from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import AcquiredProgramError, ProgramExample
from agi.adaptive_depth_composition import (
    AdaptiveDepthCompositionError,
    _bounded_chain_layers,
    _execute_descriptor_chain,
    run_adaptive_depth_composition,
    synthesize_shallowest_verified_route,
)
from agi.cross_domain_seed_gap_acquisition import run_cross_domain_seed_gap_acquisition
from agi.durable_state_rehydration import _tree_snapshot
from agi.dynamic_skillgraph_input_domains import (
    DynamicSkillGraphInputDomainError,
    _PROBEABLE_DOMAINS,
    _behavior_record,
    _domain_challenge_inputs,
    _domain_planning_inputs,
    _execute_dynamic_target,
    _observed_generator_input_domains,
)
from agi.heterogeneous_retention_campaign import _digest
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.sequence_seed_gap_acquisition import run_sequence_seed_gap_acquisition
from agi.verified_macro_library_discovery import discover_verified_acquired_program_ids
from agi.verified_macro_synthesis import _load_verified_macro_items


class DynamicSkillGraphGrowthError(RuntimeError):
    pass


def _runtime_safe_target_plan(
    root: Path,
    seed: str,
    *,
    max_generation_depth: int = 3,
    max_generation_search_nodes: int = 4096,
) -> dict[str, Any]:
    """Precommit one multistage target per observed domain that is runtime-admissible.

    Target generation remains bounded and repository-authored. The only new filter is that the
    hidden generator chain must execute the exact held-back challenge shape under each program's
    already-declared deterministic runtime budget. No budget is raised and no held-back challenge
    is shortened or skipped.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    library_ids = discover_verified_acquired_program_ids(root, max_candidates=192)
    if not library_ids:
        raise DynamicSkillGraphGrowthError("verified skill graph is empty")
    items = tuple(_load_verified_macro_items(root, library_ids))
    input_domains = _observed_generator_input_domains(items)
    if len(input_domains) < 2:
        raise DynamicSkillGraphGrowthError(
            "verified skill graph exposed fewer than two probeable input domains"
        )

    layers, search_nodes = _bounded_chain_layers(
        items,
        start_domains=set(input_domains),
        max_depth=max_generation_depth,
        max_search_nodes=max_generation_search_nodes,
    )
    support_inputs = {
        domain: _domain_planning_inputs(domain, f"{seed}:support")
        for domain in input_domains
    }

    by_semantics: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    runtime_rejections: list[dict[str, Any]] = []
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
            viability_commitment = _digest(
                {
                    "seed": seed,
                    "input_domain": input_domain,
                    "output_domain": record["output_domain"],
                    "behavior_fingerprint": record["behavior_fingerprint"],
                    "candidate_ids": list(record["candidate_ids"]),
                    "phase": "dynamic-target-runtime-admissibility-shape-v1",
                }
            )
            try:
                for value in _domain_challenge_inputs(input_domain, viability_commitment):
                    _execute_descriptor_chain(chain, value)
            except AcquiredProgramError as exc:
                runtime_rejections.append(
                    {
                        "input_domain": input_domain,
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

    eligible: list[dict[str, Any]] = []
    for semantic_records in by_semantics.values():
        minimal_depth = min(int(item["depth"]) for item in semantic_records)
        minimal = [item for item in semantic_records if int(item["depth"]) == minimal_depth]
        if minimal_depth < 2 or len(minimal) != 1:
            continue
        selected = dict(minimal[0])
        if not selected["nonconstant"]:
            continue
        selected["unique_at_minimal_behavior_depth"] = True
        eligible.append(selected)

    by_input: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in eligible:
        by_input[str(item["input_domain"])].append(item)
    missing = [domain for domain in input_domains if not by_input.get(domain)]
    if missing:
        raise DynamicSkillGraphGrowthError(
            "observed input domains lack runtime-admissible unique multistage target semantics: "
            + ",".join(missing)
        )

    source_order = sorted(
        input_domains,
        key=lambda domain: _digest(
            {
                "seed": seed,
                "domain": domain,
                "phase": "dynamic-skillgraph-source-order-v1",
            }
        ),
    )
    ranked_by_input: dict[str, list[dict[str, Any]]] = {}
    for domain in source_order:
        ranked_by_input[domain] = sorted(
            by_input[domain],
            key=lambda item: _digest(
                {
                    "seed": seed,
                    "input_domain": domain,
                    "candidate_ids": item["candidate_ids"],
                    "behavior_fingerprint": item["behavior_fingerprint"],
                    "phase": "dynamic-skillgraph-domain-target-rank-v1",
                }
            ),
        )

    offsets = {domain: 0 for domain in source_order}
    exact_runtime_rejections: list[dict[str, Any]] = []
    maximum_selection_attempts = sum(len(ranked_by_input[domain]) for domain in source_order) + 1
    for _ in range(maximum_selection_attempts):
        selected_targets: list[dict[str, Any]] = []
        exhausted = False
        for domain in source_order:
            ranked = ranked_by_input[domain]
            offset = offsets[domain]
            if offset >= len(ranked):
                exhausted = True
                break
            selected_targets.append(dict(ranked[offset]))
        if exhausted:
            break

        public_targets = [
            {
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
            for index, item in enumerate(selected_targets)
        ]
        plan_commitment = _digest(
            {
                "seed": seed,
                "library_candidate_ids": list(library_ids),
                "observed_input_domains": list(input_domains),
                "source_order": source_order,
                "targets": public_targets,
                "phase": "dynamic-skillgraph-target-plan-v1",
            }
        )

        failed_domain: str | None = None
        for target in public_targets:
            target_commitment = _digest(
                {
                    "plan_commitment": plan_commitment,
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
                failed_domain = str(target["input_domain"])
                exact_runtime_rejections.append(
                    {
                        "input_domain": failed_domain,
                        "candidate_ids": list(target["generator_hidden_candidate_ids"]),
                        "behavior_fingerprint": str(target["behavior_fingerprint"]),
                        "failure": str(exc),
                    }
                )
                offsets[failed_domain] += 1
                break
        if failed_domain is not None:
            continue

        return {
            "library_candidate_ids": list(library_ids),
            "observed_generator_input_domains": list(input_domains),
            "precommitted_source_order": source_order,
            "generation_search_nodes": search_nodes,
            "generation_search_node_budget": max_generation_search_nodes,
            "eligible_semantic_target_count": len(eligible),
            "runtime_shape_rejections": runtime_rejections,
            "exact_runtime_rejections": exact_runtime_rejections,
            "target_plan": public_targets,
            "target_plan_commitment": plan_commitment,
            "target_plan_precommitted_before_solver_execution": True,
            "runtime_budget_unchanged": True,
            "heldback_challenge_shape_unchanged": True,
        }

    raise DynamicSkillGraphGrowthError(
        "bounded target selection exhausted runtime-admissible unique multistage targets"
    )


def _run_dynamic_from_existing_graph(root: Path, seed: str) -> dict[str, Any]:
    source_ids = _all_candidate_ids(root)
    source_tree = _tree_snapshot(root, source_ids)
    source_trials = _trial_snapshot(root, source_ids)
    if not source_ids or not all(source_trials.get(candidate_id) for candidate_id in source_ids):
        raise DynamicSkillGraphGrowthError(
            "prepared dynamic target graph lacks durable Candidate trial evidence"
        )

    plan = _runtime_safe_target_plan(root, seed)
    observed_domains = list(plan["observed_generator_input_domains"])
    if set(observed_domains) != set(_PROBEABLE_DOMAINS):
        raise DynamicSkillGraphGrowthError(
            "prepared graph did not expose numeric, string, and sequence target sources"
        )
    target_domains = [str(item["input_domain"]) for item in plan["target_plan"]]
    if set(target_domains) != set(observed_domains) or len(target_domains) != len(
        set(target_domains)
    ):
        raise DynamicSkillGraphGrowthError(
            "prepared target plan did not select exactly one target per observed input domain"
        )

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
        raise DynamicSkillGraphGrowthError("dynamic target campaign changed source Candidate bytes")
    if _trial_snapshot(root, source_ids) != source_trials:
        raise DynamicSkillGraphGrowthError("dynamic target campaign changed source Candidate trials")

    control_examples = (
        ProgramExample("aa", 7),
        ProgramExample("bbb", -2),
        ProgramExample("c", 11),
    )
    try:
        synthesize_shallowest_verified_route(
            root,
            input_domain="string",
            output_domain="numeric",
            examples=control_examples,
            max_depth=4,
            max_candidates=192,
            max_search_nodes=8192,
            max_behavior_evaluations=4096,
        )
    except AdaptiveDepthCompositionError:
        control_failed_closed = True
    else:
        control_failed_closed = False
    if not control_failed_closed:
        raise DynamicSkillGraphGrowthError(
            "unrelated cross-source control behavior unexpectedly became supported"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "dynamic-skillgraph-input-domain-targets-v1",
        "seed": seed,
        "observed_generator_input_domains": observed_domains,
        "observed_input_domain_count": len(observed_domains),
        "fixed_numeric_sequence_start_set_used": False,
        "target_plan_precommitted_before_solver_execution": bool(
            plan["target_plan_precommitted_before_solver_execution"]
        ),
        "target_plan_commitment": str(plan["target_plan_commitment"]),
        "precommitted_source_order": list(plan["precommitted_source_order"]),
        "target_input_domains": target_domains,
        "target_record_count": len(records),
        "target_records": records,
        "runtime_shape_rejections": list(plan["runtime_shape_rejections"]),
        "exact_runtime_rejections": list(plan["exact_runtime_rejections"]),
        "runtime_budget_unchanged": bool(plan["runtime_budget_unchanged"]),
        "heldback_challenge_shape_unchanged": bool(
            plan["heldback_challenge_shape_unchanged"]
        ),
        "string_source_target_exercised": any(
            record["input_domain"] == "string" for record in records
        ),
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
        "unrelated_cross_source_control_failed_closed": control_failed_closed,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded dynamic skill-graph target-source evidence only. Target generation "
            "derives numeric, string, and sequence source domains from the actually verified durable "
            "Candidate graph and admits only hidden generator routes that execute the unchanged held-"
            "back challenge shape under their already-declared deterministic runtime budgets before "
            "solver execution. Hidden Candidate IDs remain withheld, multistage uniqueness is retained, "
            "and Candidate bytes/trials remain immutable. Probeable domain types, generation, search, "
            "runtime, regression, and scoring remain repository-authored; this does not establish open-"
            "domain target generation, independent production evaluation, or AGI."
        ),
    }
    if not all(
        (
            report["fixed_numeric_sequence_start_set_used"] is False,
            report["target_plan_precommitted_before_solver_execution"],
            report["runtime_budget_unchanged"],
            report["heldback_challenge_shape_unchanged"],
            report["observed_input_domain_count"] == 3,
            report["string_source_target_exercised"],
            report["all_generator_hidden_ids_withheld"],
            report["all_solver_calls_candidate_id_free"],
            report["all_generated_routes_multistage"],
            report["all_fresh_engine_runs_unique"],
            report["all_candidate_state_unchanged"],
            report["all_trial_state_unchanged"],
            report["source_candidate_state_unchanged"],
            report["source_trial_state_unchanged"],
            report["unrelated_cross_source_control_failed_closed"],
        )
    ):
        raise DynamicSkillGraphGrowthError(
            "prepared dynamic skill-graph aggregate invariant failed"
        )
    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "dynamic-skillgraph-input-domains"
        / f"dynamic-inputs-{_digest(seed)[:16]}.json",
        report,
    )
    return report


def run_dynamic_skillgraph_growth_campaign(root: Path, seed: str) -> dict[str, Any]:
    """Grow missing string behavior, then validate runtime-admissible dynamic targets."""

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    adaptive = run_adaptive_depth_composition(root, f"{seed}:pre-dynamic-base-graph")
    if not adaptive.get("passed"):
        raise DynamicSkillGraphGrowthError("pre-dynamic adaptive base graph did not pass")
    three_domain = run_sequence_seed_gap_acquisition(
        root,
        f"{seed}:pre-dynamic-three-domain",
        max_target_attempts=3,
    )
    if not three_domain.get("passed"):
        raise DynamicSkillGraphGrowthError("pre-dynamic three-domain graph did not pass")

    readiness_failures: list[str] = []
    growth_reports: list[dict[str, Any]] = []
    readiness_seed = f"{seed}:dynamic-readiness"
    for growth_index in range(4):
        try:
            readiness = _runtime_safe_target_plan(root, readiness_seed)
        except DynamicSkillGraphGrowthError as exc:
            reason = str(exc)
            readiness_failures.append(reason)
            if "string" not in reason and "runtime-admissible" not in reason:
                raise
        else:
            if "string" not in set(readiness["observed_generator_input_domains"]):
                raise DynamicSkillGraphGrowthError(
                    "ready graph unexpectedly omitted the string source domain"
                )
            break

        string_growth = run_cross_domain_seed_gap_acquisition(
            root,
            f"{seed}:pre-dynamic-string-growth:{growth_index}",
            max_target_attempts=16,
        )
        if not string_growth.get("passed"):
            raise DynamicSkillGraphGrowthError(
                "pre-dynamic string capability growth did not pass"
            )
        growth_reports.append(string_growth)
    else:
        _runtime_safe_target_plan(root, readiness_seed)

    if not growth_reports:
        raise DynamicSkillGraphGrowthError(
            "known missing-string capability boundary disappeared before any growth step"
        )

    dynamic = _run_dynamic_from_existing_graph(
        root,
        f"{seed}:dynamic-source-validation",
    )
    if not dynamic.get("passed"):
        raise DynamicSkillGraphGrowthError("dynamic source-domain validation did not pass")
    if "string" not in set(dynamic["observed_generator_input_domains"]):
        raise DynamicSkillGraphGrowthError(
            "enlarged graph failed to expose the string source domain"
        )
    if not dynamic.get("string_source_target_exercised"):
        raise DynamicSkillGraphGrowthError(
            "enlarged graph did not exercise a string-origin generated target"
        )

    last_growth = growth_reports[-1]
    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "dynamic-skillgraph-growth-campaign-v1",
        "seed": seed,
        "pre_growth_failure_boundary": (
            "the observed verified graph exposes no runtime-admissible unique multi-stage "
            "string-origin target"
        ),
        "failure_boundary_weakened": False,
        "readiness_failures": readiness_failures,
        "bounded_string_growth_attempt_count": len(growth_reports),
        "string_growth_candidate_ids": [
            str(item["candidate_id"]) for item in growth_reports
        ],
        "string_growth_candidate_id": str(last_growth["candidate_id"]),
        "string_growth_candidate_negative_evidence_retained": all(
            bool(item["new_candidate_negative_evidence_retained"])
            for item in growth_reports
        ),
        "string_growth_transactional_commit": last_growth["transactional_commit"],
        "string_growth_control_gap_remained_failed_closed": all(
            bool(item["control_gap_remained_failed_closed"])
            for item in growth_reports
        ),
        "dynamic_report_digest": str(dynamic["digest"]),
        "observed_generator_input_domains": list(
            dynamic["observed_generator_input_domains"]
        ),
        "fixed_numeric_sequence_start_set_used": bool(
            dynamic["fixed_numeric_sequence_start_set_used"]
        ),
        "target_plan_precommitted_before_solver_execution": bool(
            dynamic["target_plan_precommitted_before_solver_execution"]
        ),
        "runtime_budget_unchanged": bool(dynamic["runtime_budget_unchanged"]),
        "heldback_challenge_shape_unchanged": bool(
            dynamic["heldback_challenge_shape_unchanged"]
        ),
        "runtime_shape_rejections": list(dynamic["runtime_shape_rejections"]),
        "exact_runtime_rejections": list(dynamic["exact_runtime_rejections"]),
        "string_source_target_exercised": bool(
            dynamic["string_source_target_exercised"]
        ),
        "all_generator_hidden_ids_withheld": bool(
            dynamic["all_generator_hidden_ids_withheld"]
        ),
        "all_solver_calls_candidate_id_free": bool(
            dynamic["all_solver_calls_candidate_id_free"]
        ),
        "all_generated_routes_multistage": bool(
            dynamic["all_generated_routes_multistage"]
        ),
        "all_fresh_engine_runs_unique": bool(
            dynamic["all_fresh_engine_runs_unique"]
        ),
        "all_candidate_state_unchanged": bool(
            dynamic["all_candidate_state_unchanged"]
        ),
        "all_trial_state_unchanged": bool(dynamic["all_trial_state_unchanged"]),
        "source_candidate_state_unchanged": bool(
            dynamic["source_candidate_state_unchanged"]
        ),
        "source_trial_state_unchanged": bool(
            dynamic["source_trial_state_unchanged"]
        ),
        "unrelated_cross_source_control_failed_closed": bool(
            dynamic["unrelated_cross_source_control_failed_closed"]
        ),
        "target_records": dynamic["target_records"],
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded capability-growth and dynamic target-source evidence only. Missing "
            "string capacity is repaired through the existing exact-scope regression and transactional "
            "commit path, while target selection additionally rejects hidden routes that cannot execute "
            "the unchanged held-back challenge shape under their existing deterministic runtime budgets. "
            "The multistage requirement, challenge shape, runtime budget, Candidate/trial immutability, "
            "and unrelated fail-closed control are not weakened. Generation, search, runtime, regression, "
            "persistence, and scoring remain repository-authored; this does not establish open-domain "
            "target generation, independent production evaluation, or AGI."
        ),
    }
    if not all(
        (
            report["failure_boundary_weakened"] is False,
            report["bounded_string_growth_attempt_count"] >= 1,
            report["string_growth_candidate_negative_evidence_retained"],
            report["string_growth_control_gap_remained_failed_closed"],
            report["fixed_numeric_sequence_start_set_used"] is False,
            report["target_plan_precommitted_before_solver_execution"],
            report["runtime_budget_unchanged"],
            report["heldback_challenge_shape_unchanged"],
            report["string_source_target_exercised"],
            report["all_generator_hidden_ids_withheld"],
            report["all_solver_calls_candidate_id_free"],
            report["all_generated_routes_multistage"],
            report["all_fresh_engine_runs_unique"],
            report["all_candidate_state_unchanged"],
            report["all_trial_state_unchanged"],
            report["source_candidate_state_unchanged"],
            report["source_trial_state_unchanged"],
            report["unrelated_cross_source_control_failed_closed"],
        )
    ):
        raise DynamicSkillGraphGrowthError(
            "dynamic skill-graph growth aggregate invariant failed"
        )

    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "dynamic-skillgraph-growth-campaign"
        / f"growth-{_digest(seed)[:16]}.json",
        report,
    )
    return report
