from __future__ import annotations

from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample
from agi.bounded_verified_macro_search import select_unique_minimal_verified_macro_chain
from agi.generated_disambiguation_probes import propose_generated_disambiguating_probe
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.materialized_runtime_replay import _candidate_trial_snapshot
from agi.verified_macro_active_disambiguation import (
    _abs_spec,
    _identity_spec,
    minimal_matching_verified_macro_chains,
)
from agi.verified_macro_relevance_pruning import relevant_verified_macro_ids
from agi.verified_macro_synthesis import _load_verified_macro_items


class IterativeMacroDisambiguationError(RuntimeError):
    pass


def _piecewise_spec() -> dict[str, Any]:
    """x for x>=-3, abs(x) below -3; designed to require a second counterexample."""

    return {
        "name": "iterative-piecewise",
        "input_domain": "numeric",
        "output_domain": "numeric",
        "examples": (
            ProgramExample(-6, 6),
            ProgramExample(-4, 4),
            ProgramExample(-3, -3),
            ProgramExample(-2, -2),
            ProgramExample(0, 0),
            ProgramExample(4, 4),
        ),
        "probe": -8,
        "expected": 8,
        "max_nodes": 7,
    }


def _piecewise_oracle(value: Any) -> Any:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise IterativeMacroDisambiguationError("piecewise oracle requires numeric input")
    return value if value >= -3 else abs(value)


def run_iterative_macro_disambiguation(root: Path, seed: str) -> dict[str, Any]:
    """Accumulate committed counterexamples until an ambiguous verified macro set is unique.

    Identity, abs, and a separately learned piecewise transform all agree on the initial positive-only
    support. The first generated probe is committed before the frozen target oracle is evaluated; its
    answer intentionally leaves abs and the piecewise target tied. The next round regenerates a fresh
    probe from the refined support and must commit a second counterexample that distinguishes those two.
    The loop is bounded, fails closed if no informative probe exists, and never rewrites Candidate trial
    evidence. It demonstrates a small active counterexample loop, not AGI or independent evaluation.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    identity = _promote_task(root, seed=f"{seed}:identity", spec=_identity_spec())
    absolute = _promote_task(root, seed=f"{seed}:abs", spec=_abs_spec())
    piecewise = _promote_task(root, seed=f"{seed}:piecewise", spec=_piecewise_spec())
    generated_ids = [
        str(identity["candidate_id"]),
        str(absolute["candidate_id"]),
        str(piecewise["candidate_id"]),
    ]
    trials_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in generated_ids
    }
    if not all(trials_before.values()):
        raise IterativeMacroDisambiguationError("generated Candidate lacks regression evidence")

    relevant = relevant_verified_macro_ids(
        root,
        input_domain="numeric",
        output_domain="numeric",
        max_depth=1,
        max_library_candidates=16,
        max_relevant_candidates=16,
    )
    candidates = _load_verified_macro_items(root, relevant["relevant_candidate_ids"])
    examples: tuple[ProgramExample, ...] = (
        ProgramExample(1, 1),
        ProgramExample(4, 4),
        ProgramExample(9, 9),
    )
    transcript: list[dict[str, Any]] = []
    matching_counts: list[int] = []
    selected: tuple[dict[str, Any], ...] | None = None

    for round_index in range(3):
        ambiguous = minimal_matching_verified_macro_chains(
            root,
            candidates=candidates,
            input_domain="numeric",
            output_domain="numeric",
            examples=examples,
            max_depth=1,
        )
        matching = tuple(ambiguous["matching_chains"])
        matching_counts.append(len(matching))
        if len(matching) == 1:
            selected = matching[0]
            break

        proposal = propose_generated_disambiguating_probe(
            root,
            chains=matching,
            input_domain="numeric",
            examples=examples,
            max_probes=24,
        )
        committed_record = {
            "round_index": round_index,
            "matching_route_count_before_answer": len(matching),
            "probe_input": proposal["probe"],
            "probe_commitment": proposal["probe_commitment"],
            "route_output_sha256": proposal["route_output_sha256"],
            "distinct_route_output_count": proposal["distinct_route_output_count"],
            "answer_known_at_probe_commitment": False,
        }
        # Only after the input and route-output commitment exists do we evaluate the frozen target oracle.
        answer = _piecewise_oracle(proposal["probe"])
        committed_record["oracle_answer"] = answer
        committed_record["answer_source"] = "frozen-piecewise-oracle-after-probe-commitment"
        transcript.append(committed_record)
        examples = (*examples, ProgramExample(proposal["probe"], answer))
    else:
        raise IterativeMacroDisambiguationError("counterexample loop exhausted its hard round bound")

    if selected is None:
        selected, metrics = select_unique_minimal_verified_macro_chain(
            root,
            candidates=candidates,
            input_domain="numeric",
            output_domain="numeric",
            examples=examples,
            max_depth=1,
        )
        selected_depth = int(metrics["selected_depth"])
    else:
        selected_depth = 1

    selected_ids = [str(item["candidate_id"]) for item in selected]
    if selected_ids != [str(piecewise["candidate_id"])]:
        raise IterativeMacroDisambiguationError("iterative counterexamples did not identify target")
    if len(transcript) != 2 or matching_counts[:3] != [3, 2, 1]:
        raise IterativeMacroDisambiguationError(
            f"campaign did not require exactly two informative rounds: {matching_counts}"
        )
    if transcript[0]["oracle_answer"] != abs(transcript[0]["probe_input"]):
        raise IterativeMacroDisambiguationError("first probe did not leave abs and target tied")
    if transcript[1]["oracle_answer"] == abs(transcript[1]["probe_input"]):
        raise IterativeMacroDisambiguationError("second probe did not separate abs from target")

    challenge_inputs = (-9, -3, -1, 8)
    challenge_outputs: list[Any] = []
    from agi.behavior_guided_tool_chain_discovery import _execute_chain

    for value in challenge_inputs:
        output, _run_id, _refs = _execute_chain(root, selected, value)
        expected = _piecewise_oracle(value)
        if output != expected:
            raise IterativeMacroDisambiguationError("selected route failed fresh target challenge")
        challenge_outputs.append(output)

    trials_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in generated_ids
    }
    if trials_after != trials_before:
        raise IterativeMacroDisambiguationError("counterexample loop changed Candidate trial evidence")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "iterative-macro-disambiguation",
        "candidate_ids_supplied_by_caller": False,
        "probe_pools_supplied_by_caller": False,
        "initial_matching_route_count": matching_counts[0],
        "matching_route_counts": matching_counts,
        "counterexample_round_count": len(transcript),
        "transcript": transcript,
        "all_probes_committed_before_answers": all(
            item["answer_known_at_probe_commitment"] is False for item in transcript
        ),
        "selected_candidate_ids": selected_ids,
        "selected_depth": selected_depth,
        "fresh_challenge_count": len(challenge_inputs),
        "fresh_challenge_outputs": challenge_outputs,
        "candidate_trial_state_unchanged": trials_after == trials_before,
        "claim_boundary": (
            "Internal bounded active-counterexample evidence only. The system required two generated "
            "probe/answer rounds to isolate a verified target route and committed each probe before its "
            "frozen-oracle answer, but Candidates, generator, oracle, tasks, and runtime remain "
            "repository-authored. This is not AGI or independent production evidence."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "iterative-macro-disambiguation"
        / f"iterative-disambiguation-{_digest(seed)[:16]}.json",
        report,
    )
    return report
