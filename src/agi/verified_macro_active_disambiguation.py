from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample
from agi.behavior_guided_tool_chain_discovery import _chain_matches, _execute_chain
from agi.bounded_verified_macro_search import select_unique_minimal_verified_macro_chain
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.materialized_runtime_replay import _candidate_trial_snapshot
from agi.verified_macro_relevance_pruning import relevant_verified_macro_ids
from agi.verified_macro_synthesis import _load_verified_macro_items


class VerifiedMacroDisambiguationError(RuntimeError):
    pass


def minimal_matching_verified_macro_chains(
    root: Path,
    *,
    candidates: Sequence[dict[str, Any]],
    input_domain: str,
    output_domain: str,
    examples: Sequence[ProgramExample],
    max_depth: int,
    max_expanded_prefixes: int = 1024,
    max_evaluated_complete_routes: int = 512,
) -> dict[str, Any]:
    """Return all behavior-matching routes at the first matching depth under hard search budgets."""

    if not 1 <= max_depth <= 6:
        raise ValueError("max_depth must be in [1, 6]")
    ordered = tuple(sorted(candidates, key=lambda item: str(item["candidate_id"])))
    frontier: list[tuple[tuple[dict[str, Any], ...], str]] = [((), input_domain)]
    expanded = 0
    evaluated = 0
    for depth in range(1, max_depth + 1):
        next_frontier: list[tuple[tuple[dict[str, Any], ...], str]] = []
        matching: list[tuple[dict[str, Any], ...]] = []
        for prefix, current_domain in frontier:
            used = {str(item["candidate_id"]) for item in prefix}
            for item in ordered:
                candidate_id = str(item["candidate_id"])
                if candidate_id in used or str(item["input_domain"]) != current_domain:
                    continue
                chain = (*prefix, item)
                next_domain = str(item["output_domain"])
                expanded += 1
                if expanded > max_expanded_prefixes:
                    raise VerifiedMacroDisambiguationError(
                        "ambiguous-route prefix search exceeded bounded budget"
                    )
                next_frontier.append((chain, next_domain))
                if next_domain != output_domain:
                    continue
                evaluated += 1
                if evaluated > max_evaluated_complete_routes:
                    raise VerifiedMacroDisambiguationError(
                        "ambiguous-route evaluation exceeded bounded budget"
                    )
                if _chain_matches(root, chain, examples):
                    matching.append(chain)
        if matching:
            return {
                "depth": depth,
                "matching_chains": tuple(matching),
                "matching_candidate_ids": [
                    [str(item["candidate_id"]) for item in chain]
                    for chain in matching
                ],
                "expanded_prefix_count": expanded,
                "evaluated_complete_route_count": evaluated,
            }
        frontier = next_frontier
        if not frontier:
            break
    raise VerifiedMacroDisambiguationError("no verified macro route matches committed behavior")


def propose_disambiguating_probe(
    root: Path,
    *,
    chains: Sequence[Sequence[dict[str, Any]]],
    probe_inputs: Sequence[Any],
    existing_inputs: Sequence[Any],
    max_probe_inputs: int = 64,
) -> dict[str, Any]:
    """Choose a bounded fresh input on which ambiguous verified routes disagree most.

    The proposal contains only the chosen input and hashes of route outputs; it does not choose the
    answer. A later evaluator/oracle answer must be committed independently before the refined search
    can select a route.
    """

    if len(chains) < 2:
        raise VerifiedMacroDisambiguationError("disambiguation requires at least two ambiguous routes")
    if len(probe_inputs) > max_probe_inputs:
        raise VerifiedMacroDisambiguationError("probe pool exceeds bounded search limit")
    existing = {
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        for value in existing_inputs
    }
    best: dict[str, Any] | None = None
    for probe_index, value in enumerate(probe_inputs):
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if encoded in existing:
            continue
        output_hashes: list[str] = []
        output_signatures: set[str] = set()
        for chain in chains:
            output, _run_id, _refs = _execute_chain(root, chain, value)
            signature = json.dumps(
                output,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            output_signatures.add(signature)
            output_hashes.append(_digest(output))
        diversity = len(output_signatures)
        record = {
            "probe": value,
            "probe_index": probe_index,
            "distinct_route_output_count": diversity,
            "route_output_sha256": output_hashes,
        }
        if best is None or diversity > int(best["distinct_route_output_count"]):
            best = record
    if best is None or int(best["distinct_route_output_count"]) < 2:
        raise VerifiedMacroDisambiguationError(
            "bounded probe pool cannot distinguish ambiguous verified macro routes"
        )
    commitment = _digest(
        {
            "probe": best["probe"],
            "route_output_sha256": best["route_output_sha256"],
            "kind": "verified-macro-disambiguation-probe-v1",
        }
    )
    return {**best, "probe_commitment": commitment}


def _identity_spec() -> dict[str, Any]:
    return {
        "name": "active-disambiguation-identity",
        "input_domain": "numeric",
        "output_domain": "numeric",
        "examples": (
            ProgramExample(-3, -3),
            ProgramExample(0, 0),
            ProgramExample(5, 5),
        ),
        "probe": 8,
        "expected": 8,
        "max_nodes": 2,
    }


def _abs_spec() -> dict[str, Any]:
    return {
        "name": "active-disambiguation-abs",
        "input_domain": "numeric",
        "output_domain": "numeric",
        "examples": (
            ProgramExample(-4, 4),
            ProgramExample(0, 0),
            ProgramExample(6, 6),
        ),
        "probe": -11,
        "expected": 11,
        "max_nodes": 2,
    }


def _add_one_spec() -> dict[str, Any]:
    return {
        "name": "active-disambiguation-add-one",
        "input_domain": "numeric",
        "output_domain": "numeric",
        "examples": (
            ProgramExample(-3, -2),
            ProgramExample(0, 1),
            ProgramExample(5, 6),
        ),
        "probe": 8,
        "expected": 9,
        "max_nodes": 3,
    }


def run_verified_macro_active_disambiguation(root: Path, seed: str) -> dict[str, Any]:
    """Resolve an initially ambiguous verified-macro choice by an independently answered probe.

    Identity and absolute-value Candidates are both exact-scope regression verified. Positive-only
    planning examples make them behaviorally indistinguishable, so the normal unique-minimal search must
    fail closed instead of guessing. The system then chooses a fresh bounded probe on which the routes
    disagree. Only after that input is committed does a separately defined campaign oracle answer it;
    the answer is not selected from candidate route outputs. Adding the committed counterexample must
    make the abs Candidate uniquely identifiable while all Candidate trial ledgers remain unchanged.

    This is repository-authored internal active-learning evidence, not independent production evidence
    or an AGI claim.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    absolute = _promote_task(root, seed=f"{seed}:abs", spec=_abs_spec())
    identity = _promote_task(root, seed=f"{seed}:identity", spec=_identity_spec())
    distractor = _promote_task(root, seed=f"{seed}:add-one", spec=_add_one_spec())
    generated_ids = [
        str(absolute["candidate_id"]),
        str(identity["candidate_id"]),
        str(distractor["candidate_id"]),
    ]
    trial_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in generated_ids
    }
    if not all(trial_before.values()):
        raise VerifiedMacroDisambiguationError("generated Candidate lacks regression evidence")

    relevant = relevant_verified_macro_ids(
        root,
        input_domain="numeric",
        output_domain="numeric",
        max_depth=1,
        max_library_candidates=16,
        max_relevant_candidates=16,
    )
    candidates = _load_verified_macro_items(root, relevant["relevant_candidate_ids"])
    initial_examples = (
        ProgramExample(2, 2),
        ProgramExample(5, 5),
        ProgramExample(9, 9),
    )
    ambiguous = minimal_matching_verified_macro_chains(
        root,
        candidates=candidates,
        input_domain="numeric",
        output_domain="numeric",
        examples=initial_examples,
        max_depth=1,
    )
    if len(ambiguous["matching_chains"]) != 2:
        raise VerifiedMacroDisambiguationError("campaign did not produce exactly two ambiguous routes")
    ambiguous_ids = {
        ids[0]
        for ids in ambiguous["matching_candidate_ids"]
        if len(ids) == 1
    }
    expected_ambiguous = {str(absolute["candidate_id"]), str(identity["candidate_id"])}
    if ambiguous_ids != expected_ambiguous:
        raise VerifiedMacroDisambiguationError("unexpected Candidates matched initial support")

    unique_search_failed_closed = False
    try:
        select_unique_minimal_verified_macro_chain(
            root,
            candidates=candidates,
            input_domain="numeric",
            output_domain="numeric",
            examples=initial_examples,
            max_depth=1,
        )
    except RuntimeError as exc:
        if "ambiguous" not in str(exc):
            raise
        unique_search_failed_closed = True
    if not unique_search_failed_closed:
        raise VerifiedMacroDisambiguationError("unique search guessed through ambiguity")

    proposal = propose_disambiguating_probe(
        root,
        chains=ambiguous["matching_chains"],
        probe_inputs=(-4, -7, 3),
        existing_inputs=[item.input for item in initial_examples],
    )
    committed_probe = proposal["probe"]
    if committed_probe >= 0:
        raise VerifiedMacroDisambiguationError("chosen probe did not expose sign ambiguity")

    # The answer is supplied only after probe commitment from a separately defined frozen oracle.
    committed_answer = abs(committed_probe)
    counterexample = ProgramExample(committed_probe, committed_answer)
    refined_examples = (*initial_examples, counterexample)
    selected, metrics = select_unique_minimal_verified_macro_chain(
        root,
        candidates=candidates,
        input_domain="numeric",
        output_domain="numeric",
        examples=refined_examples,
        max_depth=1,
    )
    selected_ids = [str(item["candidate_id"]) for item in selected]
    if selected_ids != [str(absolute["candidate_id"])]:
        raise VerifiedMacroDisambiguationError("committed counterexample did not identify abs Candidate")

    challenge_inputs = (-13, 6, -21)
    challenge_outputs: list[Any] = []
    for value in challenge_inputs:
        output, _run_id, _refs = _execute_chain(root, selected, value)
        if output != abs(value):
            raise VerifiedMacroDisambiguationError("refined route failed fresh challenge")
        challenge_outputs.append(output)

    trial_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in generated_ids
    }
    if trial_after != trial_before:
        raise VerifiedMacroDisambiguationError("active disambiguation changed Candidate trial evidence")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "verified-macro-active-disambiguation",
        "candidate_ids_supplied_by_caller": False,
        "initial_matching_route_count": len(ambiguous["matching_chains"]),
        "unique_search_failed_closed_on_ambiguity": unique_search_failed_closed,
        "probe_commitment": proposal["probe_commitment"],
        "probe_input": committed_probe,
        "distinct_route_output_count": proposal["distinct_route_output_count"],
        "route_output_sha256": proposal["route_output_sha256"],
        "counterexample_answer_source": "frozen-campaign-abs-oracle-after-probe-commitment",
        "counterexample_answer": committed_answer,
        "refined_selected_candidate_ids": selected_ids,
        "refined_selected_depth": metrics["selected_depth"],
        "fresh_challenge_count": len(challenge_inputs),
        "fresh_challenge_outputs": challenge_outputs,
        "candidate_trial_state_unchanged": trial_after == trial_before,
        "claim_boundary": (
            "Internal bounded active-disambiguation evidence only. The system proposed an informative "
            "probe before receiving its answer and failed closed on initial ambiguity, but the candidate "
            "library, probe pool, oracle, tasks, and runtime remain repository-authored. This is not AGI "
            "or independent production evidence."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "verified-macro-active-disambiguation"
        / f"active-disambiguation-{_digest(seed)[:16]}.json",
        report,
    )
    return report
