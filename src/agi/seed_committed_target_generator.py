from __future__ import annotations

import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program
from agi.adaptive_depth_composition import (
    AdaptiveDepthCompositionError,
    _bounded_chain_layers,
    _challenge_inputs,
    _execute_descriptor_chain,
    _planning_inputs,
    run_adaptive_depth_composition,
    synthesize_shallowest_verified_route,
)
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.durable_state_rehydration import _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.verified_macro_library_discovery import discover_verified_acquired_program_ids
from agi.verified_macro_synthesis import _load_verified_macro_items


class SeedCommittedTargetGeneratorError(RuntimeError):
    pass


_SUPPORTED_GENERATOR_INPUT_DOMAINS = {"numeric", "sequence"}


def _candidate_ids(chain: Sequence[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(str(item["candidate_id"]) for item in chain)


def _behavior_record(
    chain: tuple[dict[str, Any], ...],
    *,
    support_inputs: dict[str, tuple[Any, ...]],
) -> dict[str, Any]:
    input_domain = str(chain[0]["input_domain"])
    output_domain = str(chain[-1]["output_domain"])
    inputs = support_inputs[input_domain]
    outputs = tuple(_execute_descriptor_chain(chain, value) for value in inputs)
    fingerprint = _digest(
        {
            "input_domain": input_domain,
            "output_domain": output_domain,
            "observations": [
                {"input": value, "output": output}
                for value, output in zip(inputs, outputs, strict=True)
            ],
            "phase": "seed-committed-generated-target-behavior-v1",
        }
    )
    return {
        "candidate_ids": _candidate_ids(chain),
        "depth": len(chain),
        "input_domain": input_domain,
        "output_domain": output_domain,
        "support_inputs": inputs,
        "support_outputs": outputs,
        "behavior_fingerprint": fingerprint,
        "nonconstant": len({_digest(value) for value in outputs}) > 1,
    }


def _precommit_target_plan(
    root: Path,
    seed: str,
    *,
    max_target_attempts: int,
    max_generation_depth: int,
    max_generation_search_nodes: int,
) -> dict[str, Any]:
    library_ids = discover_verified_acquired_program_ids(root, max_candidates=64)
    if not library_ids:
        raise SeedCommittedTargetGeneratorError("verified skill graph is empty")
    items = tuple(_load_verified_macro_items(root, library_ids))
    layers, search_nodes = _bounded_chain_layers(
        items,
        start_domains=set(_SUPPORTED_GENERATOR_INPUT_DOMAINS),
        max_depth=max_generation_depth,
        max_search_nodes=max_generation_search_nodes,
    )
    support_inputs = {
        domain: _planning_inputs(domain, f"{seed}:generator-support-v1")
        for domain in sorted(_SUPPORTED_GENERATOR_INPUT_DOMAINS)
    }

    records: list[dict[str, Any]] = []
    by_semantics: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for depth, chains in sorted(layers.items()):
        for chain in chains:
            if depth < 2:
                continue
            input_domain = str(chain[0]["input_domain"])
            if input_domain not in _SUPPORTED_GENERATOR_INPUT_DOMAINS:
                continue
            record = _behavior_record(chain, support_inputs=support_inputs)
            records.append(record)
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

    if len(eligible) < 2:
        raise SeedCommittedTargetGeneratorError(
            "verified skill graph exposed fewer than two nonconstant unique generated semantics"
        )

    ranked = sorted(
        eligible,
        key=lambda item: _digest(
            {
                "seed": seed,
                "candidate_ids": item["candidate_ids"],
                "behavior_fingerprint": item["behavior_fingerprint"],
                "phase": "seed-committed-generated-target-ranking-v1",
            }
        ),
    )
    selected = ranked[:max_target_attempts]
    plan_public = [
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
        for index, item in enumerate(selected)
    ]
    plan_commitment = _digest(
        {
            "seed": seed,
            "generator_version": "seed-committed-skill-graph-v1",
            "targets": plan_public,
            "phase": "seed-committed-generated-target-plan-v1",
        }
    )
    return {
        "library_candidate_ids": list(library_ids),
        "generation_search_nodes": search_nodes,
        "generation_search_node_budget": max_generation_search_nodes,
        "eligible_semantic_target_count": len(eligible),
        "target_plan": plan_public,
        "target_plan_commitment": plan_commitment,
    }


def _execute_generated_target(
    root: Path,
    target: dict[str, Any],
    *,
    plan_commitment: str,
    source_ids: list[str],
    source_tree: dict[str, str],
    source_trials: dict[str, dict[str, str]],
    max_solver_depth: int,
    max_solver_search_nodes: int,
    max_solver_behavior_evaluations: int,
) -> dict[str, Any]:
    examples = tuple(
        ProgramExample(item["input"], item["output"])
        for item in target["support_examples"]
    )
    input_domain = str(target["input_domain"])
    output_domain = str(target["output_domain"])
    target_commitment = _digest(
        {
            "plan_commitment": plan_commitment,
            "target_index": int(target["target_index"]),
            "behavior_fingerprint": target["behavior_fingerprint"],
            "phase": "seed-committed-generated-target-instance-v1",
        }
    )

    with tempfile.TemporaryDirectory(prefix="agi-seed-generated-target-") as temporary:
        fresh_root = Path(temporary).resolve()
        _copy_persistent_state(root, fresh_root)
        prior_runs_copied = (fresh_root / ".continual" / "runs").exists()
        prior_episodes_copied = (fresh_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (fresh_root / ".continual" / "evidence").exists()
        if _tree_snapshot(fresh_root, source_ids) != source_tree:
            raise SeedCommittedTargetGeneratorError(
                "generated-target state-only reconstruction changed Candidate bytes"
            )
        if _trial_snapshot(fresh_root, source_ids) != source_trials:
            raise SeedCommittedTargetGeneratorError(
                "generated-target state-only reconstruction changed Candidate trials"
            )

        solved = synthesize_shallowest_verified_route(
            fresh_root,
            input_domain=input_domain,
            output_domain=output_domain,
            examples=examples,
            max_depth=max_solver_depth,
            max_candidates=64,
            max_search_nodes=max_solver_search_nodes,
            max_behavior_evaluations=max_solver_behavior_evaluations,
        )
        if solved.get("candidate_ids_supplied_by_caller") is not False:
            raise SeedCommittedTargetGeneratorError(
                "generated-target solver relied on caller Candidate IDs"
            )
        if int(solved["selected_chain_depth"]) < 2:
            raise SeedCommittedTargetGeneratorError(
                "generated target collapsed to a one-stage behavior"
            )

        selected_ids = [str(value) for value in solved["selected_candidate_ids"]]
        selected_items = tuple(_load_verified_macro_items(fresh_root, selected_ids))
        challenge_inputs = _challenge_inputs(input_domain, target_commitment)
        support_inputs = [item.input for item in examples]
        if any(value in support_inputs for value in challenge_inputs):
            raise SeedCommittedTargetGeneratorError(
                "generated target held-back challenge overlaps support inputs"
            )

        hidden_items = tuple(
            _load_verified_macro_items(
                fresh_root,
                [str(value) for value in target["generator_hidden_candidate_ids"]],
            )
        )
        expected_outputs = tuple(
            _execute_descriptor_chain(hidden_items, value) for value in challenge_inputs
        )
        challenge_outputs: list[dict[str, Any]] = []
        fresh_run_ids: list[str] = []
        for value, expected in zip(challenge_inputs, expected_outputs, strict=True):
            compiled_output = execute_program(solved["compiled_program"], value)
            runtime_output, run_id, refs = _execute_chain(fresh_root, selected_items, value)
            if compiled_output != expected or runtime_output != expected:
                raise SeedCommittedTargetGeneratorError(
                    "behavior-only rediscovered route failed generated held-back semantics"
                )
            if len(refs) != int(solved["selected_chain_depth"]):
                raise SeedCommittedTargetGeneratorError(
                    "generated held-back challenge executed the wrong learned-stage count"
                )
            fresh_run_ids.append(run_id)
            challenge_outputs.append(
                {"input": value, "expected": expected, "output": runtime_output}
            )
        if len(set(fresh_run_ids)) != len(fresh_run_ids):
            raise SeedCommittedTargetGeneratorError(
                "generated target held-back challenges reused an Engine run"
            )

        if _tree_snapshot(fresh_root, source_ids) != source_tree:
            raise SeedCommittedTargetGeneratorError(
                "generated target replay changed Candidate bytes"
            )
        if _trial_snapshot(fresh_root, source_ids) != source_trials:
            raise SeedCommittedTargetGeneratorError(
                "generated target replay changed Candidate trials"
            )

    return {
        "target_index": int(target["target_index"]),
        "target_commitment": target_commitment,
        "behavior_fingerprint": str(target["behavior_fingerprint"]),
        "input_domain": input_domain,
        "output_domain": output_domain,
        "support_examples": target["support_examples"],
        "generator_hidden_candidate_ids": list(target["generator_hidden_candidate_ids"]),
        "generator_hidden_depth": int(target["generator_hidden_depth"]),
        "generator_hidden_candidate_ids_supplied_to_solver": False,
        "solver_candidate_ids_supplied_by_caller": False,
        "solver_selected_candidate_ids": selected_ids,
        "solver_selected_chain_depth": int(solved["selected_chain_depth"]),
        "solver_shortest_type_chain_depth": int(solved["shortest_type_chain_depth"]),
        "solver_search_nodes_visited": int(solved["search_nodes_visited"]),
        "solver_search_node_budget": int(solved["search_node_budget"]),
        "solver_behavior_evaluations": int(solved["behavior_evaluations"]),
        "solver_behavior_evaluation_budget": int(solved["behavior_evaluation_budget"]),
        "heldback_challenge_outputs": challenge_outputs,
        "fresh_engine_runs": fresh_run_ids,
        "fresh_engine_runs_unique": len(set(fresh_run_ids)) == len(fresh_run_ids),
        "candidate_state_unchanged": True,
        "trial_state_unchanged": True,
        "prior_runs_copied": prior_runs_copied,
        "prior_episodes_copied": prior_episodes_copied,
        "prior_evidence_copied": prior_evidence_copied,
    }


def run_seed_committed_target_generator(
    root: Path,
    seed: str,
    *,
    max_target_attempts: int = 4,
    required_distinct_semantics: int = 2,
) -> dict[str, Any]:
    """Generate task semantics from the durable verified-skill graph, then solve them behavior-only.

    A prerequisite capability graph is constructed through the existing bounded adaptive composition
    milestone. The target generator then enumerates compatible non-repeating verified-skill routes under
    a finite budget, observes their semantics on a seed-committed support set, retains only nonconstant
    behaviors with exactly one route at their minimal behavior depth, and seed-ranks the resulting target
    semantics. The entire target plan, including support examples and hidden generator route identities,
    is committed before any target solver runs.

    Each target solver executes in a fresh state-only reconstruction. It receives only input/output
    behavior examples and type domains, never the generator's Candidate IDs. It must rediscover a
    multi-stage route under explicit search budgets and transfer the behavior to held-back inputs through
    fresh Engine runs while Candidate bytes and trial ledgers remain unchanged. At least two distinct
    generated semantic fingerprints are required, and an unrelated unsupported string-to-numeric target
    must remain fail-closed.

    Task semantics are therefore generated from the current durable skill graph and a committed seed,
    not selected from a named fixed goal family. The generator, skill graph, search, regression, runtime,
    and scoring are still repository-authored internal development machinery; this does not establish
    independent production evidence or AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    if (
        not isinstance(max_target_attempts, int)
        or isinstance(max_target_attempts, bool)
        or not 2 <= max_target_attempts <= 6
    ):
        raise ValueError("max_target_attempts must be an integer in [2, 6]")
    if (
        not isinstance(required_distinct_semantics, int)
        or isinstance(required_distinct_semantics, bool)
        or not 2 <= required_distinct_semantics <= max_target_attempts
    ):
        raise ValueError(
            "required_distinct_semantics must be an integer in [2, max_target_attempts]"
        )

    root = root.resolve()
    prerequisite = run_adaptive_depth_composition(root, f"{seed}:skill-graph-bootstrap")
    if not prerequisite.get("passed"):
        raise SeedCommittedTargetGeneratorError(
            "adaptive skill-graph prerequisite did not pass"
        )

    source_ids = _all_candidate_ids(root)
    source_tree = _tree_snapshot(root, source_ids)
    source_trials = _trial_snapshot(root, source_ids)
    if not source_ids or not all(source_trials.get(candidate_id) for candidate_id in source_ids):
        raise SeedCommittedTargetGeneratorError(
            "generated-target source graph lacks durable Candidate trial evidence"
        )

    max_generation_depth = 3
    max_generation_search_nodes = 512
    max_solver_depth = 4
    max_solver_search_nodes = 512
    max_solver_behavior_evaluations = 256
    plan = _precommit_target_plan(
        root,
        seed,
        max_target_attempts=max_target_attempts,
        max_generation_depth=max_generation_depth,
        max_generation_search_nodes=max_generation_search_nodes,
    )
    if len(plan["target_plan"]) < required_distinct_semantics:
        raise SeedCommittedTargetGeneratorError(
            "precommitted target plan contains too few target semantics"
        )

    records = [
        _execute_generated_target(
            root,
            target,
            plan_commitment=str(plan["target_plan_commitment"]),
            source_ids=source_ids,
            source_tree=source_tree,
            source_trials=source_trials,
            max_solver_depth=max_solver_depth,
            max_solver_search_nodes=max_solver_search_nodes,
            max_solver_behavior_evaluations=max_solver_behavior_evaluations,
        )
        for target in plan["target_plan"]
    ]
    semantic_fingerprints = {str(item["behavior_fingerprint"]) for item in records}
    if len(semantic_fingerprints) < required_distinct_semantics:
        raise SeedCommittedTargetGeneratorError(
            "precommitted generated targets did not expose enough distinct semantics"
        )

    unsupported_failed_closed = False
    try:
        synthesize_shallowest_verified_route(
            root,
            input_domain="string",
            output_domain="numeric",
            examples=(
                ProgramExample("generated-target-unseen-a", 1),
                ProgramExample("generated-target-unseen-b", 2),
            ),
            max_depth=3,
            max_candidates=64,
            max_search_nodes=64,
            max_behavior_evaluations=32,
        )
    except AdaptiveDepthCompositionError as exc:
        unsupported_failed_closed = "no unique behavior-matching verified route" in str(exc)
    if not unsupported_failed_closed:
        raise SeedCommittedTargetGeneratorError(
            "generated-target campaign widened an unrelated unsupported gap"
        )

    if _tree_snapshot(root, source_ids) != source_tree:
        raise SeedCommittedTargetGeneratorError(
            "generated-target campaign changed source Candidate bytes"
        )
    if _trial_snapshot(root, source_ids) != source_trials:
        raise SeedCommittedTargetGeneratorError(
            "generated-target campaign changed source Candidate trials"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "seed-committed-skill-graph-target-generator-v1",
        "seed": seed,
        "named_fixed_goal_family_used": False,
        "target_semantics_source": "seed-ranked verified durable skill graph",
        "target_plan_precommitted_before_solver_execution": True,
        "target_plan_commitment": str(plan["target_plan_commitment"]),
        "target_attempt_count": len(records),
        "max_target_attempts": max_target_attempts,
        "required_distinct_semantics": required_distinct_semantics,
        "distinct_semantic_fingerprint_count": len(semantic_fingerprints),
        "eligible_semantic_target_count": int(plan["eligible_semantic_target_count"]),
        "generation_search_nodes": int(plan["generation_search_nodes"]),
        "generation_search_node_budget": int(plan["generation_search_node_budget"]),
        "generator_hidden_candidate_ids_supplied_to_solver": False,
        "all_solver_calls_candidate_id_free": all(
            item["solver_candidate_ids_supplied_by_caller"] is False for item in records
        ),
        "all_generated_routes_multistage": all(
            int(item["solver_selected_chain_depth"]) >= 2 for item in records
        ),
        "all_fresh_engine_runs_unique": all(
            bool(item["fresh_engine_runs_unique"]) for item in records
        ),
        "all_candidate_state_unchanged": all(
            bool(item["candidate_state_unchanged"]) for item in records
        ),
        "all_trial_state_unchanged": all(
            bool(item["trial_state_unchanged"]) for item in records
        ),
        "source_candidate_state_unchanged": True,
        "source_trial_state_unchanged": True,
        "unsupported_gap_failed_closed": unsupported_failed_closed,
        "target_records": records,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded generated-target evidence only. Target semantics are derived from a "
            "seed-committed ordering over the current verified durable skill graph rather than a named "
            "fixed behavior family, and solvers receive behavior examples without generator Candidate "
            "IDs. The generator, skill graph, search, regression, runtime, and scoring remain repository-"
            "authored and do not establish independent production evidence or AGI."
        ),
    }
    if not all(
        (
            report["distinct_semantic_fingerprint_count"] >= required_distinct_semantics,
            report["generation_search_nodes"] <= report["generation_search_node_budget"],
            report["generator_hidden_candidate_ids_supplied_to_solver"] is False,
            report["all_solver_calls_candidate_id_free"],
            report["all_generated_routes_multistage"],
            report["all_fresh_engine_runs_unique"],
            report["all_candidate_state_unchanged"],
            report["all_trial_state_unchanged"],
            report["unsupported_gap_failed_closed"],
            all(not item["prior_runs_copied"] for item in records),
            all(not item["prior_episodes_copied"] for item in records),
            all(not item["prior_evidence_copied"] for item in records),
        )
    ):
        raise SeedCommittedTargetGeneratorError(
            "seed-committed generated-target aggregate invariant failed"
        )

    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "seed-committed-target-generator"
        / f"generated-targets-{_digest(seed)[:16]}.json",
        report,
    )
    return report
