from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program, validate_program_descriptor
from agi.behavior_guided_tool_chain_discovery import _chain_matches, _execute_chain, _typed_chains
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.learned_tool_chain_compilation import _compile_chain_program
from agi.materialized_runtime_replay import _candidate_trial_snapshot
from agi.verified_macro_library_discovery import _numeric_to_string_spec
from agi.verified_macro_relevance_pruning import relevant_verified_macro_ids
from agi.verified_macro_synthesis import _load_verified_macro_items


class BoundedVerifiedMacroSearchError(RuntimeError):
    pass


def select_unique_minimal_verified_macro_chain(
    root: Path,
    *,
    candidates: Sequence[dict[str, Any]],
    input_domain: str,
    output_domain: str,
    examples: Sequence[ProgramExample],
    max_depth: int,
    max_expanded_prefixes: int = 4096,
    max_evaluated_complete_routes: int = 1024,
) -> tuple[tuple[dict[str, Any], ...], dict[str, int]]:
    """Search verified learned-tool routes depth-first by minimal chain length with hard budgets.

    Unlike the legacy helper that materializes every no-repeat permutation through ``max_depth`` before
    behavior is considered, this bounded breadth-first search expands only type-compatible prefixes and
    stops immediately after all complete routes at the first behavior-matching depth have been checked.
    It preserves the existing fail-closed ambiguity rule: exactly one minimal route must match committed
    behavior examples. Hard prefix and complete-route budgets prevent a dense same-domain macro library
    from turning bounded planning into an accidental factorial workload.
    """

    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 1 <= max_depth <= 6:
        raise ValueError("max_depth must be an integer in [1, 6]")
    if (
        not isinstance(max_expanded_prefixes, int)
        or isinstance(max_expanded_prefixes, bool)
        or max_expanded_prefixes < 1
    ):
        raise ValueError("max_expanded_prefixes must be a positive integer")
    if (
        not isinstance(max_evaluated_complete_routes, int)
        or isinstance(max_evaluated_complete_routes, bool)
        or max_evaluated_complete_routes < 1
    ):
        raise ValueError("max_evaluated_complete_routes must be a positive integer")
    normalized_examples = tuple(examples)
    if len(normalized_examples) < 2:
        raise ValueError("at least two behavior examples are required")

    ordered = tuple(sorted(candidates, key=lambda item: str(item["candidate_id"])))
    candidate_ids = [str(item["candidate_id"]) for item in ordered]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise BoundedVerifiedMacroSearchError("candidate pool contains duplicate identities")

    frontier: list[tuple[tuple[dict[str, Any], ...], str]] = [((), input_domain)]
    expanded_prefixes = 0
    evaluated_complete_routes = 0
    type_complete_routes = 0

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
                expanded_prefixes += 1
                if expanded_prefixes > max_expanded_prefixes:
                    raise BoundedVerifiedMacroSearchError(
                        "verified macro prefix expansion exceeded bounded search budget"
                    )
                next_frontier.append((chain, next_domain))
                if next_domain != output_domain:
                    continue
                type_complete_routes += 1
                evaluated_complete_routes += 1
                if evaluated_complete_routes > max_evaluated_complete_routes:
                    raise BoundedVerifiedMacroSearchError(
                        "verified macro complete-route evaluation exceeded bounded search budget"
                    )
                if _chain_matches(root, chain, normalized_examples):
                    matching.append(chain)

        if matching:
            if len(matching) != 1:
                raise BoundedVerifiedMacroSearchError(
                    "committed behavior is ambiguous across minimal verified macro routes"
                )
            return matching[0], {
                "selected_depth": depth,
                "expanded_prefix_count": expanded_prefixes,
                "evaluated_complete_route_count": evaluated_complete_routes,
                "type_complete_route_count": type_complete_routes,
            }
        frontier = next_frontier
        if not frontier:
            break

    raise BoundedVerifiedMacroSearchError(
        "no bounded verified macro route matches committed behavior"
    )


def synthesize_with_bounded_verified_macro_search(
    root: Path,
    *,
    input_domain: str,
    output_domain: str,
    examples: Sequence[ProgramExample],
    max_depth: int = 4,
    max_library_candidates: int = 256,
    max_relevant_candidates: int = 64,
    max_expanded_prefixes: int = 4096,
    max_evaluated_complete_routes: int = 1024,
) -> dict[str, Any]:
    """Discover relevant exact-scope verified macros and compile the unique minimal behavior route."""

    root = root.resolve()
    selection = relevant_verified_macro_ids(
        root,
        input_domain=input_domain,
        output_domain=output_domain,
        max_depth=max_depth,
        max_library_candidates=max_library_candidates,
        max_relevant_candidates=max_relevant_candidates,
    )
    relevant_ids = list(selection["relevant_candidate_ids"])
    macros = _load_verified_macro_items(root, relevant_ids)
    trial_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in relevant_ids
    }
    if not all(trial_before.values()):
        raise BoundedVerifiedMacroSearchError("verified macro lacks durable regression evidence")

    selected, metrics = select_unique_minimal_verified_macro_chain(
        root,
        candidates=macros,
        input_domain=input_domain,
        output_domain=output_domain,
        examples=examples,
        max_depth=max_depth,
        max_expanded_prefixes=max_expanded_prefixes,
        max_evaluated_complete_routes=max_evaluated_complete_routes,
    )
    program = _compile_chain_program(selected, examples)
    meta = validate_program_descriptor(program)
    for example in examples:
        source_output, _run_id, _refs = _execute_chain(root, selected, example.input)
        compiled_output = execute_program(program, example.input)
        if source_output != example.output or compiled_output != source_output:
            raise BoundedVerifiedMacroSearchError(
                "bounded verified macro compilation changed committed behavior"
            )

    trial_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in relevant_ids
    }
    if trial_after != trial_before:
        raise BoundedVerifiedMacroSearchError("bounded macro search changed Candidate trial evidence")

    return {
        **selection,
        **metrics,
        "candidate_ids_supplied_by_caller": False,
        "selected_candidate_ids": [str(item["candidate_id"]) for item in selected],
        "compiled_program": program,
        "compiled_program_nodes": int(meta["nodes"]),
        "compiled_program_depth": int(meta["depth"]),
        "candidate_trial_state_unchanged": True,
    }


def _abs_spec() -> dict[str, Any]:
    return {
        "name": "bounded-search-abs",
        "input_domain": "numeric",
        "output_domain": "numeric",
        "examples": (
            ProgramExample(-4, 4),
            ProgramExample(0, 0),
            ProgramExample(5, 5),
        ),
        "probe": -17,
        "expected": 17,
        "max_nodes": 2,
    }


def _add_one_spec(index: int) -> dict[str, Any]:
    return {
        "name": f"bounded-search-add-one-{index}",
        "input_domain": "numeric",
        "output_domain": "numeric",
        "examples": (
            ProgramExample(-4, -3),
            ProgramExample(0, 1),
            ProgramExample(5, 6),
        ),
        "probe": 9,
        "expected": 10,
        "max_nodes": 3,
    }


def run_bounded_verified_macro_search(root: Path, seed: str) -> dict[str, Any]:
    """Falsify factorial route materialization on a dense but behavior-simple verified macro library.

    The target is decimal-string(abs(x)). A direct numeric-to-string learned tool is type-correct but
    wrong on negative support. One verified abs transform and twelve independently regression-promoted
    add-one distractors all keep the numeric domain, so the full legacy permutation materialization
    through depth four contains more than a thousand complete typed routes. The bounded search must
    inspect only depth one and depth two, uniquely select abs→to-string, compile the route, and preserve
    every Candidate trial ledger.

    This is internal bounded planning/scalability evidence only, not independent production evidence or
    an AGI claim.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    absolute = _promote_task(
        root,
        seed=f"{seed}:components",
        spec=_abs_spec(),
    )
    to_string = _promote_task(
        root,
        seed=f"{seed}:components",
        spec=_numeric_to_string_spec(),
    )
    distractors = [
        _promote_task(
            root,
            seed=f"{seed}:distractor-{index}",
            spec=_add_one_spec(index),
        )
        for index in range(12)
    ]
    generated_ids = [
        str(absolute["candidate_id"]),
        str(to_string["candidate_id"]),
        *[str(item["candidate_id"]) for item in distractors],
    ]
    trials_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in generated_ids
    }
    if not all(trials_before.values()):
        raise BoundedVerifiedMacroSearchError("generated search component lacks regression evidence")

    planning_examples = (
        ProgramExample(-11, "11"),
        ProgramExample(3, "3"),
        ProgramExample(-7, "7"),
    )
    macros = _load_verified_macro_items(root, generated_ids)
    legacy_routes = _typed_chains(
        macros,
        input_domain="numeric",
        output_domain="string",
        max_depth=4,
    )
    legacy_route_count = len(legacy_routes)
    if legacy_route_count <= 1000:
        raise BoundedVerifiedMacroSearchError("dense search campaign lacks a large legacy route set")

    synthesized = synthesize_with_bounded_verified_macro_search(
        root,
        input_domain="numeric",
        output_domain="string",
        examples=planning_examples,
        max_depth=4,
        max_library_candidates=32,
        max_relevant_candidates=32,
        max_expanded_prefixes=512,
        max_evaluated_complete_routes=64,
    )
    expected_selected = [str(absolute["candidate_id"]), str(to_string["candidate_id"])]
    if synthesized["selected_candidate_ids"] != expected_selected:
        raise BoundedVerifiedMacroSearchError("bounded search did not select abs then to-string")
    if synthesized["selected_depth"] != 2:
        raise BoundedVerifiedMacroSearchError("bounded search did not stop at minimal matching depth")
    if synthesized["evaluated_complete_route_count"] >= 64:
        raise BoundedVerifiedMacroSearchError("bounded search evaluated too many complete routes")
    if synthesized["expanded_prefix_count"] >= legacy_route_count:
        raise BoundedVerifiedMacroSearchError("bounded search did not reduce route expansion")

    program = synthesized["compiled_program"]
    challenge_inputs = (-23, 8, -31)
    challenge_outputs: list[str] = []
    selected_items = [
        item
        for candidate_id in synthesized["selected_candidate_ids"]
        for item in macros
        if str(item["candidate_id"]) == candidate_id
    ]
    for value in challenge_inputs:
        expected = str(abs(value))
        source_output, _run_id, _refs = _execute_chain(root, selected_items, value)
        compiled_output = execute_program(program, value)
        if source_output != expected or compiled_output != source_output:
            raise BoundedVerifiedMacroSearchError("bounded search route failed fresh challenge")
        challenge_outputs.append(compiled_output)

    trials_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in generated_ids
    }
    if trials_after != trials_before:
        raise BoundedVerifiedMacroSearchError("bounded search campaign changed Candidate trial evidence")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "bounded-verified-macro-search",
        "generated_candidate_count": len(generated_ids),
        "legacy_full_typed_route_count": legacy_route_count,
        "expanded_prefix_count": synthesized["expanded_prefix_count"],
        "evaluated_complete_route_count": synthesized["evaluated_complete_route_count"],
        "selected_depth": synthesized["selected_depth"],
        "selected_candidate_ids": synthesized["selected_candidate_ids"],
        "candidate_ids_supplied_by_caller": synthesized["candidate_ids_supplied_by_caller"],
        "candidate_trial_state_unchanged": trials_after == trials_before,
        "fresh_challenge_count": len(challenge_inputs),
        "fresh_challenge_outputs": challenge_outputs,
        "claim_boundary": (
            "Internal bounded verified-macro planning evidence only. Breadth-first type-compatible route "
            "search stopped at the unique minimal behavior match and enforced hard search budgets, but "
            "the library, tasks, examples, evaluator, and runtime remain repository-authored. This is "
            "not AGI or independent production evidence."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "bounded-verified-macro-search"
        / f"bounded-search-{_digest(seed)[:16]}.json",
        report,
    )
    return report
