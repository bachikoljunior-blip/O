from __future__ import annotations

import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

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
from agi.cross_domain_seed_gap_acquisition import _string_challenges
from agi.durable_state_rehydration import _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.sequence_seed_gap_acquisition import run_sequence_seed_gap_acquisition
from agi.verified_macro_library_discovery import discover_verified_acquired_program_ids
from agi.verified_macro_synthesis import _load_verified_macro_items


class DynamicSkillGraphInputDomainError(RuntimeError):
    pass


_PROBEABLE_DOMAINS = frozenset({"numeric", "string", "sequence"})


def _observed_generator_input_domains(
    items: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    """Derive target-generator start domains from the verified durable library."""

    observed = {
        str(item.get("input_domain"))
        for item in items
        if str(item.get("input_domain")) in _PROBEABLE_DOMAINS
    }
    return tuple(sorted(observed))


def _domain_planning_inputs(domain: str, seed: str) -> tuple[Any, ...]:
    if domain in {"numeric", "sequence"}:
        return tuple(_planning_inputs(domain, seed))
    if domain == "string":
        token = _digest(
            {"seed": seed, "domain": domain, "phase": "dynamic-skillgraph-planning-v1"}
        )
        values = (
            f"a{token[:5]}Z",
            f"Mix-{token[5:10]}q",
            f"novel-{token[10:15]}-Xy",
        )
        if len(set(values)) != len(values):
            raise DynamicSkillGraphInputDomainError("string planning inputs are not unique")
        return values
    raise DynamicSkillGraphInputDomainError(f"unsupported planning domain: {domain}")


def _domain_challenge_inputs(domain: str, commitment: str) -> tuple[Any, ...]:
    if domain in {"numeric", "sequence"}:
        return tuple(_challenge_inputs(domain, commitment))
    if domain == "string":
        return tuple(_string_challenges(commitment))
    raise DynamicSkillGraphInputDomainError(f"unsupported challenge domain: {domain}")


def _behavior_record(
    chain: tuple[dict[str, Any], ...],
    *,
    support_inputs: Mapping[str, tuple[Any, ...]],
) -> dict[str, Any]:
    input_domain = str(chain[0]["input_domain"])
    output_domain = str(chain[-1]["output_domain"])
    inputs = tuple(support_inputs[input_domain])
    outputs = tuple(_execute_descriptor_chain(chain, value) for value in inputs)
    fingerprint = _digest(
        {
            "input_domain": input_domain,
            "output_domain": output_domain,
            "observations": [
                {"input": value, "output": output}
                for value, output in zip(inputs, outputs, strict=True)
            ],
            "phase": "dynamic-skillgraph-target-behavior-v1",
        }
    )
    return {
        "candidate_ids": tuple(str(item["candidate_id"]) for item in chain),
        "depth": len(chain),
        "input_domain": input_domain,
        "output_domain": output_domain,
        "support_inputs": inputs,
        "support_outputs": outputs,
        "behavior_fingerprint": fingerprint,
        "nonconstant": len({_digest(value) for value in outputs}) > 1,
    }


def _precommit_dynamic_target_plan(
    root: Path,
    seed: str,
    *,
    max_generation_depth: int = 3,
    max_generation_search_nodes: int = 4096,
) -> dict[str, Any]:
    """Build one committed behavior target per observed probeable library input domain."""

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    library_ids = discover_verified_acquired_program_ids(root, max_candidates=192)
    if not library_ids:
        raise DynamicSkillGraphInputDomainError("verified skill graph is empty")
    items = tuple(_load_verified_macro_items(root, library_ids))
    input_domains = _observed_generator_input_domains(items)
    if len(input_domains) < 2:
        raise DynamicSkillGraphInputDomainError(
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
        raise DynamicSkillGraphInputDomainError(
            "observed input domains lack unique multistage target semantics: " + ",".join(missing)
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
    selected_targets: list[dict[str, Any]] = []
    for domain in source_order:
        ranked = sorted(
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
        selected_targets.append(dict(ranked[0]))

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
    return {
        "library_candidate_ids": list(library_ids),
        "observed_generator_input_domains": list(input_domains),
        "precommitted_source_order": source_order,
        "generation_search_nodes": search_nodes,
        "generation_search_node_budget": max_generation_search_nodes,
        "eligible_semantic_target_count": len(eligible),
        "target_plan": public_targets,
        "target_plan_commitment": plan_commitment,
        "target_plan_precommitted_before_solver_execution": True,
    }


def _execute_dynamic_target(
    root: Path,
    target: Mapping[str, Any],
    *,
    plan_commitment: str,
    source_ids: Sequence[str],
    source_tree: Mapping[str, str],
    source_trials: Mapping[str, Mapping[str, str]],
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
            "phase": "dynamic-skillgraph-target-instance-v1",
        }
    )

    with tempfile.TemporaryDirectory(prefix="agi-dynamic-skillgraph-target-") as temporary:
        fresh_root = Path(temporary).resolve()
        _copy_persistent_state(root, fresh_root)
        prior_runs_copied = (fresh_root / ".continual" / "runs").exists()
        prior_episodes_copied = (fresh_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (fresh_root / ".continual" / "evidence").exists()
        if _tree_snapshot(fresh_root, source_ids) != source_tree:
            raise DynamicSkillGraphInputDomainError(
                "state-only reconstruction changed Candidate bytes"
            )
        if _trial_snapshot(fresh_root, source_ids) != source_trials:
            raise DynamicSkillGraphInputDomainError(
                "state-only reconstruction changed Candidate trials"
            )

        solved = synthesize_shallowest_verified_route(
            fresh_root,
            input_domain=input_domain,
            output_domain=output_domain,
            examples=examples,
            max_depth=4,
            max_candidates=192,
            max_search_nodes=8192,
            max_behavior_evaluations=4096,
        )
        if solved.get("candidate_ids_supplied_by_caller") is not False:
            raise DynamicSkillGraphInputDomainError(
                "dynamic target solver relied on caller Candidate IDs"
            )
        if int(solved["selected_chain_depth"]) < 2:
            raise DynamicSkillGraphInputDomainError(
                "dynamic target collapsed to a one-stage behavior"
            )

        selected_ids = [str(value) for value in solved["selected_candidate_ids"]]
        selected_items = tuple(_load_verified_macro_items(fresh_root, selected_ids))
        hidden_items = tuple(
            _load_verified_macro_items(
                fresh_root,
                [str(value) for value in target["generator_hidden_candidate_ids"]],
            )
        )
        challenges = _domain_challenge_inputs(input_domain, target_commitment)
        support_inputs = [item.input for item in examples]
        if any(value in support_inputs for value in challenges):
            raise DynamicSkillGraphInputDomainError(
                "held-back challenge overlaps committed support inputs"
            )
        expected_outputs = tuple(
            _execute_descriptor_chain(hidden_items, value) for value in challenges
        )

        fresh_run_ids: list[str] = []
        challenge_outputs: list[dict[str, Any]] = []
        for value, expected in zip(challenges, expected_outputs, strict=True):
            compiled_output = execute_program(solved["compiled_program"], value)
            runtime_output, run_id, refs = _execute_chain(fresh_root, selected_items, value)
            if compiled_output != expected or runtime_output != expected:
                raise DynamicSkillGraphInputDomainError(
                    "behavior-only route failed held-back semantics"
                )
            if len(refs) != int(solved["selected_chain_depth"]):
                raise DynamicSkillGraphInputDomainError(
                    "held-back replay used wrong learned-stage count"
                )
            fresh_run_ids.append(run_id)
            challenge_outputs.append(
                {"input": value, "expected": expected, "output": runtime_output}
            )
        if len(set(fresh_run_ids)) != len(fresh_run_ids):
            raise DynamicSkillGraphInputDomainError(
                "held-back challenges reused a supposedly fresh Engine run"
            )
        if _tree_snapshot(fresh_root, source_ids) != source_tree:
            raise DynamicSkillGraphInputDomainError("fresh replay changed Candidate bytes")
        if _trial_snapshot(fresh_root, source_ids) != source_trials:
            raise DynamicSkillGraphInputDomainError("fresh replay changed Candidate trials")

    return {
        "target_index": int(target["target_index"]),
        "target_commitment": target_commitment,
        "behavior_fingerprint": str(target["behavior_fingerprint"]),
        "input_domain": input_domain,
        "output_domain": output_domain,
        "generator_hidden_candidate_ids": list(target["generator_hidden_candidate_ids"]),
        "generator_hidden_depth": int(target["generator_hidden_depth"]),
        "generator_hidden_candidate_ids_supplied_to_solver": False,
        "solver_candidate_ids_supplied_by_caller": False,
        "solver_selected_candidate_ids": selected_ids,
        "solver_selected_chain_depth": int(solved["selected_chain_depth"]),
        "heldback_challenge_outputs": challenge_outputs,
        "fresh_engine_runs": fresh_run_ids,
        "fresh_engine_runs_unique": len(set(fresh_run_ids)) == len(fresh_run_ids),
        "candidate_state_unchanged": True,
        "trial_state_unchanged": True,
        "prior_runs_copied": prior_runs_copied,
        "prior_episodes_copied": prior_episodes_copied,
        "prior_evidence_copied": prior_evidence_copied,
    }


def run_dynamic_skillgraph_input_domains(root: Path, seed: str) -> dict[str, Any]:
    """Generate and transfer one committed target from every observed probeable skill-graph input domain.

    Unlike the earlier generator's fixed numeric/sequence start set, this campaign reconstructs the
    current verified durable skill graph after a three-domain acquisition prerequisite, derives its
    probeable input-domain set from the actual verified Candidates, and precommits one unique multistage
    behavior target for every observed numeric, string, and sequence source before any solver runs. Each
    solver gets behavior examples and types only, never the hidden generator route IDs, and must transfer
    to fresh held-back inputs through fresh Engine runs while Candidate bytes and trials stay immutable.

    This extends target-source diversity and removes a fixed source-domain list from semantic selection,
    but the probeable domain types, target generation, durable skill graph, search, runtime, scoring, and
    evaluator remain bounded repository-authored machinery. It is internal development evidence, not
    independent production evidence and not AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    adaptive = run_adaptive_depth_composition(root, f"{seed}:adaptive-bootstrap")
    if not adaptive.get("passed"):
        raise DynamicSkillGraphInputDomainError("adaptive prerequisite did not pass")
    three_domain = run_sequence_seed_gap_acquisition(
        root,
        f"{seed}:three-domain-bootstrap",
        max_target_attempts=3,
    )
    if not three_domain.get("passed"):
        raise DynamicSkillGraphInputDomainError("three-domain prerequisite did not pass")

    source_ids = _all_candidate_ids(root)
    source_tree = _tree_snapshot(root, source_ids)
    source_trials = _trial_snapshot(root, source_ids)
    if not source_ids or not all(source_trials.get(candidate_id) for candidate_id in source_ids):
        raise DynamicSkillGraphInputDomainError(
            "dynamic target source graph lacks durable Candidate trial evidence"
        )

    plan = _precommit_dynamic_target_plan(root, seed)
    observed_domains = list(plan["observed_generator_input_domains"])
    if set(observed_domains) != set(_PROBEABLE_DOMAINS):
        raise DynamicSkillGraphInputDomainError(
            "three-domain prerequisite did not expose numeric, string, and sequence target sources"
        )
    target_domains = [str(item["input_domain"]) for item in plan["target_plan"]]
    if set(target_domains) != set(observed_domains) or len(target_domains) != len(set(target_domains)):
        raise DynamicSkillGraphInputDomainError(
            "precommitted target plan did not select exactly one target per observed input domain"
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
        raise DynamicSkillGraphInputDomainError("dynamic target campaign changed source Candidate bytes")
    if _trial_snapshot(root, source_ids) != source_trials:
        raise DynamicSkillGraphInputDomainError("dynamic target campaign changed source Candidate trials")

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
        raise DynamicSkillGraphInputDomainError(
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
        "string_source_target_exercised": any(
            record["input_domain"] == "string" for record in records
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
        "unrelated_cross_source_control_failed_closed": control_failed_closed,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded dynamic skill-graph target-source evidence only. Target generation now "
            "derives numeric, string, and sequence source domains from the actually verified durable "
            "Candidate graph and exercises one committed multistage target from every observed source "
            "without exposing hidden generator Candidate IDs. Probeable domain types, generation, search, "
            "runtime, regression, and scoring remain repository-authored; this does not establish open-"
            "domain target generation, independent production evaluation, or AGI."
        ),
    }
    if not all(
        (
            report["fixed_numeric_sequence_start_set_used"] is False,
            report["target_plan_precommitted_before_solver_execution"],
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
        raise DynamicSkillGraphInputDomainError(
            "dynamic skill-graph input-domain aggregate invariant failed"
        )
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "dynamic-skillgraph-input-domains"
        / f"dynamic-inputs-{_digest(seed)[:16]}.json",
        report,
    )
    return report
