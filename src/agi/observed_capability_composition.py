from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.durable_state_rehydration import _tree_snapshot
from agi.generated_parameter_composition_transfer import (
    run_generated_parameter_composition_transfer,
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


class ObservedCapabilityCompositionError(RuntimeError):
    pass


def _as_bounded_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ObservedCapabilityCompositionError("observed numeric capability produced a non-integer")
    if abs(value) > 1_000_000:
        raise ObservedCapabilityCompositionError("observed numeric capability exceeded bounded output")
    return value


def _chain_vector(
    items_by_id: dict[str, dict[str, Any]],
    chain: Sequence[str],
    inputs: Sequence[int],
) -> tuple[int, ...]:
    outputs: list[int] = []
    for value in inputs:
        current: Any = value
        for candidate_id in chain:
            current = execute_program(items_by_id[candidate_id]["program"], current)
        outputs.append(_as_bounded_int(current))
    return tuple(outputs)


def _generated_spec(seed: str, prefix: str) -> dict[str, Any]:
    try:
        return next(
            spec for spec in _generated_goal_specs(seed) if str(spec["name"]).startswith(prefix)
        )
    except StopIteration as exc:  # pragma: no cover - fixed generator contract
        raise ObservedCapabilityCompositionError(f"missing generated spec: {prefix}") from exc


def _prerequisite_parameters(base_seed: str) -> dict[str, int]:
    scale = int(
        _generated_spec(f"{base_seed}:scale-source", "generated-scale-")["generated_parameter"]
    )
    offset = int(
        _generated_spec(f"{base_seed}:offset-source", "generated-offset-")["generated_parameter"]
    )
    distractor_scale: int | None = None
    for index in range(16):
        value = int(
            _generated_spec(
                f"{base_seed}:distractor:{index}",
                "generated-scale-",
            )["generated_parameter"]
        )
        if value != scale:
            distractor_scale = value
            break
    if distractor_scale is None:
        raise ObservedCapabilityCompositionError(
            "bounded prerequisite seed search found no distinct scale distractor"
        )
    return {
        "scale": scale,
        "offset": offset,
        "distractor_scale": distractor_scale,
    }


def _select_synthesizable_prerequisite_seed(seed: str) -> tuple[str, list[dict[str, Any]]]:
    """Choose a generated prerequisite whose parameters fit the existing bounded grammar.

    The underlying generated affine acquisition uses max_nodes=3. Its numeric binary expression can use
    the existing grammar's directly admitted scalar constants -5..5, so generated scale/offset values
    above 5 are known before mutation to be outside that exact synthesis frontier. Rather than partially
    learning one component and then failing on another, preflight a bounded deterministic seed sequence,
    retain rejected parameter instances as negative evidence, and execute only a compatible seed.
    """

    candidates = [f"{seed}:capability-base"] + [
        f"{seed}:capability-base:compatible:{index}" for index in range(32)
    ]
    rejected: list[dict[str, Any]] = []
    for candidate_seed in candidates:
        parameters = _prerequisite_parameters(candidate_seed)
        unsupported = {
            name: value for name, value in parameters.items() if not 2 <= int(value) <= 5
        }
        if not unsupported:
            return candidate_seed, rejected
        rejected.append(
            {
                "seed": candidate_seed,
                "parameters": parameters,
                "reason": "generated parameter is outside the max_nodes=3 numeric constant frontier [-5,5]",
                "unsupported_parameters": unsupported,
            }
        )
    raise ObservedCapabilityCompositionError(
        "bounded prerequisite seed search found no parameter set inside synthesis frontier"
    )


def run_observed_capability_composition(root: Path, seed: str) -> dict[str, Any]:
    """Generate a new composed target from observed durable capabilities, not caller role names.

    A bounded prerequisite first installs independently regression-verified generated numeric skills. Its
    generated parameters are preflighted against the existing max_nodes=3 synthesis frontier before any
    mutation; incompatible generated instances are retained as negative evidence and a bounded seed
    search selects the first compatible instance. A fresh planner then discovers the verified durable
    library without caller Candidate IDs and inspects only typed program behavior on seed-derived probes.
    It does not select candidates by names or by scale/offset roles. Instead it enumerates bounded
    two-stage behaviors, chooses a uniquely identifiable behavior that no one-stage route can realize,
    creates target examples from that observed composition, and asks the ordinary durable-library
    synthesizer to rediscover the route with no Candidate IDs.

    Post-selection challenges are generated only after the route is fixed and are executed through both
    the compiled program and fresh mechanical Engine runs. Candidate bytes and trial ledgers must remain
    unchanged in the fresh resolver and durable source. This is repository-authored internal development
    evidence only and cannot satisfy the independent external AGI claim gate.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    prerequisite_seed, rejected_prerequisite_seeds = _select_synthesizable_prerequisite_seed(seed)
    prerequisite = run_generated_parameter_composition_transfer(root, prerequisite_seed)
    if not prerequisite.get("passed"):
        raise ObservedCapabilityCompositionError("generated capability prerequisite did not pass")

    all_ids = _all_candidate_ids(root)
    source_tree = _tree_snapshot(root, all_ids)
    source_trials = _trial_snapshot(root, all_ids)
    if not all(source_trials.get(candidate_id) for candidate_id in all_ids):
        raise ObservedCapabilityCompositionError("durable capability library lacks trial evidence")

    with tempfile.TemporaryDirectory(prefix="agi-observed-capability-planner-") as temporary:
        fresh_root = Path(temporary).resolve()
        _copy_persistent_state(root, fresh_root)
        prior_runs_copied = (fresh_root / ".continual" / "runs").exists()
        prior_episodes_copied = (fresh_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (fresh_root / ".continual" / "evidence").exists()
        if _tree_snapshot(fresh_root, all_ids) != source_tree:
            raise ObservedCapabilityCompositionError("fresh planner changed Candidate bytes")
        if _trial_snapshot(fresh_root, all_ids) != source_trials:
            raise ObservedCapabilityCompositionError("fresh planner changed Candidate trials")

        library_ids = discover_verified_acquired_program_ids(fresh_root, max_candidates=64)
        library_items = _load_verified_macro_items(fresh_root, library_ids)
        numeric_items = [
            item
            for item in library_items
            if item["input_domain"] == "numeric" and item["output_domain"] == "numeric"
        ]
        if len(numeric_items) < 3:
            raise ObservedCapabilityCompositionError("observed library has fewer than three numeric skills")
        items_by_id = {str(item["candidate_id"]): item for item in numeric_items}
        numeric_ids = sorted(items_by_id)

        probe_digest = _digest({"seed": seed, "phase": "observed-composition-probes-v1"})
        base = 17 + int(probe_digest[:4], 16) % 23
        planning_inputs = (base, -(base + 3), base + 7)

        single_vectors: dict[tuple[int, ...], list[str]] = {}
        for candidate_id in numeric_ids:
            vector = _chain_vector(items_by_id, (candidate_id,), planning_inputs)
            single_vectors.setdefault(vector, []).append(candidate_id)

        pair_vectors: dict[tuple[int, ...], list[tuple[str, str]]] = {}
        for first in numeric_ids:
            for second in numeric_ids:
                vector = _chain_vector(items_by_id, (first, second), planning_inputs)
                pair_vectors.setdefault(vector, []).append((first, second))

        unique_composed: list[tuple[tuple[int, ...], tuple[str, str]]] = []
        for vector, pairs in sorted(pair_vectors.items(), key=lambda item: item[0]):
            if vector in single_vectors or len(pairs) != 1:
                continue
            pair = pairs[0]
            if pair[0] == pair[1]:
                continue
            unique_composed.append((vector, pair))
        if not unique_composed:
            raise ObservedCapabilityCompositionError(
                "observed library exposed no unique two-stage behavior beyond one-stage routes"
            )

        selection_commitment = _digest(
            {
                "seed": seed,
                "planning_inputs": planning_inputs,
                "numeric_candidate_ids": numeric_ids,
                "single_behavior_vectors": sorted(
                    (list(vector), sorted(ids)) for vector, ids in single_vectors.items()
                ),
                "unique_composed_vectors": [
                    {"vector": list(vector), "pair": list(pair)}
                    for vector, pair in unique_composed
                ],
                "phase": "observed-capability-composition-choice-v1",
            }
        )
        target_vector, hidden_pair = unique_composed[
            int(selection_commitment[:8], 16) % len(unique_composed)
        ]
        planning_examples = tuple(
            ProgramExample(value, output)
            for value, output in zip(planning_inputs, target_vector, strict=True)
        )

        synthesized = synthesize_from_verified_macro_library(
            fresh_root,
            input_domain="numeric",
            output_domain="numeric",
            examples=planning_examples,
            max_depth=2,
            max_candidates=64,
        )
        if synthesized.get("candidate_ids_supplied_by_caller") is not False:
            raise ObservedCapabilityCompositionError("synthesis relied on caller Candidate IDs")
        selected_ids = tuple(str(value) for value in synthesized["selected_candidate_ids"])
        if int(synthesized["shortest_type_chain_depth"]) != 1:
            raise ObservedCapabilityCompositionError("planner lacked a shorter type-correct route")
        if int(synthesized["selected_chain_depth"]) != 2:
            raise ObservedCapabilityCompositionError("observed target did not require two stages")
        if selected_ids != hidden_pair:
            raise ObservedCapabilityCompositionError(
                "library synthesis did not rediscover the uniquely observed two-stage behavior"
            )

        challenge_commitment = _digest(
            {
                "selection_commitment": selection_commitment,
                "synthesis_digest": synthesized["digest"],
                "phase": "observed-capability-post-selection-challenge-v1",
            }
        )
        magnitude = 71 + int(challenge_commitment[:4], 16) % 101
        challenge_inputs = (magnitude, -(magnitude + 5))
        if set(challenge_inputs) & set(planning_inputs):
            raise ObservedCapabilityCompositionError("challenge overlaps planning inputs")

        hidden_outputs = _chain_vector(items_by_id, hidden_pair, challenge_inputs)
        selected_items = _load_verified_macro_items(fresh_root, list(selected_ids))
        fresh_run_ids: list[str] = []
        outputs: list[dict[str, int]] = []
        for value, expected in zip(challenge_inputs, hidden_outputs, strict=True):
            compiled = _as_bounded_int(execute_program(synthesized["compiled_program"], value))
            runtime_output, run_id, refs = _execute_chain(fresh_root, selected_items, value)
            runtime_value = _as_bounded_int(runtime_output)
            if compiled != expected or runtime_value != expected:
                raise ObservedCapabilityCompositionError("observed composition failed challenge")
            if len(refs) != 2:
                raise ObservedCapabilityCompositionError("challenge did not execute two learned stages")
            fresh_run_ids.append(run_id)
            outputs.append({"input": value, "expected": expected, "output": runtime_value})

        if len(set(fresh_run_ids)) != len(fresh_run_ids):
            raise ObservedCapabilityCompositionError("post-selection challenge reused an Engine run")
        if _tree_snapshot(fresh_root, all_ids) != source_tree:
            raise ObservedCapabilityCompositionError("fresh execution changed Candidate bytes")
        if _trial_snapshot(fresh_root, all_ids) != source_trials:
            raise ObservedCapabilityCompositionError("fresh execution changed Candidate trials")

    if _tree_snapshot(root, all_ids) != source_tree:
        raise ObservedCapabilityCompositionError("fresh transfer mutated source Candidate bytes")
    if _trial_snapshot(root, all_ids) != source_trials:
        raise ObservedCapabilityCompositionError("fresh transfer mutated source Candidate trials")

    report: dict[str, Any] = {
        "schema_version": 2,
        "passed": True,
        "campaign_kind": "observed-capability-composition-v2",
        "prerequisite_seed": prerequisite_seed,
        "prerequisite_seed_search_attempts": len(rejected_prerequisite_seeds) + 1,
        "rejected_prerequisite_seed_count": len(rejected_prerequisite_seeds),
        "rejected_prerequisite_seeds": rejected_prerequisite_seeds,
        "prerequisite_digest": str(prerequisite["digest"]),
        "caller_supplied_candidate_ids": False,
        "caller_named_capability_roles": False,
        "planner_used_candidate_names": False,
        "numeric_library_candidate_count": len(numeric_ids),
        "planning_inputs": list(planning_inputs),
        "observed_single_behavior_count": len(single_vectors),
        "observed_unique_composed_behavior_count": len(unique_composed),
        "selection_commitment": selection_commitment,
        "hidden_observed_pair": list(hidden_pair),
        "selected_candidate_ids": list(selected_ids),
        "shortest_type_chain_depth": int(synthesized["shortest_type_chain_depth"]),
        "selected_chain_depth": int(synthesized["selected_chain_depth"]),
        "one_stage_routes_rejected_by_behavior": True,
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
            "Internal bounded observed-capability composition evidence only. Unsynthesizable generated "
            "prerequisite parameters are rejected before mutation and retained as negative evidence. The "
            "fresh planner then generates a two-stage target from mechanically observed durable numeric "
            "behaviors without caller Candidate IDs or named capability roles, and the verified library "
            "rediscovers and transfers that route. Repository-authored generation, search, runtime, "
            "regression, and scoring do not establish independent production evidence or AGI."
        ),
    }
    if not all(
        (
            report["fresh_engine_runs_unique"],
            report["one_stage_routes_rejected_by_behavior"],
            not report["prior_runs_copied"],
            not report["prior_episodes_copied"],
            not report["prior_evidence_copied"],
        )
    ):
        raise ObservedCapabilityCompositionError("observed composition aggregate invariant failed")
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "observed-capability-composition"
        / f"composition-{_digest(seed)[:16]}.json",
        report,
    )
    return report
