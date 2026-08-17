from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, validate_program_descriptor
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.materialized_runtime_replay import _candidate_trial_snapshot
from agi.verified_macro_library_discovery import (
    VerifiedMacroLibraryError,
    _numeric_to_string_spec,
    _sequence_length_string_distractor_spec,
    _sequence_sum_spec,
    discover_verified_acquired_program_ids,
    synthesize_from_verified_macro_library,
)
from agi.verified_macro_synthesis import synthesize_verified_macro_program
from continual.acquired_program_tools import AcquiredProgramToolSpec


class VerifiedMacroRelevanceError(RuntimeError):
    pass


_DOMAINS = frozenset({"numeric", "string", "sequence", "boolean", "object"})


def _verified_macro_metadata(
    root: Path,
    *,
    max_library_candidates: int,
) -> tuple[dict[str, Any], ...]:
    candidate_ids = discover_verified_acquired_program_ids(
        root,
        max_candidates=max_library_candidates,
    )
    items: list[dict[str, Any]] = []
    for candidate_id in candidate_ids:
        path = root / ".continual" / "candidates" / candidate_id / "candidate.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise VerifiedMacroRelevanceError("verified macro Candidate must be an object")
        spec = AcquiredProgramToolSpec.from_candidate(raw)
        meta = validate_program_descriptor(spec.program)
        items.append(
            {
                "candidate_id": spec.candidate_id,
                "input_domain": str(meta["input_domain"]),
                "output_domain": str(meta["output_domain"]),
            }
        )
    return tuple(items)


def _bounded_domain_distances(
    edges: Sequence[tuple[str, str]],
    *,
    start: str,
    max_depth: int,
) -> dict[str, int]:
    distances = {start: 0}
    frontier = {start}
    for depth in range(1, max_depth + 1):
        next_frontier: set[str] = set()
        for source, target in edges:
            if source not in frontier or target in distances:
                continue
            distances[target] = depth
            next_frontier.add(target)
        if not next_frontier:
            break
        frontier = next_frontier
    return distances


def relevant_verified_macro_ids(
    root: Path,
    *,
    input_domain: str,
    output_domain: str,
    max_depth: int,
    max_library_candidates: int = 256,
    max_relevant_candidates: int = 32,
) -> dict[str, Any]:
    """Select only verified macros that can lie on a bounded typed path for the requested task.

    The filter is conservative: a Candidate is kept whenever its input type is reachable from the
    requested input and its output type can still reach the requested output within the total depth
    budget. Behavior is not inspected here; behavior examples remain the authority for route selection
    in ``synthesize_verified_macro_program``. The function fails closed instead of silently truncating
    either the durable library or the relevant subgraph.
    """

    if input_domain not in _DOMAINS or output_domain not in _DOMAINS:
        raise ValueError("input_domain and output_domain must be supported acquired-program domains")
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 1 <= max_depth <= 6:
        raise ValueError("max_depth must be an integer in [1, 6]")
    if (
        not isinstance(max_relevant_candidates, int)
        or isinstance(max_relevant_candidates, bool)
        or not 1 <= max_relevant_candidates <= 128
    ):
        raise ValueError("max_relevant_candidates must be an integer in [1, 128]")

    root = root.resolve()
    metadata = _verified_macro_metadata(
        root,
        max_library_candidates=max_library_candidates,
    )
    if not metadata:
        raise VerifiedMacroRelevanceError("verified acquired-program library is empty")

    edges = [
        (str(item["input_domain"]), str(item["output_domain"]))
        for item in metadata
    ]
    forward = _bounded_domain_distances(
        edges,
        start=input_domain,
        max_depth=max_depth,
    )
    reverse = _bounded_domain_distances(
        [(target, source) for source, target in edges],
        start=output_domain,
        max_depth=max_depth,
    )
    if output_domain not in forward or forward[output_domain] > max_depth:
        raise VerifiedMacroRelevanceError("verified macro library has no bounded typed route")

    relevant: list[str] = []
    pruned: list[str] = []
    for item in metadata:
        source = str(item["input_domain"])
        target = str(item["output_domain"])
        prefix = forward.get(source)
        suffix = reverse.get(target)
        if prefix is not None and suffix is not None and prefix + 1 + suffix <= max_depth:
            relevant.append(str(item["candidate_id"]))
        else:
            pruned.append(str(item["candidate_id"]))

    if not relevant:
        raise VerifiedMacroRelevanceError("typed route exists but no Candidate survived relevance filter")
    if len(relevant) > max_relevant_candidates:
        raise VerifiedMacroRelevanceError(
            "typed-relevant verified macro subgraph exceeds bound: "
            f"{len(relevant)}>{max_relevant_candidates}"
        )

    return {
        "library_candidate_ids": [str(item["candidate_id"]) for item in metadata],
        "library_candidate_count": len(metadata),
        "relevant_candidate_ids": relevant,
        "relevant_candidate_count": len(relevant),
        "pruned_candidate_ids": pruned,
        "pruned_candidate_count": len(pruned),
        "forward_domain_distances": forward,
        "reverse_domain_distances": reverse,
        "max_depth": max_depth,
    }


def synthesize_from_relevant_verified_macro_library(
    root: Path,
    *,
    input_domain: str,
    output_domain: str,
    examples: Sequence[ProgramExample],
    max_depth: int = 4,
    max_library_candidates: int = 256,
    max_relevant_candidates: int = 32,
) -> dict[str, Any]:
    """Prune by typed reachability, then behaviorally synthesize from the verified macro library."""

    selection = relevant_verified_macro_ids(
        root,
        input_domain=input_domain,
        output_domain=output_domain,
        max_depth=max_depth,
        max_library_candidates=max_library_candidates,
        max_relevant_candidates=max_relevant_candidates,
    )
    result = synthesize_verified_macro_program(
        root,
        candidate_ids=selection["relevant_candidate_ids"],
        input_domain=input_domain,
        output_domain=output_domain,
        examples=examples,
        max_depth=max_depth,
    )
    return {
        **result,
        **selection,
        "candidate_ids_supplied_by_caller": False,
        "typed_relevance_pruning_applied": True,
    }


def _numeric_abs_distractor_spec(index: int) -> dict[str, Any]:
    return {
        "name": f"pruning-numeric-abs-{index}",
        "input_domain": "numeric",
        "output_domain": "numeric",
        "examples": (
            ProgramExample(-3 - index, 3 + index),
            ProgramExample(0, 0),
            ProgramExample(5 + index, 5 + index),
        ),
        "probe": -(20 + index),
        "expected": 20 + index,
        "max_nodes": 2,
    }


def run_verified_macro_relevance_pruning(root: Path, seed: str) -> dict[str, Any]:
    """Show that typed relevance avoids a whole-library bound failure without hiding behavior checks.

    A useful sequence-sum→numeric-to-string route and a shorter wrong direct sequence-to-string route
    are promoted alongside five verified numeric→numeric distractors. With a four-Candidate whole-
    library cap, the prior unpruned discovery path must fail closed. The relevance path may inspect the
    bounded durable library but must pass only the three Candidates that can participate in a depth-two
    sequence→string route to behavior-guided synthesis, which still rejects the direct wrong route.

    This remains repository-authored internal scalability evidence, not independent production evidence
    or an AGI claim.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    sequence_sum = _promote_task(
        root,
        seed=f"{seed}:relevance-components",
        spec=_sequence_sum_spec(),
    )
    numeric_to_string = _promote_task(
        root,
        seed=f"{seed}:relevance-components",
        spec=_numeric_to_string_spec(),
    )
    distractor = _promote_task(
        root,
        seed=f"{seed}:relevance-components",
        spec=_sequence_length_string_distractor_spec(),
    )
    numeric_distractors = [
        _promote_task(
            root,
            seed=f"{seed}:irrelevant-{index}",
            spec=_numeric_abs_distractor_spec(index),
        )
        for index in range(5)
    ]
    all_generated_ids = [
        str(sequence_sum["candidate_id"]),
        str(numeric_to_string["candidate_id"]),
        str(distractor["candidate_id"]),
        *[str(item["candidate_id"]) for item in numeric_distractors],
    ]
    trials_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in all_generated_ids
    }
    if not all(trials_before.values()):
        raise VerifiedMacroRelevanceError("generated relevance library lacks regression evidence")

    planning_examples = (
        ProgramExample([10, -1], "9"),
        ProgramExample([2, 1, 0], "3"),
        ProgramExample([-8, 1], "-7"),
    )

    old_path_failed_on_library_bound = False
    try:
        synthesize_from_verified_macro_library(
            root,
            input_domain="sequence",
            output_domain="string",
            examples=planning_examples,
            max_depth=2,
            max_candidates=4,
        )
    except VerifiedMacroLibraryError as exc:
        if "library exceeds bound" not in str(exc):
            raise
        old_path_failed_on_library_bound = True
    if not old_path_failed_on_library_bound:
        raise VerifiedMacroRelevanceError("unpruned library unexpectedly fit the four-Candidate bound")

    synthesized = synthesize_from_relevant_verified_macro_library(
        root,
        input_domain="sequence",
        output_domain="string",
        examples=planning_examples,
        max_depth=2,
        max_library_candidates=32,
        max_relevant_candidates=4,
    )
    expected_relevant = {
        str(sequence_sum["candidate_id"]),
        str(numeric_to_string["candidate_id"]),
        str(distractor["candidate_id"]),
    }
    if set(synthesized["relevant_candidate_ids"]) != expected_relevant:
        raise VerifiedMacroRelevanceError("typed relevance retained an unexpected Candidate set")
    expected_selected = [
        str(sequence_sum["candidate_id"]),
        str(numeric_to_string["candidate_id"]),
    ]
    if synthesized["selected_candidate_ids"] != expected_selected:
        raise VerifiedMacroRelevanceError("behavior synthesis did not select sum then to-string")
    if synthesized["shortest_type_chain_depth"] != 1 or synthesized["selected_chain_depth"] != 2:
        raise VerifiedMacroRelevanceError("relevance campaign lacks required shorter wrong route")

    trials_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in all_generated_ids
    }
    if trials_after != trials_before:
        raise VerifiedMacroRelevanceError("relevance discovery changed Candidate trial evidence")

    numeric_distractor_ids = {str(item["candidate_id"]) for item in numeric_distractors}
    if not numeric_distractor_ids.issubset(set(synthesized["pruned_candidate_ids"])):
        raise VerifiedMacroRelevanceError("numeric distractors were not pruned from the depth-two route")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "verified-macro-relevance-pruning",
        "old_unpruned_path_failed_on_four_candidate_bound": old_path_failed_on_library_bound,
        "library_candidate_count": synthesized["library_candidate_count"],
        "relevant_candidate_count": synthesized["relevant_candidate_count"],
        "pruned_candidate_count": synthesized["pruned_candidate_count"],
        "relevant_candidate_ids": synthesized["relevant_candidate_ids"],
        "pruned_candidate_ids": synthesized["pruned_candidate_ids"],
        "numeric_distractor_ids": sorted(numeric_distractor_ids),
        "candidate_ids_supplied_by_caller": synthesized["candidate_ids_supplied_by_caller"],
        "typed_relevance_pruning_applied": synthesized["typed_relevance_pruning_applied"],
        "shortest_type_chain_depth": synthesized["shortest_type_chain_depth"],
        "selected_chain_depth": synthesized["selected_chain_depth"],
        "selected_candidate_ids": synthesized["selected_candidate_ids"],
        "candidate_trial_state_unchanged": trials_after == trials_before,
        "claim_boundary": (
            "Internal bounded scalability evidence only. Type-reachability pruning avoided an explicit "
            "whole-library bound failure while preserving behavior-guided route selection and immutable "
            "Candidate trial evidence. The library, tasks, bounds, evaluator, and runtime are all "
            "repository-authored; this does not establish AGI or independent production evidence."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "verified-macro-relevance-pruning"
        / f"relevance-pruning-{_digest(seed)[:16]}.json",
        report,
    )
    return report
