from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.durable_state_rehydration import _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.seeded_generated_goal_acquisition import _generated_goal_specs
from agi.verified_macro_library_discovery import (
    discover_verified_acquired_program_ids,
    synthesize_from_verified_macro_library,
)
from agi.verified_macro_synthesis import (
    VerifiedMacroSynthesisError,
    _load_verified_macro_items,
    synthesize_verified_macro_program,
)


class HeterogeneousObservedCompositionError(RuntimeError):
    pass


_SUPPORTED_PROBE_DOMAINS = {"numeric", "sequence"}


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _planning_inputs(domain: str, seed: str) -> tuple[Any, ...]:
    token = _digest({"seed": seed, "domain": domain, "phase": "heterogeneous-observed-probes-v1"})
    magnitude = 19 + int(token[:4], 16) % 29
    if domain == "numeric":
        return (magnitude, -(magnitude + 4), magnitude + 9)
    if domain == "sequence":
        return (
            [magnitude, -3, 1],
            [-(magnitude + 2), 4, 2],
            [7, -2, magnitude + 1],
        )
    raise HeterogeneousObservedCompositionError(f"unsupported probe domain: {domain}")


def _challenge_inputs(domain: str, commitment: str) -> tuple[Any, ...]:
    magnitude = 101 + int(commitment[:4], 16) % 151
    if domain == "numeric":
        return (magnitude, -(magnitude + 7))
    if domain == "sequence":
        return (
            [magnitude, -11, 5],
            [-(magnitude + 3), 13, 2, -1],
        )
    raise HeterogeneousObservedCompositionError(f"unsupported challenge domain: {domain}")


def _dummy_output(domain: str, index: int) -> Any:
    if domain == "numeric":
        return index
    if domain == "string":
        return f"dummy-{index}"
    if domain == "boolean":
        return bool(index % 2)
    if domain == "object":
        return {"dummy": index}
    if domain == "sequence":
        return [index]
    raise HeterogeneousObservedCompositionError(f"unsupported output domain: {domain}")


def _run_chain(
    items_by_id: dict[str, dict[str, Any]],
    chain: Sequence[str],
    value: Any,
) -> Any:
    current = value
    for candidate_id in chain:
        current = execute_program(items_by_id[candidate_id]["program"], current)
    return current


def run_heterogeneous_observed_composition(root: Path, seed: str) -> dict[str, Any]:
    """Autonomously generate a heterogeneous composed target from observed durable skills.

    Four independently regression-verified seed-generated capabilities are installed by type signature,
    not by semantic role name: numeric→numeric, numeric→object, sequence→numeric, and object→boolean.
    A fresh planner then discovers the verified library without caller Candidate IDs, mechanically
    enumerates bounded compatible two-stage behaviors across multiple domain signatures, and selects a
    unique non-constant behavior that no one-stage route realizes. The target examples are generated from
    that observed behavior, and ordinary verified-library synthesis must rediscover the same route.

    Separately, the planner chooses an observed pair whose types cannot form the requested chain and
    verifies the lower-level typed synthesizer fails closed with no type-correct route. Post-selection
    challenges use fresh Engine runs and preserve Candidate bytes and trial ledgers. This remains bounded
    repository-authored internal development evidence and is inadmissible for the external AGI gate.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    generated_specs = _generated_goal_specs(f"{seed}:heterogeneous-library")
    required_signatures = (
        ("numeric", "numeric"),
        ("numeric", "object"),
        ("sequence", "numeric"),
        ("object", "boolean"),
    )
    promoted_ids: list[str] = []
    for input_domain, output_domain in required_signatures:
        matching = [
            spec
            for spec in generated_specs
            if spec["input_domain"] == input_domain and spec["output_domain"] == output_domain
        ]
        if not matching:
            raise HeterogeneousObservedCompositionError(
                f"generated library lacks required signature {input_domain}->{output_domain}"
            )
        promoted = _promote_task(
            root,
            seed=f"{seed}:promote:{input_domain}:{output_domain}",
            spec=matching[0],
        )
        if not promoted.get("negative_evidence_retained"):
            raise HeterogeneousObservedCompositionError("promotion lost protected negative evidence")
        promoted_ids.append(str(promoted["candidate_id"]))

    if len(set(promoted_ids)) != len(promoted_ids):
        raise HeterogeneousObservedCompositionError("heterogeneous promotions reused Candidate identity")

    all_ids = _all_candidate_ids(root)
    source_tree = _tree_snapshot(root, all_ids)
    source_trials = _trial_snapshot(root, all_ids)
    if not all(source_trials.get(candidate_id) for candidate_id in promoted_ids):
        raise HeterogeneousObservedCompositionError("promoted heterogeneous skill lacks trials")

    with tempfile.TemporaryDirectory(prefix="agi-heterogeneous-observed-") as temporary:
        fresh_root = Path(temporary).resolve()
        _copy_persistent_state(root, fresh_root)
        prior_runs_copied = (fresh_root / ".continual" / "runs").exists()
        prior_episodes_copied = (fresh_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (fresh_root / ".continual" / "evidence").exists()
        if _tree_snapshot(fresh_root, all_ids) != source_tree:
            raise HeterogeneousObservedCompositionError("fresh planner changed Candidate bytes")
        if _trial_snapshot(fresh_root, all_ids) != source_trials:
            raise HeterogeneousObservedCompositionError("fresh planner changed Candidate trials")

        library_ids = discover_verified_acquired_program_ids(fresh_root, max_candidates=64)
        items = _load_verified_macro_items(fresh_root, library_ids)
        items_by_id = {str(item["candidate_id"]): item for item in items}
        inputs_by_domain = {
            domain: _planning_inputs(domain, seed)
            for domain in _SUPPORTED_PROBE_DOMAINS
        }

        singles: dict[tuple[str, str, str], list[str]] = {}
        for item in items:
            input_domain = str(item["input_domain"])
            output_domain = str(item["output_domain"])
            if input_domain not in inputs_by_domain:
                continue
            outputs = [
                execute_program(item["program"], value)
                for value in inputs_by_domain[input_domain]
            ]
            singles.setdefault(
                (input_domain, output_domain, _canonical(outputs)),
                [],
            ).append(str(item["candidate_id"]))

        pair_records: list[dict[str, Any]] = []
        grouped: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
        for first in items:
            for second in items:
                first_id = str(first["candidate_id"])
                second_id = str(second["candidate_id"])
                if first_id == second_id:
                    continue
                if first["output_domain"] != second["input_domain"]:
                    continue
                first_signature = (str(first["input_domain"]), str(first["output_domain"]))
                second_signature = (str(second["input_domain"]), str(second["output_domain"]))
                if first_signature == second_signature:
                    continue
                input_domain = first_signature[0]
                output_domain = second_signature[1]
                if input_domain not in inputs_by_domain:
                    continue
                planning_inputs = inputs_by_domain[input_domain]
                outputs = [
                    _run_chain(items_by_id, (first_id, second_id), value)
                    for value in planning_inputs
                ]
                key = (input_domain, output_domain, _canonical(outputs))
                grouped.setdefault(key, []).append((first_id, second_id))
                pair_records.append(
                    {
                        "pair": (first_id, second_id),
                        "stage_signatures": (first_signature, second_signature),
                        "input_domain": input_domain,
                        "output_domain": output_domain,
                        "planning_inputs": planning_inputs,
                        "outputs": outputs,
                        "behavior_key": key,
                    }
                )

        unique_records: list[dict[str, Any]] = []
        for record in pair_records:
            key = record["behavior_key"]
            if len(grouped[key]) != 1:
                continue
            if key in singles:
                continue
            if len({_canonical(value) for value in record["outputs"]}) < 2:
                continue
            unique_records.append(record)
        if not unique_records:
            raise HeterogeneousObservedCompositionError(
                "heterogeneous library exposed no unique non-constant two-stage target"
            )

        choice_commitment = _digest(
            {
                "seed": seed,
                "library_ids": sorted(library_ids),
                "unique_targets": [
                    {
                        "pair": list(record["pair"]),
                        "stage_signatures": [list(value) for value in record["stage_signatures"]],
                        "input_domain": record["input_domain"],
                        "output_domain": record["output_domain"],
                        "outputs": record["outputs"],
                    }
                    for record in unique_records
                ],
                "phase": "heterogeneous-observed-target-choice-v1",
            }
        )
        target = unique_records[int(choice_commitment[:8], 16) % len(unique_records)]
        hidden_pair = tuple(str(value) for value in target["pair"])
        planning_examples = tuple(
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
            examples=planning_examples,
            max_depth=2,
            max_candidates=64,
        )
        if synthesized.get("candidate_ids_supplied_by_caller") is not False:
            raise HeterogeneousObservedCompositionError("synthesis relied on caller Candidate IDs")
        selected_ids = tuple(str(value) for value in synthesized["selected_candidate_ids"])
        if int(synthesized["selected_chain_depth"]) != 2:
            raise HeterogeneousObservedCompositionError("selected heterogeneous target was not two-stage")
        if selected_ids != hidden_pair:
            raise HeterogeneousObservedCompositionError(
                "verified library did not rediscover the uniquely observed heterogeneous route"
            )
        selected_signatures = tuple(
            (items_by_id[candidate_id]["input_domain"], items_by_id[candidate_id]["output_domain"])
            for candidate_id in selected_ids
        )
        if len(set(selected_signatures)) != 2:
            raise HeterogeneousObservedCompositionError("selected stages do not span distinct signatures")

        incompatible_pair: tuple[dict[str, Any], dict[str, Any]] | None = None
        for first in items:
            for second in items:
                if first["candidate_id"] == second["candidate_id"]:
                    continue
                if first["input_domain"] not in _SUPPORTED_PROBE_DOMAINS:
                    continue
                if first["output_domain"] == second["input_domain"]:
                    continue
                target_input = str(first["input_domain"])
                target_output = str(second["output_domain"])
                if first["input_domain"] == target_input and first["output_domain"] == target_output:
                    continue
                if second["input_domain"] == target_input and second["output_domain"] == target_output:
                    continue
                if second["input_domain"] == target_input:
                    continue
                incompatible_pair = (first, second)
                break
            if incompatible_pair is not None:
                break
        if incompatible_pair is None:
            raise HeterogeneousObservedCompositionError("no incompatible pair available for fail-closed test")

        bad_first, bad_second = incompatible_pair
        bad_input_domain = str(bad_first["input_domain"])
        bad_output_domain = str(bad_second["output_domain"])
        bad_inputs = inputs_by_domain[bad_input_domain][:2]
        incompatible_failed_closed = False
        try:
            synthesize_verified_macro_program(
                fresh_root,
                candidate_ids=[str(bad_first["candidate_id"]), str(bad_second["candidate_id"])],
                input_domain=bad_input_domain,
                output_domain=bad_output_domain,
                examples=tuple(
                    ProgramExample(value, _dummy_output(bad_output_domain, index))
                    for index, value in enumerate(bad_inputs)
                ),
                max_depth=2,
            )
        except VerifiedMacroSynthesisError as exc:
            incompatible_failed_closed = "no type-correct verified macro chain" in str(exc)
        if not incompatible_failed_closed:
            raise HeterogeneousObservedCompositionError("incompatible observed pair did not fail closed")

        challenge_commitment = _digest(
            {
                "choice_commitment": choice_commitment,
                "synthesis_digest": synthesized["digest"],
                "phase": "heterogeneous-observed-post-selection-v1",
            }
        )
        challenge_inputs = _challenge_inputs(str(target["input_domain"]), challenge_commitment)
        if any(value in target["planning_inputs"] for value in challenge_inputs):
            raise HeterogeneousObservedCompositionError("challenge overlaps planning inputs")
        expected_outputs = [
            _run_chain(items_by_id, hidden_pair, value)
            for value in challenge_inputs
        ]
        selected_items = _load_verified_macro_items(fresh_root, list(selected_ids))
        fresh_run_ids: list[str] = []
        outputs: list[dict[str, Any]] = []
        for value, expected in zip(challenge_inputs, expected_outputs, strict=True):
            compiled_output = execute_program(synthesized["compiled_program"], value)
            runtime_output, run_id, refs = _execute_chain(fresh_root, selected_items, value)
            if compiled_output != expected or runtime_output != expected:
                raise HeterogeneousObservedCompositionError("heterogeneous route failed challenge")
            if len(refs) != 2:
                raise HeterogeneousObservedCompositionError("challenge did not execute two learned stages")
            fresh_run_ids.append(run_id)
            outputs.append({"input": value, "expected": expected, "output": runtime_output})

        if len(set(fresh_run_ids)) != len(fresh_run_ids):
            raise HeterogeneousObservedCompositionError("challenge reused a fresh Engine run")
        if _tree_snapshot(fresh_root, all_ids) != source_tree:
            raise HeterogeneousObservedCompositionError("fresh challenge changed Candidate bytes")
        if _trial_snapshot(fresh_root, all_ids) != source_trials:
            raise HeterogeneousObservedCompositionError("fresh challenge changed Candidate trials")

    if _tree_snapshot(root, all_ids) != source_tree:
        raise HeterogeneousObservedCompositionError("fresh transfer mutated source Candidate bytes")
    if _trial_snapshot(root, all_ids) != source_trials:
        raise HeterogeneousObservedCompositionError("fresh transfer mutated source Candidate trials")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "heterogeneous-observed-composition-v1",
        "promoted_signature_count": len(required_signatures),
        "promoted_candidate_ids": promoted_ids,
        "caller_supplied_candidate_ids": False,
        "planner_selected_by_semantic_role_name": False,
        "observed_unique_target_count": len(unique_records),
        "choice_commitment": choice_commitment,
        "selected_candidate_ids": list(selected_ids),
        "selected_stage_signatures": [list(value) for value in selected_signatures],
        "selected_distinct_stage_signature_count": len(set(selected_signatures)),
        "selected_chain_depth": int(synthesized["selected_chain_depth"]),
        "shortest_type_chain_depth": int(synthesized["shortest_type_chain_depth"]),
        "incompatible_pair_candidate_ids": [
            str(bad_first["candidate_id"]),
            str(bad_second["candidate_id"]),
        ],
        "incompatible_pair_failed_closed": incompatible_failed_closed,
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
            "Internal bounded heterogeneous observed-capability composition evidence only. A fresh planner "
            "selected a non-constant two-stage target from mechanically observed durable behavior across "
            "distinct type signatures without caller Candidate IDs or semantic role names, and an "
            "incompatible observed pair failed closed. Repository-authored generation, regression, search, "
            "runtime, and scoring do not establish independent production evidence or AGI."
        ),
    }
    if not all(
        (
            report["incompatible_pair_failed_closed"],
            report["fresh_engine_runs_unique"],
            report["selected_distinct_stage_signature_count"] >= 2,
            not report["prior_runs_copied"],
            not report["prior_episodes_copied"],
            not report["prior_evidence_copied"],
        )
    ):
        raise HeterogeneousObservedCompositionError("heterogeneous aggregate invariant failed")
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "heterogeneous-observed-composition"
        / f"composition-{_digest(seed)[:16]}.json",
        report,
    )
    return report
