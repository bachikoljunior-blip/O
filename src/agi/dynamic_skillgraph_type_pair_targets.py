from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample
from agi.adaptive_depth_composition import (
    AdaptiveDepthCompositionError,
    _bounded_chain_layers,
    synthesize_shallowest_verified_route,
)
from agi.durable_state_rehydration import _tree_snapshot
from agi.dynamic_skillgraph_input_domains import (
    _behavior_record,
    _domain_planning_inputs,
    _execute_dynamic_target,
    _observed_generator_input_domains,
)
from agi.heterogeneous_retention_campaign import _digest
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.verified_macro_library_discovery import discover_verified_acquired_program_ids
from agi.verified_macro_synthesis import _load_verified_macro_items


class DynamicSkillGraphTypePairError(RuntimeError):
    pass


def _pair_key(input_domain: str, output_domain: str) -> str:
    return f"{input_domain}->{output_domain}"


def _precommit_type_pair_target_plan(
    root: Path,
    seed: str,
    *,
    max_generation_depth: int = 3,
    max_generation_search_nodes: int = 8192,
) -> dict[str, Any]:
    """Commit one unique multistage behavior target for every observed reachable typed pair."""

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    library_ids = discover_verified_acquired_program_ids(root, max_candidates=256)
    if not library_ids:
        raise DynamicSkillGraphTypePairError("verified skill graph is empty")
    items = tuple(_load_verified_macro_items(root, library_ids))
    input_domains = _observed_generator_input_domains(items)
    if len(input_domains) < 3:
        raise DynamicSkillGraphTypePairError(
            "prepared graph exposed fewer than three probeable input domains"
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
    nonconstant_reachable_pairs: set[tuple[str, str]] = set()
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
            if not record["nonconstant"]:
                continue
            pair = (str(record["input_domain"]), str(record["output_domain"]))
            nonconstant_reachable_pairs.add(pair)
            by_semantics[
                (
                    pair[0],
                    pair[1],
                    str(record["behavior_fingerprint"]),
                )
            ].append(record)

    if not nonconstant_reachable_pairs:
        raise DynamicSkillGraphTypePairError("no nonconstant multistage typed pairs were reachable")

    eligible: list[dict[str, Any]] = []
    for semantic_records in by_semantics.values():
        minimal_depth = min(int(item["depth"]) for item in semantic_records)
        minimal = [item for item in semantic_records if int(item["depth"]) == minimal_depth]
        if minimal_depth < 2 or len(minimal) != 1:
            continue
        selected = dict(minimal[0])
        selected["unique_at_minimal_behavior_depth"] = True
        eligible.append(selected)

    by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in eligible:
        by_pair[(str(item["input_domain"]), str(item["output_domain"]))].append(item)

    missing_pairs = sorted(nonconstant_reachable_pairs - set(by_pair))
    if missing_pairs:
        raise DynamicSkillGraphTypePairError(
            "reachable typed pairs lack unique multistage semantics: "
            + ",".join(_pair_key(*pair) for pair in missing_pairs)
        )

    observed_pairs = sorted(nonconstant_reachable_pairs)
    if len(observed_pairs) < 4:
        raise DynamicSkillGraphTypePairError(
            "prepared graph exposed fewer than four nonconstant multistage typed pairs"
        )
    if not any(input_domain != output_domain for input_domain, output_domain in observed_pairs):
        raise DynamicSkillGraphTypePairError(
            "prepared graph exposed no cross-domain multistage typed pair"
        )

    pair_order = sorted(
        observed_pairs,
        key=lambda pair: _digest(
            {
                "seed": seed,
                "input_domain": pair[0],
                "output_domain": pair[1],
                "phase": "dynamic-skillgraph-type-pair-order-v1",
            }
        ),
    )
    selected_targets: list[dict[str, Any]] = []
    for pair in pair_order:
        ranked = sorted(
            by_pair[pair],
            key=lambda item: _digest(
                {
                    "seed": seed,
                    "pair": list(pair),
                    "candidate_ids": item["candidate_ids"],
                    "behavior_fingerprint": item["behavior_fingerprint"],
                    "phase": "dynamic-skillgraph-type-pair-target-rank-v1",
                }
            ),
        )
        selected_targets.append(dict(ranked[0]))

    public_targets = [
        {
            "target_index": index,
            "input_domain": str(item["input_domain"]),
            "output_domain": str(item["output_domain"]),
            "pair_key": _pair_key(str(item["input_domain"]), str(item["output_domain"])),
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
    commitment = _digest(
        {
            "seed": seed,
            "library_candidate_ids": list(library_ids),
            "observed_input_domains": list(input_domains),
            "observed_type_pairs": [list(pair) for pair in observed_pairs],
            "pair_order": [list(pair) for pair in pair_order],
            "targets": public_targets,
            "phase": "dynamic-skillgraph-type-pair-plan-v1",
        }
    )
    return {
        "library_candidate_ids": list(library_ids),
        "observed_generator_input_domains": list(input_domains),
        "observed_type_pairs": [
            {
                "input_domain": input_domain,
                "output_domain": output_domain,
                "pair_key": _pair_key(input_domain, output_domain),
            }
            for input_domain, output_domain in observed_pairs
        ],
        "precommitted_pair_order": [_pair_key(*pair) for pair in pair_order],
        "generation_search_nodes": search_nodes,
        "generation_search_node_budget": max_generation_search_nodes,
        "eligible_semantic_target_count": len(eligible),
        "target_plan": public_targets,
        "target_plan_commitment": commitment,
        "target_plan_precommitted_before_solver_execution": True,
    }


def run_dynamic_skillgraph_type_pair_targets(root: Path, seed: str) -> dict[str, Any]:
    """Transfer one hidden multistage behavior from every observed reachable typed pair.

    The caller must provide a graph already grown by the preceding dynamic-source milestone. This
    milestone derives both dimensions of the target source from the durable verified graph: every
    nonconstant typed input/output pair reachable by at least two verified stages must expose at least
    one unique minimal-depth behavior. The full pair registry and one hidden route per pair are committed
    before any solver runs. Each solver receives only types and behavior examples, never generator
    Candidate IDs, and must replay held-back semantics through fresh Engines without changing Candidate
    bytes or trial ledgers. A precommitted string->numeric control remains intentionally unsupported.

    This is bounded repository-authored internal development evidence, not independent production
    evidence and not an AGI claim.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    source_ids = _all_candidate_ids(root)
    source_tree = _tree_snapshot(root, source_ids)
    source_trials = _trial_snapshot(root, source_ids)
    if not source_ids or not all(source_trials.get(candidate_id) for candidate_id in source_ids):
        raise DynamicSkillGraphTypePairError(
            "type-pair source graph lacks durable Candidate trial evidence"
        )

    plan = _precommit_type_pair_target_plan(root, seed)
    pair_keys = [str(item["pair_key"]) for item in plan["observed_type_pairs"]]
    target_pair_keys = [str(item["pair_key"]) for item in plan["target_plan"]]
    if set(target_pair_keys) != set(pair_keys) or len(target_pair_keys) != len(set(target_pair_keys)):
        raise DynamicSkillGraphTypePairError(
            "precommitted type-pair plan did not select exactly one target per observed pair"
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
        raise DynamicSkillGraphTypePairError("type-pair campaign changed source Candidate bytes")
    if _trial_snapshot(root, source_ids) != source_trials:
        raise DynamicSkillGraphTypePairError("type-pair campaign changed source Candidate trials")

    control_seed = _digest(
        {
            "seed": seed,
            "target_plan_commitment": plan["target_plan_commitment"],
            "phase": "dynamic-skillgraph-cross-pair-control-v1",
        }
    )
    control_examples = (
        ProgramExample(f"control-{control_seed[:5]}", 17),
        ProgramExample(f"other-{control_seed[5:10]}-xx", -3),
        ProgramExample(f"z-{control_seed[10:15]}", 11),
    )
    control_pair = "string->numeric"
    control_commitment = _digest(
        {
            "pair": control_pair,
            "examples": [
                {"input": item.input, "output": item.output}
                for item in control_examples
            ],
            "phase": "dynamic-skillgraph-cross-pair-control-commit-v1",
        }
    )
    if control_pair in pair_keys:
        raise DynamicSkillGraphTypePairError(
            "untouched cross-pair control became an observed reachable pair before evaluation"
        )
    try:
        synthesize_shallowest_verified_route(
            root,
            input_domain="string",
            output_domain="numeric",
            examples=control_examples,
            max_depth=4,
            max_candidates=256,
            max_search_nodes=16384,
            max_behavior_evaluations=8192,
        )
    except AdaptiveDepthCompositionError:
        control_failed_closed = True
    else:
        control_failed_closed = False
    if not control_failed_closed:
        raise DynamicSkillGraphTypePairError(
            "untouched string->numeric cross-pair control unexpectedly became supported"
        )
    if _tree_snapshot(root, source_ids) != source_tree:
        raise DynamicSkillGraphTypePairError("cross-pair control changed source Candidate bytes")
    if _trial_snapshot(root, source_ids) != source_trials:
        raise DynamicSkillGraphTypePairError("cross-pair control changed source Candidate trials")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "dynamic-skillgraph-type-pair-targets-v1",
        "seed": seed,
        "observed_generator_input_domains": list(plan["observed_generator_input_domains"]),
        "observed_type_pairs": list(plan["observed_type_pairs"]),
        "observed_type_pair_count": len(pair_keys),
        "target_plan_precommitted_before_solver_execution": bool(
            plan["target_plan_precommitted_before_solver_execution"]
        ),
        "target_plan_commitment": str(plan["target_plan_commitment"]),
        "precommitted_pair_order": list(plan["precommitted_pair_order"]),
        "target_pair_keys": target_pair_keys,
        "target_records": records,
        "cross_domain_pair_target_exercised": any(
            record["input_domain"] != record["output_domain"] for record in records
        ),
        "all_generator_hidden_ids_withheld": all(
            record["generator_hidden_candidate_ids_supplied_to_solver"] is False
            for record in records
        ),
        "all_solver_calls_candidate_id_free": all(
            record["solver_candidate_ids_supplied_by_caller"] is False
            for record in records
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
        "untouched_cross_pair_control": {
            "pair_key": control_pair,
            "commitment": control_commitment,
            "failed_closed": control_failed_closed,
        },
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded type-pair target-source evidence only. The complete nonconstant multistage "
            "typed-pair registry and hidden target route for each observed pair are committed before solver "
            "execution; solvers receive no Candidate IDs and must transfer through fresh Engines while "
            "source Candidate bytes and trials remain immutable. Types, generation, search, runtime, "
            "regression, control construction, and scoring remain repository-authored; this does not "
            "establish open-domain target generation, independent production evaluation, or AGI."
        ),
    }
    if not all(
        (
            report["observed_type_pair_count"] >= 4,
            report["target_plan_precommitted_before_solver_execution"],
            report["cross_domain_pair_target_exercised"],
            report["all_generator_hidden_ids_withheld"],
            report["all_solver_calls_candidate_id_free"],
            report["all_generated_routes_multistage"],
            report["all_fresh_engine_runs_unique"],
            report["all_candidate_state_unchanged"],
            report["all_trial_state_unchanged"],
            report["source_candidate_state_unchanged"],
            report["source_trial_state_unchanged"],
            report["untouched_cross_pair_control"]["failed_closed"],
        )
    ):
        raise DynamicSkillGraphTypePairError("dynamic type-pair aggregate invariant failed")

    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "dynamic-skillgraph-type-pairs"
        / f"type-pairs-{_digest(seed)[:16]}.json",
        report,
    )
    return report
