from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample
from agi.behavior_guided_tool_chain_discovery import (
    _chain_matches,
    _execute_chain,
    _planner_specs,
    _typed_chains,
)
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.materialized_runtime_replay import _candidate_trial_snapshot


def _select_chain(
    root: Path,
    chains: Sequence[tuple[dict[str, Any], ...]],
    examples: Sequence[ProgramExample],
) -> tuple[dict[str, Any], ...]:
    matching = [chain for chain in chains if _chain_matches(root, chain, examples)]
    if not matching:
        raise RuntimeError("task-conditioned planner found no behaviorally valid chain")
    matching.sort(
        key=lambda chain: (
            len(chain),
            tuple(str(item["candidate_id"]) for item in chain),
        )
    )
    minimum = len(matching[0])
    minimal = [chain for chain in matching if len(chain) == minimum]
    if len(minimal) != 1:
        raise RuntimeError("task-conditioned planning support is ambiguous across minimal chains")
    return minimal[0]


def _candidate_by_name(
    promoted: Sequence[dict[str, Any]],
    name: str,
) -> dict[str, Any]:
    prefix = f"hetero-{name}-"
    matches = [item for item in promoted if str(item["candidate_id"]).startswith(prefix)]
    if len(matches) != 1:
        raise RuntimeError(f"expected one promoted Candidate for {name}")
    return matches[0]


def run_task_conditioned_tool_chain_switching(root: Path, seed: str) -> dict[str, Any]:
    """Select two different learned-tool plans from one promoted capability library.

    Both goals share the same numeric input and boolean output types, so type compatibility alone
    cannot determine the plan. One goal requires absolute-value thresholding while the other requires
    negation thresholding. The finite planner must select a different first learned tool for each goal
    while reusing the same numeric-to-object and object-to-boolean stages. Goal demonstrations are
    disjoint from every Candidate's synthesis support. Only after both plans are fixed do we derive a
    shared challenge family and interleave the two plans across fresh mechanical Engines.

    This remains internal bounded planning evidence: the goals, examples, search, scorer, and runtime
    are repository-authored and credential-free and therefore cannot satisfy the independent AGI gate.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    specs = _planner_specs()
    promoted = tuple(
        _promote_task(root, seed=f"{seed}:task-conditioned-switching", spec=spec)
        for spec in specs
    )
    candidate_ids = [str(item["candidate_id"]) for item in promoted]
    if len(set(candidate_ids)) != len(candidate_ids):
        raise RuntimeError("task-conditioned switching reused a Candidate identity")

    trial_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in candidate_ids
    }
    if not all(trial_before.values()):
        raise RuntimeError("task-conditioned Candidate lacks regression evidence")

    synthesis_numeric_inputs = {
        example.input
        for spec in specs
        if spec["input_domain"] == "numeric"
        for example in spec["examples"]
    }
    absolute_goal = (
        ProgramExample(-8, True),
        ProgramExample(-1, False),
        ProgramExample(9, True),
    )
    negative_goal = (
        ProgramExample(-8, True),
        ProgramExample(-1, False),
        ProgramExample(9, False),
    )
    planning_inputs = {example.input for example in absolute_goal}
    if planning_inputs != {example.input for example in negative_goal}:
        raise RuntimeError("task-conditioned goals must use the same planning inputs")
    if planning_inputs & synthesis_numeric_inputs:
        raise RuntimeError("task-conditioned planning support overlaps synthesis support")

    chains = _typed_chains(
        promoted,
        input_domain="numeric",
        output_domain="boolean",
        max_depth=4,
    )
    if not chains:
        raise RuntimeError("task-conditioned switching found no typed plan")

    absolute_chain = _select_chain(root, chains, absolute_goal)
    negative_chain = _select_chain(root, chains, negative_goal)
    absolute_ids = [str(item["candidate_id"]) for item in absolute_chain]
    negative_ids = [str(item["candidate_id"]) for item in negative_chain]
    if absolute_ids == negative_ids:
        raise RuntimeError("different behavioral goals selected the same learned-tool plan")

    abs_candidate = _candidate_by_name(promoted, "planner-abs")
    neg_candidate = _candidate_by_name(promoted, "planner-neg")
    object_candidate = _candidate_by_name(promoted, "planner-object")
    threshold_candidate = _candidate_by_name(promoted, "planner-threshold")
    expected_absolute = [
        str(abs_candidate["candidate_id"]),
        str(object_candidate["candidate_id"]),
        str(threshold_candidate["candidate_id"]),
    ]
    expected_negative = [
        str(neg_candidate["candidate_id"]),
        str(object_candidate["candidate_id"]),
        str(threshold_candidate["candidate_id"]),
    ]
    if absolute_ids != expected_absolute:
        raise RuntimeError("absolute-value goal did not select the expected minimal learned plan")
    if negative_ids != expected_negative:
        raise RuntimeError("negation goal did not select the expected minimal learned plan")
    if absolute_ids[1:] != negative_ids[1:]:
        raise RuntimeError("task-conditioned plans failed to reuse compatible learned stages")

    commitment = _digest(
        {
            "seed": seed,
            "absolute_plan": absolute_ids,
            "negative_plan": negative_ids,
            "planning_inputs": sorted(planning_inputs),
            "phase": "post-selection-task-switching-challenge-v1",
        }
    )
    large_negative = -(20 + int(commitment[:2], 16) % 40)
    large_positive = 20 + int(commitment[2:4], 16) % 40
    challenge_inputs = (large_negative, -2, large_positive)
    if len(set(challenge_inputs)) != 3:
        raise RuntimeError("task-conditioned challenge generator produced duplicate inputs")
    if set(challenge_inputs) & (planning_inputs | synthesis_numeric_inputs):
        raise RuntimeError("task-conditioned challenge overlaps planning or synthesis support")

    goal_specs = (
        ("absolute-threshold", absolute_chain, lambda value: abs(value) >= 5),
        ("negative-threshold", negative_chain, lambda value: -value >= 5),
    )
    outputs: list[dict[str, Any]] = []
    fresh_engine_runs: list[str] = []
    stage_invocations = 0
    for repeat_index in range(3):
        for case_index, value in enumerate(challenge_inputs):
            for goal_name, chain, oracle in goal_specs:
                expected = bool(oracle(value))
                output, run_id, refs = _execute_chain(root, chain, value)
                if not isinstance(output, bool) or output != expected:
                    raise RuntimeError(
                        f"task-conditioned plan failed post-selection transfer for {goal_name}"
                    )
                fresh_engine_runs.append(run_id)
                stage_invocations += len(refs)
                outputs.append(
                    {
                        "repeat_index": repeat_index,
                        "case_index": case_index,
                        "goal": goal_name,
                        "input_sha256": _digest(value),
                        "output": output,
                        "expected": expected,
                        "run_id": run_id,
                        "stage_count": len(refs),
                    }
                )

    trial_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in candidate_ids
    }
    trial_state_unchanged = trial_after == trial_before
    if not trial_state_unchanged:
        raise RuntimeError("task-conditioned planning changed Candidate trial state")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "task-conditioned-learned-tool-plan-switching",
        "candidate_count": len(promoted),
        "candidate_ids": candidate_ids,
        "typed_chain_count": len(chains),
        "absolute_plan": absolute_ids,
        "negative_plan": negative_ids,
        "plans_are_distinct": absolute_ids != negative_ids,
        "shared_tail_stage_count": len(absolute_ids[1:]),
        "planning_example_count_per_goal": len(absolute_goal),
        "planning_examples_overlap_synthesis_support": False,
        "challenge_generated_after_both_plan_selections": True,
        "challenge_commitment": commitment,
        "challenge_inputs_overlap_planning_or_support": False,
        "challenge_case_count": len(challenge_inputs),
        "goal_count": len(goal_specs),
        "fresh_engine_run_count": len(fresh_engine_runs),
        "fresh_engine_runs_unique": len(set(fresh_engine_runs)) == len(fresh_engine_runs),
        "stage_invocation_count": stage_invocations,
        "all_outputs_matched": all(item["output"] == item["expected"] for item in outputs),
        "all_negative_evidence_retained": all(
            bool(item["negative_evidence_retained"]) for item in promoted
        ),
        "candidate_trial_state_unchanged": trial_state_unchanged,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded task-conditioned planning evidence only. The same promoted learned-tool "
            "library supported two distinct behavior-selected plans that reused compatible tail stages "
            "and transferred under interleaved post-selection challenges across fresh mechanical "
            "Engines. Goal construction, planning support, search, scoring, and runtime remain "
            "repository-authored and credential-free, so this is not open-world autonomy, AGI, or "
            "independent production evidence."
        ),
    }
    if not report["fresh_engine_runs_unique"]:
        raise RuntimeError("task-conditioned switching reused a fresh Engine run identity")
    if not report["all_outputs_matched"]:
        raise RuntimeError("task-conditioned switching aggregate output check failed")
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "task-conditioned-tool-chain-switching"
        / f"task-conditioned-switching-{_digest(seed)[:16]}.json",
        report,
    )
    return report
