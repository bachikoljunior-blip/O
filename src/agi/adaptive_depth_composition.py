from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program, validate_program_descriptor
from agi.behavior_guided_tool_chain_discovery import _chain_matches, _execute_chain
from agi.durable_state_rehydration import _tree_snapshot
from agi.heterogeneous_observed_composition import run_heterogeneous_observed_composition
from agi.heterogeneous_retention_campaign import _digest
from agi.learned_tool_chain_compilation import _compile_chain_program
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.observed_three_stage_composition import _select_compatible_heterogeneous_seed
from agi.verified_macro_library_discovery import discover_verified_acquired_program_ids
from agi.verified_macro_synthesis import _load_verified_macro_items


class AdaptiveDepthCompositionError(RuntimeError):
    pass


_SUPPORTED_INPUT_DOMAINS = {"numeric", "sequence"}


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _planning_inputs(domain: str, seed: str) -> tuple[Any, ...]:
    token = _digest({"seed": seed, "domain": domain, "phase": "adaptive-depth-probes-v1"})
    magnitude = 29 + int(token[:4], 16) % 37
    if domain == "numeric":
        return (magnitude, -(magnitude + 5), magnitude + 11)
    if domain == "sequence":
        return (
            [magnitude, -6, 2],
            [-(magnitude + 3), 8, 1],
            [11, -5, magnitude + 4],
        )
    raise AdaptiveDepthCompositionError(f"unsupported planning input domain: {domain}")


def _challenge_inputs(domain: str, commitment: str) -> tuple[Any, ...]:
    magnitude = 191 + int(commitment[:4], 16) % 211
    if domain == "numeric":
        return (magnitude, -(magnitude + 13))
    if domain == "sequence":
        return (
            [magnitude, -23, 7],
            [-(magnitude + 7), 29, 5, -3],
        )
    raise AdaptiveDepthCompositionError(f"unsupported challenge input domain: {domain}")


def _execute_descriptor_chain(
    chain: Sequence[dict[str, Any]],
    value: Any,
) -> Any:
    current = value
    for item in chain:
        current = execute_program(item["program"], current)
    return current


def _items_by_input(items: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_input: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_input.setdefault(str(item["input_domain"]), []).append(item)
    for values in by_input.values():
        values.sort(key=lambda item: str(item["candidate_id"]))
    return by_input


def _bounded_chain_layers(
    items: Sequence[dict[str, Any]],
    *,
    start_domains: set[str],
    max_depth: int,
    max_search_nodes: int,
) -> tuple[dict[int, list[tuple[dict[str, Any], ...]]], int]:
    """Enumerate compatible non-repeating chains under an explicit expansion budget."""

    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 1 <= max_depth <= 6:
        raise ValueError("max_depth must be an integer in [1, 6]")
    if (
        not isinstance(max_search_nodes, int)
        or isinstance(max_search_nodes, bool)
        or not 1 <= max_search_nodes <= 65536
    ):
        raise ValueError("max_search_nodes must be an integer in [1, 65536]")
    if not start_domains or any(not isinstance(value, str) or not value for value in start_domains):
        raise ValueError("start_domains must contain non-empty strings")

    by_input = _items_by_input(items)
    layers: dict[int, list[tuple[dict[str, Any], ...]]] = {}
    frontier: list[tuple[tuple[dict[str, Any], ...], str]] = [
        ((), domain) for domain in sorted(start_domains)
    ]
    visited = 0
    for depth in range(1, max_depth + 1):
        next_frontier: list[tuple[tuple[dict[str, Any], ...], str]] = []
        layer: list[tuple[dict[str, Any], ...]] = []
        for chain, current_domain in frontier:
            used = {str(item["candidate_id"]) for item in chain}
            for item in by_input.get(current_domain, []):
                candidate_id = str(item["candidate_id"])
                if candidate_id in used:
                    continue
                visited += 1
                if visited > max_search_nodes:
                    raise AdaptiveDepthCompositionError(
                        "bounded composition search exceeded max_search_nodes"
                    )
                child = (*chain, item)
                next_domain = str(item["output_domain"])
                next_frontier.append((child, next_domain))
                layer.append(child)
        layers[depth] = layer
        frontier = next_frontier
        if not frontier:
            break
    return layers, visited


def _unique_match_or_error(
    matching: Sequence[tuple[dict[str, Any], ...]],
    *,
    depth: int,
) -> tuple[dict[str, Any], ...] | None:
    if not matching:
        return None
    if len(matching) != 1:
        raise AdaptiveDepthCompositionError(
            f"ambiguous behavior across minimal matching chains at depth {depth}"
        )
    return matching[0]


def synthesize_shallowest_verified_route(
    root: Path,
    *,
    input_domain: str,
    output_domain: str,
    examples: Sequence[ProgramExample],
    max_depth: int = 4,
    max_candidates: int = 64,
    max_search_nodes: int = 512,
    max_behavior_evaluations: int = 256,
) -> dict[str, Any]:
    """Discover the verified library and stop at the shallowest unique behavior-matching depth.

    Search expands only type-compatible non-repeating paths and has explicit expansion and behavior
    evaluation budgets. A behavior match at the first successful depth must be unique; ambiguity fails
    closed rather than searching deeper because deeper routes cannot make an ambiguous minimal route
    unique. No Candidate IDs are accepted from the caller.
    """

    if not isinstance(input_domain, str) or not input_domain:
        raise ValueError("input_domain must be non-empty")
    if not isinstance(output_domain, str) or not output_domain:
        raise ValueError("output_domain must be non-empty")
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 1 <= max_depth <= 6:
        raise ValueError("max_depth must be an integer in [1, 6]")
    if (
        not isinstance(max_behavior_evaluations, int)
        or isinstance(max_behavior_evaluations, bool)
        or not 1 <= max_behavior_evaluations <= 65536
    ):
        raise ValueError("max_behavior_evaluations must be an integer in [1, 65536]")
    normalized_examples = tuple(examples)
    if len(normalized_examples) < 2:
        raise ValueError("at least two behavior examples are required")

    root = root.resolve()
    library_ids = discover_verified_acquired_program_ids(root, max_candidates=max_candidates)
    if not library_ids:
        raise AdaptiveDepthCompositionError("verified acquired-program library is empty")
    items = _load_verified_macro_items(root, library_ids)
    by_input = _items_by_input(items)
    trials_before = _trial_snapshot(root, library_ids)
    if not all(trials_before.get(candidate_id) for candidate_id in library_ids):
        raise AdaptiveDepthCompositionError("verified library has missing Candidate trial evidence")

    frontier: list[tuple[tuple[dict[str, Any], ...], str]] = [((), input_domain)]
    search_nodes = 0
    behavior_evaluations = 0
    shortest_type_depth: int | None = None
    attempts: list[dict[str, Any]] = []
    selected: tuple[dict[str, Any], ...] | None = None

    for depth in range(1, max_depth + 1):
        next_frontier: list[tuple[tuple[dict[str, Any], ...], str]] = []
        typed_at_depth: list[tuple[dict[str, Any], ...]] = []
        for chain, current_domain in frontier:
            used = {str(item["candidate_id"]) for item in chain}
            for item in by_input.get(current_domain, []):
                candidate_id = str(item["candidate_id"])
                if candidate_id in used:
                    continue
                search_nodes += 1
                if search_nodes > max_search_nodes:
                    raise AdaptiveDepthCompositionError(
                        "adaptive route search exceeded max_search_nodes"
                    )
                child = (*chain, item)
                next_domain = str(item["output_domain"])
                next_frontier.append((child, next_domain))
                if next_domain == output_domain:
                    typed_at_depth.append(child)

        if typed_at_depth and shortest_type_depth is None:
            shortest_type_depth = depth
        matching: list[tuple[dict[str, Any], ...]] = []
        for chain in typed_at_depth:
            behavior_evaluations += 1
            if behavior_evaluations > max_behavior_evaluations:
                raise AdaptiveDepthCompositionError(
                    "adaptive route search exceeded max_behavior_evaluations"
                )
            if _chain_matches(root, chain, normalized_examples):
                matching.append(chain)

        attempts.append(
            {
                "depth": depth,
                "typed_chain_count": len(typed_at_depth),
                "behavior_matching_count": len(matching),
                "cumulative_search_nodes": search_nodes,
                "cumulative_behavior_evaluations": behavior_evaluations,
            }
        )
        selected = _unique_match_or_error(matching, depth=depth)
        if selected is not None:
            break
        frontier = next_frontier
        if not frontier:
            break

    if selected is None:
        raise AdaptiveDepthCompositionError(
            "no unique behavior-matching verified route exists within the bounded depth"
        )
    if shortest_type_depth is None:  # pragma: no cover - selected implies a typed route
        raise AdaptiveDepthCompositionError("adaptive route search lost shortest type depth")

    selected_ids = [str(item["candidate_id"]) for item in selected]
    selected_depth = len(selected)
    compiled_program = _compile_chain_program(selected, normalized_examples)
    compiled_meta = validate_program_descriptor(compiled_program)
    if (
        str(compiled_meta["input_domain"]) != input_domain
        or str(compiled_meta["output_domain"]) != output_domain
    ):
        raise AdaptiveDepthCompositionError("compiled adaptive route changed requested type")
    for example in normalized_examples:
        runtime_output, _run_id, _refs = _execute_chain(root, selected, example.input)
        compiled_output = execute_program(compiled_program, example.input)
        if runtime_output != example.output or compiled_output != runtime_output:
            raise AdaptiveDepthCompositionError(
                "compiled adaptive route changed committed behavior"
            )

    trials_after = _trial_snapshot(root, library_ids)
    if trials_after != trials_before:
        raise AdaptiveDepthCompositionError(
            "adaptive route synthesis consumed or rewrote Candidate trial evidence"
        )

    result: dict[str, Any] = {
        "schema_version": 1,
        "candidate_ids_supplied_by_caller": False,
        "library_candidate_ids": list(library_ids),
        "library_candidate_count": len(library_ids),
        "input_domain": input_domain,
        "output_domain": output_domain,
        "max_depth_allowed": max_depth,
        "selected_chain_depth": selected_depth,
        "shortest_type_chain_depth": shortest_type_depth,
        "selected_candidate_ids": selected_ids,
        "compiled_program": compiled_program,
        "compiled_program_nodes": int(compiled_meta["nodes"]),
        "compiled_program_depth": int(compiled_meta["depth"]),
        "depth_attempts": attempts,
        "depths_attempted": [int(item["depth"]) for item in attempts],
        "search_nodes_visited": search_nodes,
        "search_node_budget": max_search_nodes,
        "behavior_evaluations": behavior_evaluations,
        "behavior_evaluation_budget": max_behavior_evaluations,
        "stopped_at_first_unique_behavior_depth": True,
        "deeper_depths_not_searched": list(range(selected_depth + 1, max_depth + 1)),
        "candidate_trial_state_unchanged": True,
        "all_source_negative_evidence_retained": all(
            bool(item.get("negative_evidence_retained")) for item in items
        ),
    }
    result["digest"] = _digest({key: value for key, value in result.items() if key != "digest"})
    return result


def run_adaptive_depth_composition(root: Path, seed: str) -> dict[str, Any]:
    """Select and transfer an observed composed behavior without hard-coding its solution depth.

    A bounded heterogeneous verified library is created first. A fresh state-only planner observes all
    compatible behaviors through target-generation depth three under a finite expansion budget and
    chooses a seed-committed unique non-constant target whose minimal behavior route is deeper than a
    shorter type-correct route. The adaptive solver is allowed depth four but incrementally searches from
    depth one and stops as soon as exactly one behavior-matching route exists. It never receives Candidate
    IDs from the caller. Post-selection challenges run in fresh Engines and source/fresh Candidate bytes
    and trial ledgers must remain unchanged.

    This is repository-authored bounded internal development evidence only. It does not establish
    independent production evaluation or AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    prerequisite_seed, rejected_prerequisite_seeds = _select_compatible_heterogeneous_seed(seed)
    prerequisite = run_heterogeneous_observed_composition(root, prerequisite_seed)
    if not prerequisite.get("passed"):
        raise AdaptiveDepthCompositionError("heterogeneous prerequisite did not pass")

    all_ids = _all_candidate_ids(root)
    source_tree = _tree_snapshot(root, all_ids)
    source_trials = _trial_snapshot(root, all_ids)
    if not all(source_trials.get(candidate_id) for candidate_id in all_ids):
        raise AdaptiveDepthCompositionError("durable library has missing Candidate trial evidence")

    target_generation_max_depth = 3
    adaptive_max_depth = 4
    target_search_budget = 256
    adaptive_search_budget = 256
    adaptive_behavior_budget = 128

    with tempfile.TemporaryDirectory(prefix="agi-adaptive-depth-") as temporary:
        fresh_root = Path(temporary).resolve()
        _copy_persistent_state(root, fresh_root)
        prior_runs_copied = (fresh_root / ".continual" / "runs").exists()
        prior_episodes_copied = (fresh_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (fresh_root / ".continual" / "evidence").exists()
        if _tree_snapshot(fresh_root, all_ids) != source_tree:
            raise AdaptiveDepthCompositionError("fresh planner changed Candidate bytes")
        if _trial_snapshot(fresh_root, all_ids) != source_trials:
            raise AdaptiveDepthCompositionError("fresh planner changed Candidate trials")

        library_ids = discover_verified_acquired_program_ids(fresh_root, max_candidates=64)
        items = _load_verified_macro_items(fresh_root, library_ids)
        layers, target_search_nodes = _bounded_chain_layers(
            items,
            start_domains=set(_SUPPORTED_INPUT_DOMAINS),
            max_depth=target_generation_max_depth,
            max_search_nodes=target_search_budget,
        )
        inputs_by_domain = {
            domain: _planning_inputs(domain, seed)
            for domain in _SUPPORTED_INPUT_DOMAINS
        }

        records: list[dict[str, Any]] = []
        behavior_depths: dict[tuple[str, str, str], list[int]] = {}
        same_depth_groups: dict[tuple[int, str, str, str], list[tuple[str, ...]]] = {}
        type_depths: dict[tuple[str, str], list[int]] = {}
        for depth, chains in sorted(layers.items()):
            for chain in chains:
                first = chain[0]
                last = chain[-1]
                input_domain = str(first["input_domain"])
                output_domain = str(last["output_domain"])
                if input_domain not in inputs_by_domain:
                    continue
                planning_inputs = inputs_by_domain[input_domain]
                outputs = [
                    _execute_descriptor_chain(chain, value)
                    for value in planning_inputs
                ]
                behavior_key = (input_domain, output_domain, _canonical(outputs))
                chain_ids = tuple(str(item["candidate_id"]) for item in chain)
                stage_signatures = tuple(
                    (str(item["input_domain"]), str(item["output_domain"]))
                    for item in chain
                )
                behavior_depths.setdefault(behavior_key, []).append(depth)
                same_depth_groups.setdefault((depth, *behavior_key), []).append(chain_ids)
                type_depths.setdefault((input_domain, output_domain), []).append(depth)
                records.append(
                    {
                        "chain": chain,
                        "chain_ids": chain_ids,
                        "depth": depth,
                        "input_domain": input_domain,
                        "output_domain": output_domain,
                        "planning_inputs": planning_inputs,
                        "outputs": outputs,
                        "behavior_key": behavior_key,
                        "stage_signatures": stage_signatures,
                    }
                )

        eligible: list[dict[str, Any]] = []
        for record in records:
            depth = int(record["depth"])
            if depth < 2:
                continue
            if len(set(record["stage_signatures"])) < 2:
                continue
            if len({_canonical(value) for value in record["outputs"]}) < 2:
                continue
            behavior_key = record["behavior_key"]
            if min(behavior_depths[behavior_key]) != depth:
                continue
            if len(same_depth_groups[(depth, *behavior_key)]) != 1:
                continue
            signature = (str(record["input_domain"]), str(record["output_domain"]))
            shorter_type_depths = [value for value in type_depths[signature] if value < depth]
            if not shorter_type_depths:
                continue
            item = dict(record)
            item["shorter_type_depth"] = min(shorter_type_depths)
            eligible.append(item)
        if not eligible:
            raise AdaptiveDepthCompositionError(
                "observed library exposed no unique composed target beyond a shorter type route"
            )

        eligible.sort(key=lambda item: (int(item["depth"]), tuple(item["chain_ids"])))
        selection_commitment = _digest(
            {
                "seed": seed,
                "library_ids": sorted(library_ids),
                "eligible": [
                    {
                        "chain_ids": list(item["chain_ids"]),
                        "depth": item["depth"],
                        "input_domain": item["input_domain"],
                        "output_domain": item["output_domain"],
                        "outputs": item["outputs"],
                        "stage_signatures": [list(value) for value in item["stage_signatures"]],
                        "shorter_type_depth": item["shorter_type_depth"],
                    }
                    for item in eligible
                ],
                "phase": "adaptive-depth-target-selection-v1",
            }
        )
        target = eligible[int(selection_commitment[:8], 16) % len(eligible)]
        hidden_chain = tuple(str(value) for value in target["chain_ids"])
        planning_examples = tuple(
            ProgramExample(value, output)
            for value, output in zip(
                target["planning_inputs"],
                target["outputs"],
                strict=True,
            )
        )

        synthesized = synthesize_shallowest_verified_route(
            fresh_root,
            input_domain=str(target["input_domain"]),
            output_domain=str(target["output_domain"]),
            examples=planning_examples,
            max_depth=adaptive_max_depth,
            max_candidates=64,
            max_search_nodes=adaptive_search_budget,
            max_behavior_evaluations=adaptive_behavior_budget,
        )
        selected_ids = tuple(str(value) for value in synthesized["selected_candidate_ids"])
        if synthesized.get("candidate_ids_supplied_by_caller") is not False:
            raise AdaptiveDepthCompositionError("adaptive synthesis relied on caller Candidate IDs")
        if selected_ids != hidden_chain:
            raise AdaptiveDepthCompositionError(
                "adaptive synthesis did not rediscover the uniquely observed route"
            )
        if int(synthesized["selected_chain_depth"]) != int(target["depth"]):
            raise AdaptiveDepthCompositionError("adaptive synthesis selected the wrong solution depth")
        if int(synthesized["shortest_type_chain_depth"]) >= int(target["depth"]):
            raise AdaptiveDepthCompositionError(
                "target did not behaviorally reject a shorter type-correct route"
            )
        if synthesized["depths_attempted"] != list(
            range(1, int(synthesized["selected_chain_depth"]) + 1)
        ):
            raise AdaptiveDepthCompositionError("adaptive synthesis skipped a shallower depth")
        if int(synthesized["selected_chain_depth"]) >= adaptive_max_depth:
            raise AdaptiveDepthCompositionError(
                "adaptive target did not demonstrate early bounded depth stopping"
            )

        challenge_commitment = _digest(
            {
                "selection_commitment": selection_commitment,
                "synthesis_digest": synthesized["digest"],
                "phase": "adaptive-depth-post-selection-v1",
            }
        )
        challenge_inputs = _challenge_inputs(str(target["input_domain"]), challenge_commitment)
        if any(value in target["planning_inputs"] for value in challenge_inputs):
            raise AdaptiveDepthCompositionError("challenge overlaps planning inputs")
        expected_outputs = [
            _execute_descriptor_chain(target["chain"], value)
            for value in challenge_inputs
        ]
        selected_items = _load_verified_macro_items(fresh_root, list(selected_ids))
        fresh_run_ids: list[str] = []
        challenge_outputs: list[dict[str, Any]] = []
        for value, expected in zip(challenge_inputs, expected_outputs, strict=True):
            compiled_output = execute_program(synthesized["compiled_program"], value)
            runtime_output, run_id, refs = _execute_chain(fresh_root, selected_items, value)
            if compiled_output != expected or runtime_output != expected:
                raise AdaptiveDepthCompositionError("adaptive route failed post-selection challenge")
            if len(refs) != int(synthesized["selected_chain_depth"]):
                raise AdaptiveDepthCompositionError("challenge executed the wrong learned-stage count")
            fresh_run_ids.append(run_id)
            challenge_outputs.append(
                {"input": value, "expected": expected, "output": runtime_output}
            )

        if len(set(fresh_run_ids)) != len(fresh_run_ids):
            raise AdaptiveDepthCompositionError("post-selection challenge reused a fresh Engine run")
        if _tree_snapshot(fresh_root, all_ids) != source_tree:
            raise AdaptiveDepthCompositionError("fresh challenge changed Candidate bytes")
        if _trial_snapshot(fresh_root, all_ids) != source_trials:
            raise AdaptiveDepthCompositionError("fresh challenge changed Candidate trials")

    if _tree_snapshot(root, all_ids) != source_tree:
        raise AdaptiveDepthCompositionError("fresh transfer mutated source Candidate bytes")
    if _trial_snapshot(root, all_ids) != source_trials:
        raise AdaptiveDepthCompositionError("fresh transfer mutated source Candidate trials")

    selected_depth = int(synthesized["selected_chain_depth"])
    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "adaptive-depth-composition-v1",
        "prerequisite_seed": prerequisite_seed,
        "prerequisite_rejected_seed_count": len(rejected_prerequisite_seeds),
        "prerequisite_digest": str(prerequisite["digest"]),
        "caller_supplied_candidate_ids": False,
        "planner_selected_by_semantic_role_name": False,
        "target_generation_max_depth": target_generation_max_depth,
        "target_search_nodes_visited": target_search_nodes,
        "target_search_node_budget": target_search_budget,
        "eligible_behavior_target_count": len(eligible),
        "eligible_behavior_target_depths": sorted({int(item["depth"]) for item in eligible}),
        "selection_commitment": selection_commitment,
        "hidden_observed_chain": list(hidden_chain),
        "hidden_observed_depth": int(target["depth"]),
        "selected_candidate_ids": list(selected_ids),
        "selected_chain_depth": selected_depth,
        "shortest_type_chain_depth": int(synthesized["shortest_type_chain_depth"]),
        "shorter_type_correct_route_depth": int(target["shorter_type_depth"]),
        "selected_stage_signatures": [list(value) for value in target["stage_signatures"]],
        "selected_distinct_stage_signature_count": len(set(target["stage_signatures"])),
        "adaptive_max_depth": adaptive_max_depth,
        "adaptive_depths_attempted": synthesized["depths_attempted"],
        "adaptive_depth_attempts": synthesized["depth_attempts"],
        "adaptive_search_nodes_visited": int(synthesized["search_nodes_visited"]),
        "adaptive_search_node_budget": int(synthesized["search_node_budget"]),
        "adaptive_behavior_evaluations": int(synthesized["behavior_evaluations"]),
        "adaptive_behavior_evaluation_budget": int(synthesized["behavior_evaluation_budget"]),
        "adaptive_stopped_at_first_unique_behavior_depth": bool(
            synthesized["stopped_at_first_unique_behavior_depth"]
        ),
        "deeper_depths_not_searched": synthesized["deeper_depths_not_searched"],
        "shorter_routes_rejected_by_behavior": True,
        "ambiguity_policy": "fail_closed_at_minimal_matching_depth",
        "unsupported_domain_policy": "fail_closed",
        "search_budget_policy": "fail_closed",
        "challenge_generated_after_selection": True,
        "challenge_commitment": challenge_commitment,
        "challenge_case_count": len(challenge_inputs),
        "challenge_outputs": challenge_outputs,
        "fresh_engine_runs": fresh_run_ids,
        "fresh_engine_runs_unique": len(set(fresh_run_ids)) == len(fresh_run_ids),
        "fresh_candidate_state_unchanged": True,
        "fresh_trial_state_unchanged": True,
        "source_candidate_state_unchanged": True,
        "source_trial_state_unchanged": True,
        "prior_runs_copied": prior_runs_copied,
        "prior_episodes_copied": prior_episodes_copied,
        "prior_evidence_copied": prior_evidence_copied,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded adaptive-depth composition evidence only. A fresh state-only planner "
            "discovered verified durable skills without caller Candidate IDs, selected a behavior-defined "
            "composed target, incrementally stopped at the shallowest unique behavior-sufficient depth "
            "under explicit budgets, rejected shorter type-correct routes by behavior, and transferred "
            "the route across fresh Engine challenges without rewriting Candidate bytes or trial ledgers. "
            "Repository-authored generation, search, runtime, regression, and scoring do not establish "
            "independent production evidence or AGI."
        ),
    }
    if not all(
        (
            report["fresh_engine_runs_unique"],
            report["adaptive_stopped_at_first_unique_behavior_depth"],
            report["selected_chain_depth"] == report["hidden_observed_depth"],
            report["selected_chain_depth"] < report["adaptive_max_depth"],
            report["shortest_type_chain_depth"] < report["selected_chain_depth"],
            report["shorter_type_correct_route_depth"] < report["selected_chain_depth"],
            report["selected_distinct_stage_signature_count"] >= 2,
            report["adaptive_search_nodes_visited"] <= report["adaptive_search_node_budget"],
            report["adaptive_behavior_evaluations"]
            <= report["adaptive_behavior_evaluation_budget"],
            report["target_search_nodes_visited"] <= report["target_search_node_budget"],
            not report["prior_runs_copied"],
            not report["prior_episodes_copied"],
            not report["prior_evidence_copied"],
        )
    ):
        raise AdaptiveDepthCompositionError("adaptive-depth aggregate invariant failed")
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "adaptive-depth-composition"
        / f"composition-{_digest(seed)[:16]}.json",
        report,
    )
    return report
