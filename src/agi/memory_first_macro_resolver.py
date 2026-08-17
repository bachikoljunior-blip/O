from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence

from agi.acquired_programs import ProgramExample
from agi.bounded_verified_macro_search import select_unique_minimal_verified_macro_chain
from agi.generated_disambiguation_probes import generate_bounded_probe_inputs
from agi.heterogeneous_retention_campaign import _digest
from agi.materialized_runtime_replay import _candidate_trial_snapshot
from agi.multiround_disambiguation_bank import (
    MultiRoundDisambiguationError,
    append_counterexample_to_bank,
    bank_examples,
)
from agi.partial_route_probe_disambiguation import propose_probe_with_partial_route_outcomes
from agi.verified_macro_active_disambiguation import minimal_matching_verified_macro_chains


class MemoryFirstMacroResolutionError(RuntimeError):
    pass


Oracle = Callable[[Any], Any]


def _matching(
    root: Path,
    *,
    candidates: Sequence[dict[str, Any]],
    input_domain: str,
    output_domain: str,
    examples: Sequence[ProgramExample],
    max_depth: int,
) -> dict[str, Any]:
    return minimal_matching_verified_macro_chains(
        root,
        candidates=candidates,
        input_domain=input_domain,
        output_domain=output_domain,
        examples=examples,
        max_depth=max_depth,
    )


def _candidate_ids(candidates: Sequence[dict[str, Any]]) -> list[str]:
    ids = [str(item["candidate_id"]) for item in candidates]
    if not ids or len(ids) != len(set(ids)):
        raise MemoryFirstMacroResolutionError("Candidates must have unique identities")
    return ids


def resolve_verified_macro_with_memory(
    root: Path,
    *,
    candidates: Sequence[dict[str, Any]],
    task_key: str,
    input_domain: str,
    output_domain: str,
    support: Sequence[ProgramExample],
    max_depth: int,
    max_rounds: int = 4,
    max_probes_per_round: int = 32,
    oracle: Oracle | None = None,
) -> dict[str, Any]:
    """Resolve verified-macro ambiguity memory-first, querying an oracle only when still necessary.

    Durable counterexamples are scoped to the exact task key, input/output domains, and base-support
    digest. Existing memory is applied before any new probe is generated. If ambiguity remains, a fresh
    bounded probe is generated and behaviorally committed before the supplied oracle is called; the
    resulting counterexample is appended to the task bank and the search repeats. If no oracle is
    available while ambiguity remains, resolution fails closed rather than guessing.

    Candidate promotion/regression evidence is read-only throughout. The returned selected chain remains
    subject to the normal exact-scope verified-Candidate rules and is not itself an AGI claim.
    """

    if not isinstance(task_key, str) or not task_key.strip():
        raise ValueError("task_key must be non-empty")
    if not 1 <= max_depth <= 6:
        raise ValueError("max_depth must be in [1, 6]")
    if not isinstance(max_rounds, int) or isinstance(max_rounds, bool) or not 0 <= max_rounds <= 16:
        raise ValueError("max_rounds must be an integer in [0, 16]")
    if (
        not isinstance(max_probes_per_round, int)
        or isinstance(max_probes_per_round, bool)
        or not 1 <= max_probes_per_round <= 64
    ):
        raise ValueError("max_probes_per_round must be an integer in [1, 64]")
    root = root.resolve()
    normalized_candidates = tuple(candidates)
    normalized_support = tuple(support)
    if len(normalized_support) < 2:
        raise ValueError("at least two support examples are required")

    candidate_ids = _candidate_ids(normalized_candidates)
    trials_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in candidate_ids
    }
    if not all(trials_before.values()):
        raise MemoryFirstMacroResolutionError(
            "all Candidates must have persisted regression evidence before resolution"
        )

    initial = _matching(
        root,
        candidates=normalized_candidates,
        input_domain=input_domain,
        output_domain=output_domain,
        examples=normalized_support,
        max_depth=max_depth,
    )
    initial_route_count = len(initial["matching_chains"])

    try:
        remembered = bank_examples(
            root,
            task_key=task_key,
            input_domain=input_domain,
            output_domain=output_domain,
            support=normalized_support,
        )
    except MultiRoundDisambiguationError as exc:
        if "does not exist" not in str(exc):
            raise MemoryFirstMacroResolutionError(str(exc)) from exc
        remembered = ()

    learned_examples: list[ProgramExample] = [*normalized_support, *remembered]
    memory_route_count = len(
        _matching(
            root,
            candidates=normalized_candidates,
            input_domain=input_domain,
            output_domain=output_domain,
            examples=learned_examples,
            max_depth=max_depth,
        )["matching_chains"]
    )
    oracle_query_count = 0
    generated_probe_count = 0
    probe_commitments: list[str] = []
    route_counts = [memory_route_count]

    while route_counts[-1] > 1:
        if oracle is None:
            raise MemoryFirstMacroResolutionError(
                "verified macro ambiguity remains after durable memory and no oracle is available"
            )
        if oracle_query_count >= max_rounds:
            raise MemoryFirstMacroResolutionError(
                "verified macro ambiguity exceeded bounded oracle-query rounds"
            )
        ambiguous = _matching(
            root,
            candidates=normalized_candidates,
            input_domain=input_domain,
            output_domain=output_domain,
            examples=learned_examples,
            max_depth=max_depth,
        )
        probes = generate_bounded_probe_inputs(
            input_domain,
            learned_examples,
            max_probes=max_probes_per_round,
        )
        proposal = propose_probe_with_partial_route_outcomes(
            root,
            chains=ambiguous["matching_chains"],
            probe_inputs=probes,
            existing_inputs=[item.input for item in learned_examples],
            max_probe_inputs=max_probes_per_round,
        )
        probe = proposal["probe"]
        commitment = str(proposal["probe_commitment"])
        # The oracle is intentionally invoked only after the probe and route behaviors are committed.
        answer = oracle(probe)
        oracle_query_count += 1
        generated_probe_count += len(probes)
        probe_commitments.append(commitment)
        append_counterexample_to_bank(
            root,
            task_key=task_key,
            input_domain=input_domain,
            output_domain=output_domain,
            support=normalized_support,
            round_index=len(remembered) + oracle_query_count,
            probe=probe,
            answer=answer,
            probe_commitment=commitment,
            route_output_sha256=proposal["route_outcome_sha256"],
            answer_source=f"resolver-oracle-round-{oracle_query_count}-after-probe-commitment",
        )
        learned_examples.append(ProgramExample(probe, answer))
        refined = _matching(
            root,
            candidates=normalized_candidates,
            input_domain=input_domain,
            output_domain=output_domain,
            examples=learned_examples,
            max_depth=max_depth,
        )
        refined_count = len(refined["matching_chains"])
        if refined_count >= route_counts[-1]:
            raise MemoryFirstMacroResolutionError(
                "committed oracle answer did not reduce verified-macro ambiguity"
            )
        route_counts.append(refined_count)

    selected, metrics = select_unique_minimal_verified_macro_chain(
        root,
        candidates=normalized_candidates,
        input_domain=input_domain,
        output_domain=output_domain,
        examples=tuple(learned_examples),
        max_depth=max_depth,
    )
    trials_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in candidate_ids
    }
    if trials_after != trials_before:
        raise MemoryFirstMacroResolutionError(
            "memory-first resolution changed Candidate regression evidence"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "resolver_kind": "memory-first-verified-macro-resolver-v1",
        "task_key": task_key,
        "candidate_ids": candidate_ids,
        "initial_matching_route_count": initial_route_count,
        "remembered_counterexample_count": len(remembered),
        "route_count_after_memory": memory_route_count,
        "route_counts_during_new_learning": route_counts,
        "oracle_available": oracle is not None,
        "oracle_query_count": oracle_query_count,
        "generated_probe_pool_items_considered": generated_probe_count,
        "new_probe_commitments": probe_commitments,
        "selected_candidate_ids": [str(item["candidate_id"]) for item in selected],
        "selected_depth": metrics["selected_depth"],
        "candidate_trial_state_unchanged": trials_after == trials_before,
        "claim_boundary": (
            "Reusable internal continual-learning primitive only. It applies exact-context durable "
            "counterexamples before generating new committed probes and invokes an oracle only while "
            "verified ambiguity remains. Candidate evidence, task memory, probe generation, and any "
            "provided oracle may still be repository-authored; this is not AGI or independent "
            "production evidence."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    return {"selected": tuple(selected), "report": report}
