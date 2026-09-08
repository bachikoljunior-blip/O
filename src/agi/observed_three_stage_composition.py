from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.durable_state_rehydration import _tree_snapshot
from agi.heterogeneous_observed_composition import (
    run_heterogeneous_observed_composition,
)
from agi.heterogeneous_retention_campaign import _digest
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.seeded_generated_goal_acquisition import _generated_goal_specs
from agi.verified_macro_library_discovery import (
    discover_verified_acquired_program_ids,
    synthesize_from_verified_macro_library,
)
from agi.verified_macro_synthesis import _load_verified_macro_items


class ObservedThreeStageCompositionError(RuntimeError):
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


def _generated_scale(seed: str) -> int:
    specs = _generated_goal_specs(f"{seed}:heterogeneous-library")
    try:
        spec = next(
            item
            for item in specs
            if item["input_domain"] == "numeric" and item["output_domain"] == "numeric"
        )
    except StopIteration as exc:  # pragma: no cover - fixed generator contract
        raise ObservedThreeStageCompositionError("generated heterogeneous library lacks numeric skill") from exc
    return int(spec["generated_parameter"])


def _select_compatible_heterogeneous_seed(seed: str) -> tuple[str, list[dict[str, Any]]]:
    """Select a prerequisite seed that stays inside the current bounded numeric frontier."""

    candidates = [f"{seed}:heterogeneous-base"] + [
        f"{seed}:heterogeneous-base:compatible:{index}" for index in range(32)
    ]
    rejected: list[dict[str, Any]] = []
    for candidate in candidates:
        scale = _generated_scale(candidate)
        if 2 <= scale <= 5:
            return candidate, rejected
        rejected.append(
            {
                "seed": candidate,
                "generated_scale": scale,
                "reason": "generated numeric scale is outside the max_nodes=3 constant frontier [-5,5]",
            }
        )
    raise ObservedThreeStageCompositionError(
        "bounded heterogeneous prerequisite search found no synthesizable numeric scale"
    )


def _planning_inputs(domain: str, seed: str) -> tuple[Any, ...]:
    token = _digest({"seed": seed, "domain": domain, "phase": "observed-three-stage-probes-v1"})
    magnitude = 23 + int(token[:4], 16) % 31
    if domain == "numeric":
        return (magnitude, -(magnitude + 3), magnitude + 8)
    if domain == "sequence":
        return (
            [magnitude, -4, 1],
            [-(magnitude + 2), 7, 3],
            [9, -2, magnitude + 5],
        )
    raise ObservedThreeStageCompositionError(f"unsupported planning input domain: {domain}")


def _challenge_inputs(domain: str, commitment: str) -> tuple[Any, ...]:
    magnitude = 137 + int(commitment[:4], 16) % 173
    if domain == "numeric":
        return (magnitude, -(magnitude + 9))
    if domain == "sequence":
        return (
            [magnitude, -17, 6],
            [-(magnitude + 5), 19, 4, -2],
        )
    raise ObservedThreeStageCompositionError(f"unsupported challenge input domain: {domain}")


def _execute_descriptor_chain(
    items_by_id: dict[str, dict[str, Any]],
    chain: Sequence[str],
    value: Any,
) -> Any:
    current = value
    for candidate_id in chain:
        current = execute_program(items_by_id[candidate_id]["program"], current)
    return current


def _enumerate_typed_chains(
    items: Sequence[dict[str, Any]],
    *,
    max_depth: int,
) -> list[tuple[str, ...]]:
    by_input: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_input.setdefault(str(item["input_domain"]), []).append(item)
    for values in by_input.values():
        values.sort(key=lambda item: str(item["candidate_id"]))

    chains: list[tuple[str, ...]] = []

    def extend(chain: tuple[str, ...], output_domain: str) -> None:
        if len(chain) >= max_depth:
            return
        for item in by_input.get(output_domain, []):
            candidate_id = str(item["candidate_id"])
            if candidate_id in chain:
                continue
            child = (*chain, candidate_id)
            chains.append(child)
            extend(child, str(item["output_domain"]))

    for item in sorted(items, key=lambda value: str(value["candidate_id"])):
        candidate_id = str(item["candidate_id"])
        chain = (candidate_id,)
        chains.append(chain)
        extend(chain, str(item["output_domain"]))
    return chains


def run_observed_three_stage_composition(root: Path, seed: str) -> dict[str, Any]:
    """Generate and solve a behavior-defined three-stage target from durable heterogeneous skills.

    A bounded heterogeneous prerequisite is first selected without widening the existing synthesis grammar.
    In a fresh state-only resolver, verified skills are discovered without caller Candidate IDs. The
    planner enumerates all non-repeating compatible chains through depth three, observes their behavior on
    seed-committed probes, and chooses a unique non-constant three-stage behavior that no one- or two-stage
    chain matches even though a shorter type-correct route exists for the same input/output signature.

    The ordinary verified-library synthesizer must rediscover the same unique minimal behavior route from
    examples alone. Challenges are generated only after route selection and executed in fresh Engine runs.
    Candidate bytes and trial ledgers remain immutable. This is bounded repository-authored internal
    development evidence only; it is never admissible for the strict independent external AGI gate.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    prerequisite_seed, rejected_prerequisite_seeds = _select_compatible_heterogeneous_seed(seed)
    prerequisite = run_heterogeneous_observed_composition(root, prerequisite_seed)
    if not prerequisite.get("passed"):
        raise ObservedThreeStageCompositionError("heterogeneous prerequisite did not pass")

    all_ids = _all_candidate_ids(root)
    source_tree = _tree_snapshot(root, all_ids)
    source_trials = _trial_snapshot(root, all_ids)
    if not all(source_trials.get(candidate_id) for candidate_id in all_ids):
        raise ObservedThreeStageCompositionError("durable library has missing Candidate trial evidence")

    with tempfile.TemporaryDirectory(prefix="agi-observed-three-stage-") as temporary:
        fresh_root = Path(temporary).resolve()
        _copy_persistent_state(root, fresh_root)
        prior_runs_copied = (fresh_root / ".continual" / "runs").exists()
        prior_episodes_copied = (fresh_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (fresh_root / ".continual" / "evidence").exists()
        if _tree_snapshot(fresh_root, all_ids) != source_tree:
            raise ObservedThreeStageCompositionError("fresh resolver changed Candidate bytes")
        if _trial_snapshot(fresh_root, all_ids) != source_trials:
            raise ObservedThreeStageCompositionError("fresh resolver changed Candidate trials")

        library_ids = discover_verified_acquired_program_ids(fresh_root, max_candidates=64)
        items = _load_verified_macro_items(fresh_root, library_ids)
        items_by_id = {str(item["candidate_id"]): item for item in items}
        chains = _enumerate_typed_chains(items, max_depth=3)
        if not chains:
            raise ObservedThreeStageCompositionError("verified library exposed no typed chains")

        inputs_by_domain = {
            domain: _planning_inputs(domain, seed)
            for domain in _SUPPORTED_INPUT_DOMAINS
        }
        records: list[dict[str, Any]] = []
        behavior_groups: dict[tuple[str, str, str], list[tuple[str, ...]]] = {}
        type_depths: dict[tuple[str, str], set[int]] = {}
        for chain in chains:
            first = items_by_id[chain[0]]
            last = items_by_id[chain[-1]]
            input_domain = str(first["input_domain"])
            output_domain = str(last["output_domain"])
            if input_domain not in inputs_by_domain:
                continue
            planning_inputs = inputs_by_domain[input_domain]
            outputs = [
                _execute_descriptor_chain(items_by_id, chain, value)
                for value in planning_inputs
            ]
            behavior_key = (input_domain, output_domain, _canonical(outputs))
            behavior_groups.setdefault(behavior_key, []).append(chain)
            type_depths.setdefault((input_domain, output_domain), set()).add(len(chain))
            records.append(
                {
                    "chain": chain,
                    "depth": len(chain),
                    "input_domain": input_domain,
                    "output_domain": output_domain,
                    "planning_inputs": planning_inputs,
                    "outputs": outputs,
                    "behavior_key": behavior_key,
                    "stage_signatures": tuple(
                        (
                            str(items_by_id[candidate_id]["input_domain"]),
                            str(items_by_id[candidate_id]["output_domain"]),
                        )
                        for candidate_id in chain
                    ),
                }
            )

        shorter_behavior_keys = {
            record["behavior_key"] for record in records if int(record["depth"]) < 3
        }
        eligible: list[dict[str, Any]] = []
        for record in records:
            if int(record["depth"]) != 3:
                continue
            if len(set(record["stage_signatures"])) < 3:
                continue
            if len({_canonical(value) for value in record["outputs"]}) < 2:
                continue
            if record["behavior_key"] in shorter_behavior_keys:
                continue
            if len(behavior_groups[record["behavior_key"]]) != 1:
                continue
            signature = (str(record["input_domain"]), str(record["output_domain"]))
            shorter_type_depths = [depth for depth in type_depths.get(signature, set()) if depth < 3]
            if not shorter_type_depths:
                continue
            record = dict(record)
            record["shorter_type_depth"] = min(shorter_type_depths)
            eligible.append(record)
        if not eligible:
            raise ObservedThreeStageCompositionError(
                "observed library exposed no unique three-stage behavior beyond shorter type-correct routes"
            )

        selection_commitment = _digest(
            {
                "seed": seed,
                "library_ids": sorted(library_ids),
                "eligible": [
                    {
                        "chain": list(record["chain"]),
                        "stage_signatures": [list(value) for value in record["stage_signatures"]],
                        "input_domain": record["input_domain"],
                        "output_domain": record["output_domain"],
                        "outputs": record["outputs"],
                        "shorter_type_depth": record["shorter_type_depth"],
                    }
                    for record in eligible
                ],
                "phase": "observed-three-stage-selection-v1",
            }
        )
        target = eligible[int(selection_commitment[:8], 16) % len(eligible)]
        hidden_chain = tuple(str(value) for value in target["chain"])
        examples = tuple(
            ProgramExample(value, output)
            for value, output in zip(
                target["planning_inputs"],
                target["outputs"],
                strict=True,
            )
        )

        synthesized = synthesize_from_verified_macro_library(
            fresh_root,
            input_domain=str(target["input_domain"]),
            output_domain=str(target["output_domain"]),
            examples=examples,
            max_depth=3,
            max_candidates=64,
        )
        if synthesized.get("candidate_ids_supplied_by_caller") is not False:
            raise ObservedThreeStageCompositionError("synthesis relied on caller Candidate IDs")
        selected_ids = tuple(str(value) for value in synthesized["selected_candidate_ids"])
        if int(synthesized["selected_chain_depth"]) != 3:
            raise ObservedThreeStageCompositionError("synthesis did not require the three-stage route")
        if int(synthesized["shortest_type_chain_depth"]) >= 3:
            raise ObservedThreeStageCompositionError(
                "target did not reject a shorter type-correct route by behavior"
            )
        if selected_ids != hidden_chain:
            raise ObservedThreeStageCompositionError(
                "verified library did not rediscover the uniquely observed three-stage route"
            )

        challenge_commitment = _digest(
            {
                "selection_commitment": selection_commitment,
                "synthesis_digest": synthesized["digest"],
                "phase": "observed-three-stage-post-selection-v1",
            }
        )
        challenge_inputs = _challenge_inputs(str(target["input_domain"]), challenge_commitment)
        if any(value in target["planning_inputs"] for value in challenge_inputs):
            raise ObservedThreeStageCompositionError("challenge overlaps planning inputs")
        expected_outputs = [
            _execute_descriptor_chain(items_by_id, hidden_chain, value)
            for value in challenge_inputs
        ]
        selected_items = _load_verified_macro_items(fresh_root, list(selected_ids))
        fresh_run_ids: list[str] = []
        outputs: list[dict[str, Any]] = []
        for value, expected in zip(challenge_inputs, expected_outputs, strict=True):
            compiled_output = execute_program(synthesized["compiled_program"], value)
            runtime_output, run_id, refs = _execute_chain(fresh_root, selected_items, value)
            if compiled_output != expected or runtime_output != expected:
                raise ObservedThreeStageCompositionError("three-stage route failed post-selection challenge")
            if len(refs) != 3:
                raise ObservedThreeStageCompositionError("challenge did not execute three learned stages")
            fresh_run_ids.append(run_id)
            outputs.append({"input": value, "expected": expected, "output": runtime_output})

        if len(set(fresh_run_ids)) != len(fresh_run_ids):
            raise ObservedThreeStageCompositionError("post-selection challenge reused a fresh Engine run")
        if _tree_snapshot(fresh_root, all_ids) != source_tree:
            raise ObservedThreeStageCompositionError("fresh challenge changed Candidate bytes")
        if _trial_snapshot(fresh_root, all_ids) != source_trials:
            raise ObservedThreeStageCompositionError("fresh challenge changed Candidate trials")

    if _tree_snapshot(root, all_ids) != source_tree:
        raise ObservedThreeStageCompositionError("fresh transfer mutated source Candidate bytes")
    if _trial_snapshot(root, all_ids) != source_trials:
        raise ObservedThreeStageCompositionError("fresh transfer mutated source Candidate trials")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "observed-three-stage-composition-v1",
        "prerequisite_seed": prerequisite_seed,
        "prerequisite_rejected_seed_count": len(rejected_prerequisite_seeds),
        "prerequisite_rejected_seeds": rejected_prerequisite_seeds,
        "prerequisite_digest": str(prerequisite["digest"]),
        "caller_supplied_candidate_ids": False,
        "planner_selected_by_semantic_role_name": False,
        "typed_chain_count_through_depth_three": len(chains),
        "eligible_unique_three_stage_behavior_count": len(eligible),
        "selection_commitment": selection_commitment,
        "hidden_observed_chain": list(hidden_chain),
        "selected_candidate_ids": list(selected_ids),
        "selected_stage_signatures": [list(value) for value in target["stage_signatures"]],
        "selected_distinct_stage_signature_count": len(set(target["stage_signatures"])),
        "shorter_type_correct_route_depth": int(target["shorter_type_depth"]),
        "shortest_type_chain_depth": int(synthesized["shortest_type_chain_depth"]),
        "selected_chain_depth": int(synthesized["selected_chain_depth"]),
        "shorter_routes_rejected_by_behavior": True,
        "challenge_generated_after_selection": True,
        "challenge_commitment": challenge_commitment,
        "challenge_case_count": len(challenge_inputs),
        "challenge_outputs": outputs,
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
            "Internal bounded observed three-stage composition evidence only. A fresh resolver mechanically "
            "generated a unique three-stage target from verified durable heterogeneous behavior, rejected "
            "shorter type-correct routes by behavior, and transferred the selected route across fresh "
            "Engine challenges without rewriting Candidate bytes or trial ledgers. Repository-authored "
            "generation, regression, search, runtime, and scoring do not establish independent production "
            "evidence or AGI."
        ),
    }
    if not all(
        (
            report["fresh_engine_runs_unique"],
            report["shorter_routes_rejected_by_behavior"],
            report["selected_chain_depth"] == 3,
            report["shortest_type_chain_depth"] < 3,
            report["selected_distinct_stage_signature_count"] >= 3,
            not report["prior_runs_copied"],
            not report["prior_episodes_copied"],
            not report["prior_evidence_copied"],
        )
    ):
        raise ObservedThreeStageCompositionError("three-stage aggregate invariant failed")
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "observed-three-stage-composition"
        / f"composition-{_digest(seed)[:16]}.json",
        report,
    )
    return report
