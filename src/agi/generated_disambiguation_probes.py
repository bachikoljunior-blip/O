from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, _domain_of_value, _validate_json_tree
from agi.bounded_verified_macro_search import select_unique_minimal_verified_macro_chain
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.materialized_runtime_replay import _candidate_trial_snapshot
from agi.verified_macro_active_disambiguation import (
    _abs_spec,
    _add_one_spec,
    _identity_spec,
    minimal_matching_verified_macro_chains,
    propose_disambiguating_probe,
)
from agi.verified_macro_relevance_pruning import relevant_verified_macro_ids
from agi.verified_macro_synthesis import _load_verified_macro_items


class GeneratedProbeError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def generate_bounded_probe_inputs(
    input_domain: str,
    examples: Sequence[ProgramExample],
    *,
    max_probes: int = 32,
) -> tuple[Any, ...]:
    """Generate a finite deterministic fresh probe pool from domain-safe kernels and support transforms."""

    if input_domain not in {"numeric", "string", "sequence", "boolean", "object"}:
        raise ValueError("unsupported probe input domain")
    if not isinstance(max_probes, int) or isinstance(max_probes, bool) or not 1 <= max_probes <= 64:
        raise ValueError("max_probes must be an integer in [1, 64]")
    existing = {_canonical(item.input) for item in examples}
    candidates: list[Any] = []

    if input_domain == "numeric":
        candidates.extend(range(-5, 6))
        for item in examples:
            value = item.input
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            for derived in (-value, value - 1, value + 1):
                if isinstance(derived, (int, float)) and math.isfinite(float(derived)):
                    candidates.append(derived)
    elif input_domain == "string":
        candidates.extend(("", "a", "A", "0", "#", "aa", "Ab"))
        for item in examples:
            if not isinstance(item.input, str):
                continue
            candidates.extend(
                (
                    item.input[::-1],
                    item.input.swapcase(),
                    item.input + "#",
                )
            )
    elif input_domain == "boolean":
        candidates.extend((False, True))
    elif input_domain == "sequence":
        candidates.extend(([], [0], [1], [-1], ["a"], ["A"], [False], [True]))
        for item in examples:
            if not isinstance(item.input, (list, tuple)):
                continue
            value = list(item.input)
            candidates.append(list(reversed(value)))
            candidates.append(value[:1])
            if len(value) < 63:
                candidates.append([*value, 0])
    else:
        candidates.extend(
            (
                {},
                {"value": 0},
                {"value": 1},
                {"flag": False},
                {"flag": True},
                {"kind": "a"},
                {"kind": "b"},
            )
        )
        observed_keys: list[str] = []
        for item in examples:
            if isinstance(item.input, dict):
                observed_keys.extend(str(key) for key in item.input if isinstance(key, str) and key)
        for key in sorted(set(observed_keys))[:8]:
            candidates.extend(({key: 0}, {key: 1}, {key: False}, {key: True}))

    selected: list[Any] = []
    seen = set(existing)
    for value in candidates:
        if _domain_of_value(value) != input_domain:
            continue
        try:
            if input_domain in {"sequence", "object"}:
                _validate_json_tree(value)
            encoded = _canonical(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if encoded in seen:
            continue
        seen.add(encoded)
        selected.append(value)
        if len(selected) >= max_probes:
            break
    if not selected:
        raise GeneratedProbeError("no fresh bounded probe can be generated for the task domain")
    return tuple(selected)


def propose_generated_disambiguating_probe(
    root: Path,
    *,
    chains: Sequence[Sequence[dict[str, Any]]],
    input_domain: str,
    examples: Sequence[ProgramExample],
    max_probes: int = 32,
) -> dict[str, Any]:
    probes = generate_bounded_probe_inputs(input_domain, examples, max_probes=max_probes)
    proposal = propose_disambiguating_probe(
        root,
        chains=chains,
        probe_inputs=probes,
        existing_inputs=[item.input for item in examples],
        max_probe_inputs=max_probes,
    )
    return {
        **proposal,
        "generated_probe_count": len(probes),
        "probe_pool_supplied_by_caller": False,
        "probe_generator": "bounded-domain-kernels-and-support-transforms-v1",
    }


def run_generated_disambiguation_probe_campaign(root: Path, seed: str) -> dict[str, Any]:
    """Resolve verified-macro ambiguity without caller-supplied Candidate IDs or probe inputs.

    The Candidate library and finite probe pool are both discovered mechanically. Positive support keeps
    verified identity and abs tools ambiguous. The domain-safe generator must create a fresh negative
    numeric probe before any answer is known, a frozen oracle answers only after commitment, and the
    refined search must uniquely select abs while all regression trial ledgers remain unchanged.

    This is internal bounded active-learning evidence only, not AGI or independent production evidence.
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
    trials_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in generated_ids
    }
    if not all(trials_before.values()):
        raise GeneratedProbeError("generated Candidate lacks regression evidence")

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
        raise GeneratedProbeError("campaign did not produce exactly two ambiguous routes")

    proposal = propose_generated_disambiguating_probe(
        root,
        chains=ambiguous["matching_chains"],
        input_domain="numeric",
        examples=initial_examples,
        max_probes=16,
    )
    probe = proposal["probe"]
    if probe >= 0:
        raise GeneratedProbeError("generated probe failed to expose the sign ambiguity")
    committed_answer = abs(probe)
    refined_examples = (*initial_examples, ProgramExample(probe, committed_answer))
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
        raise GeneratedProbeError("generated committed probe did not identify abs")

    trials_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in generated_ids
    }
    if trials_after != trials_before:
        raise GeneratedProbeError("generated-probe campaign changed Candidate trial evidence")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "generated-disambiguation-probes",
        "candidate_ids_supplied_by_caller": False,
        "probe_pool_supplied_by_caller": False,
        "generated_probe_count": proposal["generated_probe_count"],
        "probe_generator": proposal["probe_generator"],
        "initial_matching_route_count": len(ambiguous["matching_chains"]),
        "probe_commitment": proposal["probe_commitment"],
        "probe_input": probe,
        "distinct_route_output_count": proposal["distinct_route_output_count"],
        "counterexample_answer_source": "frozen-campaign-abs-oracle-after-generated-probe-commitment",
        "counterexample_answer": committed_answer,
        "refined_selected_candidate_ids": selected_ids,
        "refined_selected_depth": metrics["selected_depth"],
        "candidate_trial_state_unchanged": trials_after == trials_before,
        "claim_boundary": (
            "Internal bounded active-learning evidence only. Candidate IDs and probe inputs were both "
            "discovered/generated mechanically before an oracle answer was supplied, but the generator, "
            "oracle, tasks, and runtime remain repository-authored. This is not AGI or independent "
            "production evidence."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "generated-disambiguation-probes"
        / f"generated-probes-{_digest(seed)[:16]}.json",
        report,
    )
    return report
