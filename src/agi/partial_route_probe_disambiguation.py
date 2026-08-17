from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.bounded_verified_macro_search import select_unique_minimal_verified_macro_chain
from agi.generated_disambiguation_probes import generate_bounded_probe_inputs
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.materialized_runtime_replay import _candidate_trial_snapshot
from agi.verified_macro_active_disambiguation import minimal_matching_verified_macro_chains
from agi.verified_macro_relevance_pruning import relevant_verified_macro_ids
from agi.verified_macro_synthesis import _load_verified_macro_items
from continual.acquired_program_tools import AcquiredProgramToolError


class PartialRouteProbeError(RuntimeError):
    pass


def propose_probe_with_partial_route_outcomes(
    root: Path,
    *,
    chains: Sequence[Sequence[dict[str, Any]]],
    probe_inputs: Sequence[Any],
    existing_inputs: Sequence[Any],
    max_probe_inputs: int = 64,
) -> dict[str, Any]:
    """Choose a probe while treating deterministic learned-tool rejection as an observable outcome.

    Acquired programs can be intentionally partial inside a broad type domain (for example object_get
    rejects an object lacking the projected key). Such a verified rejection is useful behavioral
    evidence and must not crash active disambiguation. Unexpected exceptions still propagate.
    """

    if len(chains) < 2:
        raise PartialRouteProbeError("partial-route disambiguation requires ambiguous routes")
    if len(probe_inputs) > max_probe_inputs:
        raise PartialRouteProbeError("probe pool exceeds bounded limit")
    existing = {
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        for value in existing_inputs
    }
    best: dict[str, Any] | None = None
    for index, value in enumerate(probe_inputs):
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if encoded in existing:
            continue
        outcome_hashes: list[str] = []
        signatures: set[str] = set()
        rejection_count = 0
        for chain in chains:
            try:
                output, _run_id, _refs = _execute_chain(root, chain, value)
                outcome = {"status": "ok", "output": output}
            except AcquiredProgramToolError:
                outcome = {"status": "verified_rejection", "error_class": "AcquiredProgramToolError"}
                rejection_count += 1
            signature = json.dumps(
                outcome,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            signatures.add(signature)
            outcome_hashes.append(_digest(outcome))
        record = {
            "probe": value,
            "probe_index": index,
            "distinct_route_outcome_count": len(signatures),
            "verified_rejection_count": rejection_count,
            "route_outcome_sha256": outcome_hashes,
        }
        if best is None or int(record["distinct_route_outcome_count"]) > int(
            best["distinct_route_outcome_count"]
        ):
            best = record
    if best is None or int(best["distinct_route_outcome_count"]) < 2:
        raise PartialRouteProbeError("no bounded probe distinguishes partial verified routes")
    return {
        **best,
        "probe_commitment": _digest(
            {
                "probe": best["probe"],
                "route_outcome_sha256": best["route_outcome_sha256"],
                "kind": "partial-route-disambiguation-probe-v1",
            }
        ),
    }


def _has_flag_spec() -> dict[str, Any]:
    return {
        "name": "partial-probe-has-flag",
        "input_domain": "object",
        "output_domain": "boolean",
        "examples": (
            ProgramExample({"flag": True}, True),
            ProgramExample({"x": 1}, False),
            ProgramExample({"flag": False}, True),
        ),
        "probe": {"y": 2},
        "expected": False,
        "max_nodes": 3,
    }


def _flag_value_spec() -> dict[str, Any]:
    return {
        "name": "partial-probe-flag-value",
        "input_domain": "object",
        "output_domain": "boolean",
        "examples": (
            ProgramExample({"flag": True}, True),
            ProgramExample({"flag": False}, False),
            ProgramExample({"flag": True, "x": 1}, True),
        ),
        "probe": {"flag": False, "x": 2},
        "expected": False,
        "max_nodes": 3,
    }


def run_partial_route_probe_disambiguation(root: Path, seed: str) -> dict[str, Any]:
    """Use generated object probes to distinguish total presence logic from partial projection logic.

    Two exact-scope verified object→boolean Candidates both return True on initial objects containing a
    true ``flag`` field. One learned program tests key presence and remains total on any finite object;
    the other projects ``flag`` and deterministically rejects objects without the key. Generated probes
    must exploit that verified rejection as behavior, commit the distinguishing object before the oracle
    answer, then refine to the total presence Candidate without changing Candidate trial ledgers.

    Internal robustness/active-learning evidence only; not AGI or independent production evidence.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    presence = _promote_task(root, seed=f"{seed}:presence", spec=_has_flag_spec())
    projection = _promote_task(root, seed=f"{seed}:projection", spec=_flag_value_spec())
    generated_ids = [str(presence["candidate_id"]), str(projection["candidate_id"])]
    trials_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in generated_ids
    }
    if not all(trials_before.values()):
        raise PartialRouteProbeError("generated object Candidate lacks regression evidence")

    relevant = relevant_verified_macro_ids(
        root,
        input_domain="object",
        output_domain="boolean",
        max_depth=1,
        max_library_candidates=16,
        max_relevant_candidates=16,
    )
    candidates = _load_verified_macro_items(root, relevant["relevant_candidate_ids"])
    initial_examples = (
        ProgramExample({"flag": True}, True),
        ProgramExample({"flag": True, "x": 1}, True),
        ProgramExample({"flag": True, "y": 2}, True),
    )
    ambiguous = minimal_matching_verified_macro_chains(
        root,
        candidates=candidates,
        input_domain="object",
        output_domain="boolean",
        examples=initial_examples,
        max_depth=1,
    )
    if len(ambiguous["matching_chains"]) != 2:
        raise PartialRouteProbeError("object campaign did not produce two ambiguous routes")

    generated_probes = generate_bounded_probe_inputs("object", initial_examples, max_probes=24)
    proposal = propose_probe_with_partial_route_outcomes(
        root,
        chains=ambiguous["matching_chains"],
        probe_inputs=generated_probes,
        existing_inputs=[item.input for item in initial_examples],
        max_probe_inputs=24,
    )
    probe = proposal["probe"]
    if not isinstance(probe, dict) or "flag" in probe:
        raise PartialRouteProbeError("chosen object probe did not expose missing-key behavior")
    if proposal["verified_rejection_count"] != 1:
        raise PartialRouteProbeError("probe did not produce exactly one verified route rejection")

    committed_answer = bool("flag" in probe)
    refined_examples = (*initial_examples, ProgramExample(probe, committed_answer))
    selected, metrics = select_unique_minimal_verified_macro_chain(
        root,
        candidates=candidates,
        input_domain="object",
        output_domain="boolean",
        examples=refined_examples,
        max_depth=1,
    )
    selected_ids = [str(item["candidate_id"]) for item in selected]
    if selected_ids != [str(presence["candidate_id"])]:
        raise PartialRouteProbeError("missing-key counterexample did not identify presence Candidate")

    challenge_inputs = (
        {},
        {"flag": False},
        {"flag": True, "z": 1},
        {"other": 1},
    )
    challenge_outputs: list[bool] = []
    for value in challenge_inputs:
        output, _run_id, _refs = _execute_chain(root, selected, value)
        expected = bool("flag" in value)
        if output != expected:
            raise PartialRouteProbeError("refined total presence route failed fresh object challenge")
        challenge_outputs.append(bool(output))

    trials_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in generated_ids
    }
    if trials_after != trials_before:
        raise PartialRouteProbeError("partial-route probe campaign changed Candidate trial evidence")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "partial-route-probe-disambiguation",
        "candidate_ids_supplied_by_caller": False,
        "probe_pool_supplied_by_caller": False,
        "generated_probe_count": len(generated_probes),
        "initial_matching_route_count": len(ambiguous["matching_chains"]),
        "probe_commitment": proposal["probe_commitment"],
        "probe_input": probe,
        "distinct_route_outcome_count": proposal["distinct_route_outcome_count"],
        "verified_rejection_count": proposal["verified_rejection_count"],
        "route_outcome_sha256": proposal["route_outcome_sha256"],
        "counterexample_answer_source": "frozen-presence-oracle-after-probe-commitment",
        "counterexample_answer": committed_answer,
        "refined_selected_candidate_ids": selected_ids,
        "refined_selected_depth": metrics["selected_depth"],
        "fresh_challenge_count": len(challenge_inputs),
        "fresh_challenge_outputs": challenge_outputs,
        "candidate_trial_state_unchanged": trials_after == trials_before,
        "claim_boundary": (
            "Internal bounded object-domain robustness evidence only. Deterministic verified rejection "
            "was treated as an informative route outcome rather than a planner crash, but Candidates, "
            "probe generator, oracle, tasks, and runtime remain repository-authored. This is not AGI or "
            "independent production evidence."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "partial-route-probe-disambiguation"
        / f"partial-route-probe-{_digest(seed)[:16]}.json",
        report,
    )
    return report
