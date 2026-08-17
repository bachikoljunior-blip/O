from __future__ import annotations

from itertools import permutations
from pathlib import Path
from typing import Any, Mapping, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
)


def _planner_specs() -> tuple[dict[str, Any], ...]:
    return (
        {
            "name": "planner-abs",
            "input_domain": "numeric",
            "output_domain": "numeric",
            "examples": (
                ProgramExample(-3, 3),
                ProgramExample(0, 0),
                ProgramExample(5, 5),
            ),
            "probe": -11,
            "expected": 11,
            "max_nodes": 3,
        },
        {
            "name": "planner-neg",
            "input_domain": "numeric",
            "output_domain": "numeric",
            "examples": (
                ProgramExample(2, -2),
                ProgramExample(-4, 4),
                ProgramExample(0, 0),
            ),
            "probe": 11,
            "expected": -11,
            "max_nodes": 3,
        },
        {
            "name": "planner-object",
            "input_domain": "numeric",
            "output_domain": "object",
            "examples": (
                ProgramExample(2, {"score": 2}),
                ProgramExample(-3, {"score": -3}),
                ProgramExample(7, {"score": 7}),
            ),
            "probe": 11,
            "expected": {"score": 11},
            "max_nodes": 3,
        },
        {
            "name": "planner-threshold",
            "input_domain": "object",
            "output_domain": "boolean",
            "examples": (
                ProgramExample({"score": -2}, False),
                ProgramExample({"score": 4}, False),
                ProgramExample({"score": 5}, True),
                ProgramExample({"score": 12}, True),
            ),
            "probe": {"score": 11},
            "expected": True,
            "max_nodes": 6,
        },
    )


def _typed_chains(
    promoted: Sequence[dict[str, Any]],
    *,
    input_domain: str,
    output_domain: str,
    max_depth: int,
) -> list[tuple[dict[str, Any], ...]]:
    chains: list[tuple[dict[str, Any], ...]] = []
    for depth in range(1, max_depth + 1):
        for chain in permutations(promoted, depth):
            current_domain = input_domain
            valid = True
            for item in chain:
                if str(item["input_domain"]) != current_domain:
                    valid = False
                    break
                current_domain = str(item["output_domain"])
            if valid and current_domain == output_domain:
                chains.append(chain)
    return chains


def _runtime_output(response: Mapping[str, Any], *, candidate_id: str) -> Any:
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise RuntimeError("tool-chain discovery received malformed learned-tool output")
    if result.get("candidate_id") != candidate_id:
        raise RuntimeError("tool-chain discovery invoked an unexpected Candidate")
    if result.get("program_kind") != "acquired_program":
        raise RuntimeError("tool-chain discovery left the acquired-program execution path")
    if "output" not in result:
        raise RuntimeError("tool-chain discovery learned-tool output is missing output")
    return result["output"]


def _execute_chain(
    root: Path,
    chain: Sequence[dict[str, Any]],
    value: Any,
) -> tuple[Any, str, list[dict[str, Any]]]:
    engine = _mechanical_engine(root)
    run_id = _new_runtime_run(engine)
    current = value
    refs: list[dict[str, Any]] = []
    for index, item in enumerate(chain):
        input_value = current
        response = _invoke(
            engine,
            run_id,
            scope=str(item["scope"]),
            tool_id=str(item["tool_id"]),
            value=input_value,
        )
        current = _runtime_output(response, candidate_id=str(item["candidate_id"]))
        refs.append(
            {
                "stage_index": index,
                "candidate_id": item["candidate_id"],
                "scope": item["scope"],
                "input_sha256": _digest(input_value),
                "output_sha256": _digest(current),
            }
        )
    return current, run_id, refs


def _chain_matches(
    root: Path,
    chain: Sequence[dict[str, Any]],
    examples: Sequence[ProgramExample],
) -> bool:
    for example in examples:
        output, _run_id, _refs = _execute_chain(root, chain, example.input)
        if output != example.output:
            return False
    return True


def run_behavior_guided_tool_chain_discovery(root: Path, seed: str) -> dict[str, Any]:
    """Discover a learned-tool chain by type compatibility plus committed behavior examples.

    The campaign intentionally includes a shorter type-correct chain that is behaviorally wrong and a
    same-depth numeric distractor. The planner must therefore do more than choose the shortest typed
    route. It enumerates finite no-repeat typed chains, evaluates them against planning examples that
    were not used for synthesis, selects the shortest behaviorally consistent chain, and only then
    derives a separate challenge family. Every selected-chain challenge is replayed through fresh
    mechanical Engines while Candidate trial ledgers remain byte-identical.

    This is internal bounded composition evidence. The chain search, planning examples, and scorer are
    repository-authored and credential-free, so the result does not establish open-world autonomous
    planning, independent production evaluation, or AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    specs = _planner_specs()
    promoted = tuple(
        _promote_task(root, seed=f"{seed}:behavior-guided-chain", spec=spec)
        for spec in specs
    )
    candidate_ids = [str(item["candidate_id"]) for item in promoted]
    if len(set(candidate_ids)) != len(candidate_ids):
        raise RuntimeError("tool-chain discovery reused a Candidate identity")

    trial_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in candidate_ids
    }
    if not all(trial_before.values()):
        raise RuntimeError("tool-chain discovery Candidate lacks regression trial evidence")

    synthesis_numeric_inputs = {
        example.input
        for spec in specs
        if spec["input_domain"] == "numeric"
        for example in spec["examples"]
    }
    planning_examples = (
        ProgramExample(-8, True),
        ProgramExample(-1, False),
        ProgramExample(9, True),
    )
    planning_inputs = {example.input for example in planning_examples}
    if planning_inputs & synthesis_numeric_inputs:
        raise RuntimeError("tool-chain planning examples overlap synthesis support")

    chains = _typed_chains(
        promoted,
        input_domain="numeric",
        output_domain="boolean",
        max_depth=4,
    )
    if not chains:
        raise RuntimeError("tool-chain discovery found no type-correct route")
    shortest_type_depth = min(len(chain) for chain in chains)

    matching: list[tuple[dict[str, Any], ...]] = []
    nonmatching_by_depth: dict[int, int] = {}
    for chain in chains:
        if _chain_matches(root, chain, planning_examples):
            matching.append(chain)
        else:
            nonmatching_by_depth[len(chain)] = nonmatching_by_depth.get(len(chain), 0) + 1
    if not matching:
        raise RuntimeError("no typed learned-tool chain matches committed planning behavior")

    matching.sort(
        key=lambda chain: (
            len(chain),
            tuple(str(item["candidate_id"]) for item in chain),
        )
    )
    selected = matching[0]
    selected_ids = [str(item["candidate_id"]) for item in selected]
    selected_depth = len(selected)
    if selected_depth <= shortest_type_depth:
        raise RuntimeError(
            "planning behavior did not disambiguate the shortest type-only chain"
        )
    if nonmatching_by_depth.get(selected_depth, 0) < 1:
        raise RuntimeError("planner lacks a same-depth behavioral distractor")

    commitment = _digest(
        {
            "seed": seed,
            "selected_candidate_ids": selected_ids,
            "planning_examples": [
                {"input": item.input, "output": item.output}
                for item in planning_examples
            ],
            "phase": "post-discovery-challenge-v1",
        }
    )
    large_negative = -(20 + int(commitment[:2], 16) % 40)
    small_negative = -2
    large_positive = 20 + int(commitment[2:4], 16) % 40
    challenge_inputs = (large_negative, small_negative, large_positive)
    if len(set(challenge_inputs)) != len(challenge_inputs):
        raise RuntimeError("tool-chain challenge generator produced duplicate inputs")
    if set(challenge_inputs) & (planning_inputs | synthesis_numeric_inputs):
        raise RuntimeError("tool-chain challenge overlaps planning or synthesis support")

    expected_outputs = [abs(value) >= 5 for value in challenge_inputs]
    fresh_engine_runs: list[str] = []
    chain_refs: list[dict[str, Any]] = []
    outputs: list[bool] = []
    for engine_index in range(3):
        for case_index, (value, expected) in enumerate(
            zip(challenge_inputs, expected_outputs, strict=True)
        ):
            output, run_id, refs = _execute_chain(root, selected, value)
            if not isinstance(output, bool):
                raise RuntimeError("selected learned-tool chain did not terminate in boolean output")
            if output != expected:
                raise RuntimeError("selected learned-tool chain failed post-discovery transfer")
            fresh_engine_runs.append(run_id)
            outputs.append(output)
            chain_refs.extend(
                {
                    "engine_index": engine_index,
                    "case_index": case_index,
                    "challenge_input_sha256": _digest(value),
                    "run_id": run_id,
                    **ref,
                }
                for ref in refs
            )

    trial_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in candidate_ids
    }
    trial_state_unchanged = trial_after == trial_before
    if not trial_state_unchanged:
        raise RuntimeError("tool-chain discovery changed Candidate trial state")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "behavior-guided-learned-tool-chain-discovery",
        "candidate_count": len(promoted),
        "candidate_ids": candidate_ids,
        "typed_chain_count": len(chains),
        "shortest_type_chain_depth": shortest_type_depth,
        "matching_chain_count": len(matching),
        "selected_chain_depth": selected_depth,
        "selected_candidate_ids": selected_ids,
        "same_depth_nonmatching_chain_count": nonmatching_by_depth.get(selected_depth, 0),
        "planning_example_count": len(planning_examples),
        "planning_examples_overlap_synthesis_support": False,
        "challenge_generated_after_selection": True,
        "challenge_commitment": commitment,
        "challenge_inputs_overlap_planning_or_support": False,
        "challenge_case_count": len(challenge_inputs),
        "fresh_engine_chain_count": len(fresh_engine_runs),
        "fresh_engine_runs": fresh_engine_runs,
        "stage_invocation_count": len(chain_refs),
        "all_challenge_outputs_matched": outputs
        == expected_outputs * 3,
        "all_negative_evidence_retained": all(
            bool(item["negative_evidence_retained"]) for item in promoted
        ),
        "candidate_trial_state_unchanged": trial_state_unchanged,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded behavior-guided learned-tool composition evidence only. A finite planner "
            "selected a semantically correct typed chain despite a shorter type-correct wrong route "
            "and a same-depth distractor, then transferred on challenges generated after selection "
            "across fresh mechanical Engines with unchanged Candidate trial ledgers. Planning examples, "
            "search, scoring, and runtime remain repository-authored and credential-free, so this does "
            "not establish open-world autonomous planning, AGI, or the strict independent production "
            "evidence gate."
        ),
    }
    if report["all_challenge_outputs_matched"] is not True:
        raise RuntimeError("tool-chain challenge aggregate output ordering is inconsistent")
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "behavior-guided-tool-chain-discovery"
        / f"tool-chain-discovery-{_digest(seed)[:16]}.json",
        report,
    )
    return report
