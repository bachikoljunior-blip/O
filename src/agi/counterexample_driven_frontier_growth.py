from __future__ import annotations

from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample
from agi.adaptive_depth_composition import AdaptiveDepthCompositionError, synthesize_shallowest_verified_route
from agi.durable_state_rehydration import _tree_snapshot
from agi.dynamic_behavior_frontier import (
    _mutate_output,
    _public_target,
    _runtime_admissible_behavior_frontier,
)
from agi.dynamic_skillgraph_growth_campaign import run_dynamic_skillgraph_growth_campaign
from agi.dynamic_skillgraph_input_domains import _execute_dynamic_target
from agi.heterogeneous_retention_campaign import _digest
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.seed_committed_gap_acquisition import run_seed_committed_gap_acquisition


class CounterexampleDrivenFrontierGrowthError(RuntimeError):
    pass


def _frontier_fingerprints(frontier_state: dict[str, Any]) -> set[str]:
    return {
        str(item["behavior_fingerprint"])
        for item in frontier_state["frontier"]
    }


def run_counterexample_driven_frontier_growth(root: Path, seed: str) -> dict[str, Any]:
    """Require one fail-closed generated gap to create and transfer genuinely new frontier behavior.

    The campaign first constructs the previously verified dynamic three-domain graph and freezes its
    runtime-admissible multistage behavior frontier. It then uses the existing seed-committed generic
    grammar to select a target that the current library demonstrably cannot solve, retains that failure
    as negative evidence, regression-gates and transactionally commits exactly one learned Candidate,
    and recomputes the *same seeded* behavior frontier.

    Success requires the new Candidate to introduce at least one behavior fingerprint absent from the
    frozen frontier, to increase the frontier cardinality, and to participate in a newly introduced
    multistage behavior that is rediscovered without caller Candidate IDs and replayed on fresh held-back
    Engine runs. Prior Candidate bytes/trials remain immutable. The acquisition's untouched generated
    control gap and a post-growth contradictory counterfactual must both remain fail-closed. Runtime
    budgets and held-back challenge shapes are not widened.

    This is bounded repository-authored internal development evidence. It does not establish open-domain
    target generation, independent production evaluation, or AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    bootstrap = run_dynamic_skillgraph_growth_campaign(root, f"{seed}:bootstrap")
    if not bootstrap.get("passed"):
        raise CounterexampleDrivenFrontierGrowthError("dynamic skill-graph bootstrap did not pass")

    frontier_seed = f"{seed}:shared-frontier"
    before = _runtime_admissible_behavior_frontier(root, frontier_seed)
    before_fingerprints = _frontier_fingerprints(before)
    prior_ids = _all_candidate_ids(root)
    prior_tree = _tree_snapshot(root, prior_ids)
    prior_trials = _trial_snapshot(root, prior_ids)
    if not prior_ids or not all(prior_trials.get(candidate_id) for candidate_id in prior_ids):
        raise CounterexampleDrivenFrontierGrowthError(
            "pre-growth frontier lacks durable Candidate trial evidence"
        )

    acquisition = run_seed_committed_gap_acquisition(
        root,
        f"{seed}:counterexample-acquisition",
        max_target_attempts=16,
    )
    if not acquisition.get("passed"):
        raise CounterexampleDrivenFrontierGrowthError("counterexample-driven acquisition did not pass")
    candidate_id = str(acquisition["candidate_id"])
    if candidate_id in prior_ids:
        raise CounterexampleDrivenFrontierGrowthError("growth reused a prior Candidate identity")
    if not acquisition.get("acquisition_target_failed_closed_before_learning"):
        raise CounterexampleDrivenFrontierGrowthError(
            "acquisition target was not a genuine pre-learning fail-closed gap"
        )
    if not acquisition.get("new_candidate_negative_evidence_retained"):
        raise CounterexampleDrivenFrontierGrowthError(
            "acquisition did not retain its negative evidence"
        )
    if not acquisition.get("control_gap_remained_failed_closed"):
        raise CounterexampleDrivenFrontierGrowthError(
            "untouched generated control gap did not remain fail-closed"
        )
    if _tree_snapshot(root, prior_ids) != prior_tree:
        raise CounterexampleDrivenFrontierGrowthError(
            "counterexample acquisition changed prior Candidate bytes"
        )
    if _trial_snapshot(root, prior_ids) != prior_trials:
        raise CounterexampleDrivenFrontierGrowthError(
            "counterexample acquisition changed prior Candidate trials"
        )

    enlarged_ids = _all_candidate_ids(root)
    if candidate_id not in enlarged_ids:
        raise CounterexampleDrivenFrontierGrowthError(
            "new Candidate was not retained in the durable verified graph"
        )
    enlarged_tree = _tree_snapshot(root, enlarged_ids)
    enlarged_trials = _trial_snapshot(root, enlarged_ids)
    if not all(enlarged_trials.get(item_id) for item_id in enlarged_ids):
        raise CounterexampleDrivenFrontierGrowthError(
            "post-growth graph lacks durable Candidate trial evidence"
        )

    after = _runtime_admissible_behavior_frontier(root, frontier_seed)
    after_fingerprints = _frontier_fingerprints(after)
    added_fingerprints = sorted(after_fingerprints - before_fingerprints)
    if int(after["frontier_behavior_count"]) <= int(before["frontier_behavior_count"]):
        raise CounterexampleDrivenFrontierGrowthError(
            "counterexample-driven learning did not increase behavior-frontier cardinality"
        )
    if not added_fingerprints:
        raise CounterexampleDrivenFrontierGrowthError(
            "counterexample-driven learning introduced no new behavior fingerprint"
        )

    added_set = set(added_fingerprints)
    eligible = [
        dict(item)
        for item in after["frontier"]
        if str(item["behavior_fingerprint"]) in added_set
        and candidate_id in {str(value) for value in item["candidate_ids"]}
    ]
    if not eligible:
        raise CounterexampleDrivenFrontierGrowthError(
            "new Candidate introduced no selectable multistage frontier behavior"
        )
    eligible.sort(
        key=lambda item: _digest(
            {
                "seed": seed,
                "candidate_id": candidate_id,
                "behavior_fingerprint": item["behavior_fingerprint"],
                "candidate_ids": list(item["candidate_ids"]),
                "phase": "counterexample-frontier-growth-target-rank-v1",
            }
        )
    )
    selected = eligible[0]
    target = _public_target(0, selected)
    plan_commitment = _digest(
        {
            "seed": seed,
            "before_registry_digest": before["frontier_registry_digest"],
            "after_registry_digest": after["frontier_registry_digest"],
            "acquisition_target_fingerprint": acquisition["acquisition_behavior_fingerprint"],
            "selected_new_behavior_fingerprint": target["behavior_fingerprint"],
            "phase": "counterexample-frontier-growth-plan-v1",
        }
    )

    transfer = _execute_dynamic_target(
        root,
        target,
        plan_commitment=plan_commitment,
        source_ids=enlarged_ids,
        source_tree=enlarged_tree,
        source_trials=enlarged_trials,
    )
    if candidate_id not in {
        str(value) for value in transfer["generator_hidden_candidate_ids"]
    }:
        raise CounterexampleDrivenFrontierGrowthError(
            "selected new behavior did not actually depend on the learned Candidate"
        )
    if candidate_id not in {
        str(value) for value in transfer["solver_selected_candidate_ids"]
    }:
        raise CounterexampleDrivenFrontierGrowthError(
            "fresh behavior-only solver did not rediscover the learned Candidate"
        )
    if int(transfer["solver_selected_chain_depth"]) < 2:
        raise CounterexampleDrivenFrontierGrowthError(
            "new frontier behavior collapsed to a one-stage route"
        )

    support = list(target["support_examples"])
    if not support:
        raise CounterexampleDrivenFrontierGrowthError("new target has no support examples")
    original = support[0]
    mutated_output = _mutate_output(original["output"], str(target["output_domain"]))
    contradictory_examples = (
        ProgramExample(original["input"], original["output"]),
        ProgramExample(original["input"], mutated_output),
    )
    counterfactual_failure: str | None = None
    try:
        synthesize_shallowest_verified_route(
            root,
            input_domain=str(target["input_domain"]),
            output_domain=str(target["output_domain"]),
            examples=contradictory_examples,
            max_depth=4,
            max_candidates=192,
            max_search_nodes=8192,
            max_behavior_evaluations=4096,
        )
    except (AdaptiveDepthCompositionError, ValueError) as exc:
        counterfactual_failure = f"{type(exc).__name__}: {exc}"
    if counterfactual_failure is None:
        raise CounterexampleDrivenFrontierGrowthError(
            "post-growth contradictory counterfactual unexpectedly became supported"
        )

    if _tree_snapshot(root, enlarged_ids) != enlarged_tree:
        raise CounterexampleDrivenFrontierGrowthError(
            "frontier transfer/control changed durable Candidate bytes"
        )
    if _trial_snapshot(root, enlarged_ids) != enlarged_trials:
        raise CounterexampleDrivenFrontierGrowthError(
            "frontier transfer/control changed durable Candidate trials"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "counterexample-driven-frontier-growth-v1",
        "seed": seed,
        "frontier_seed_reused_before_and_after_learning": True,
        "before_frontier_registry_digest": str(before["frontier_registry_digest"]),
        "after_frontier_registry_digest": str(after["frontier_registry_digest"]),
        "before_frontier_behavior_count": int(before["frontier_behavior_count"]),
        "after_frontier_behavior_count": int(after["frontier_behavior_count"]),
        "frontier_cardinality_increased": (
            int(after["frontier_behavior_count"]) > int(before["frontier_behavior_count"])
        ),
        "new_behavior_fingerprints": added_fingerprints,
        "new_behavior_count": len(added_fingerprints),
        "counterexample_schedule_precommitted_before_support_checks": bool(
            acquisition["target_schedule_precommitted_before_support_checks"]
        ),
        "counterexample_schedule_commitment": str(acquisition["target_schedule_commitment"]),
        "acquisition_target_failed_closed_before_learning": bool(
            acquisition["acquisition_target_failed_closed_before_learning"]
        ),
        "acquisition_behavior_fingerprint": str(
            acquisition["acquisition_behavior_fingerprint"]
        ),
        "new_candidate_id": candidate_id,
        "new_candidate_negative_evidence_retained": bool(
            acquisition["new_candidate_negative_evidence_retained"]
        ),
        "new_candidate_transactional_commit": acquisition["transactional_commit"],
        "untouched_generated_control_gap_failed_closed": bool(
            acquisition["control_gap_remained_failed_closed"]
        ),
        "prior_candidate_state_unchanged": True,
        "prior_trial_state_unchanged": True,
        "runtime_budget_unchanged": bool(
            before["runtime_budget_unchanged"] and after["runtime_budget_unchanged"]
        ),
        "heldback_challenge_shape_unchanged": bool(
            before["heldback_challenge_shape_unchanged"]
            and after["heldback_challenge_shape_unchanged"]
        ),
        "selected_new_target": {
            "behavior_fingerprint": str(target["behavior_fingerprint"]),
            "input_domain": str(target["input_domain"]),
            "output_domain": str(target["output_domain"]),
            "generator_hidden_depth": int(target["generator_hidden_depth"]),
            "learned_candidate_in_hidden_route": candidate_id
            in {str(value) for value in target["generator_hidden_candidate_ids"]},
        },
        "new_behavior_transfer": transfer,
        "solver_candidate_ids_supplied_by_caller": bool(
            transfer["solver_candidate_ids_supplied_by_caller"]
        ),
        "new_candidate_rediscovered_by_behavior_only_solver": candidate_id
        in {str(value) for value in transfer["solver_selected_candidate_ids"]},
        "new_behavior_transfer_fresh_engine_runs_unique": bool(
            transfer["fresh_engine_runs_unique"]
        ),
        "source_candidate_state_unchanged": True,
        "source_trial_state_unchanged": True,
        "post_growth_counterfactual_control": {
            "failed_closed": True,
            "failure": counterfactual_failure,
        },
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded counterexample-driven capability-growth evidence only. A seed-committed "
            "generic grammar target must first fail closed, its failure is retained, one exact-scope "
            "regression-gated Candidate is transactionally committed, and the same seeded runtime-"
            "admissible behavior frontier must then strictly expand with a new multistage behavior "
            "that is rediscovered without caller Candidate IDs and replayed on fresh Engines. Prior "
            "Candidate bytes/trials, runtime budgets, held-back challenge shapes, an untouched "
            "generated gap, and a contradictory counterfactual remain protected. Generator, grammar, "
            "learning, search, runtime, regression, scoring, and evaluator remain repository-authored; "
            "this does not establish open-domain target generation, independent production evaluation, "
            "or AGI."
        ),
    }
    if not all(
        (
            report["frontier_seed_reused_before_and_after_learning"],
            report["frontier_cardinality_increased"],
            report["new_behavior_count"] >= 1,
            report["counterexample_schedule_precommitted_before_support_checks"],
            report["acquisition_target_failed_closed_before_learning"],
            report["new_candidate_negative_evidence_retained"],
            report["untouched_generated_control_gap_failed_closed"],
            report["prior_candidate_state_unchanged"],
            report["prior_trial_state_unchanged"],
            report["runtime_budget_unchanged"],
            report["heldback_challenge_shape_unchanged"],
            report["selected_new_target"]["learned_candidate_in_hidden_route"],
            report["solver_candidate_ids_supplied_by_caller"] is False,
            report["new_candidate_rediscovered_by_behavior_only_solver"],
            report["new_behavior_transfer_fresh_engine_runs_unique"],
            report["source_candidate_state_unchanged"],
            report["source_trial_state_unchanged"],
            report["post_growth_counterfactual_control"]["failed_closed"],
        )
    ):
        raise CounterexampleDrivenFrontierGrowthError(
            "counterexample-driven frontier-growth aggregate invariant failed"
        )

    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "counterexample-driven-frontier-growth"
        / f"growth-{_digest(seed)[:16]}.json",
        report,
    )
    return report
