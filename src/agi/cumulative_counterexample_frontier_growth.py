from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample
from agi.adaptive_depth_composition import (
    AdaptiveDepthCompositionError,
    synthesize_shallowest_verified_route,
)
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


class CumulativeCounterexampleFrontierGrowthError(RuntimeError):
    pass


def _fingerprints(frontier_state: Mapping[str, Any]) -> set[str]:
    return {
        str(item["behavior_fingerprint"])
        for item in frontier_state["frontier"]
    }


def _counterfactual_failure(
    root: Path,
    target: Mapping[str, Any],
) -> str:
    support = list(target["support_examples"])
    if not support:
        raise CumulativeCounterexampleFrontierGrowthError(
            "selected target has no support examples"
        )
    original = support[0]
    mutated = _mutate_output(original["output"], str(target["output_domain"]))
    if mutated == original["output"]:
        raise CumulativeCounterexampleFrontierGrowthError(
            "counterfactual output mutation was ineffective"
        )
    examples = (
        ProgramExample(original["input"], original["output"]),
        ProgramExample(original["input"], mutated),
    )
    try:
        synthesize_shallowest_verified_route(
            root,
            input_domain=str(target["input_domain"]),
            output_domain=str(target["output_domain"]),
            examples=examples,
            max_depth=4,
            max_candidates=192,
            max_search_nodes=8192,
            max_behavior_evaluations=4096,
        )
    except (AdaptiveDepthCompositionError, ValueError) as exc:
        return f"{type(exc).__name__}: {exc}"
    raise CumulativeCounterexampleFrontierGrowthError(
        "contradictory post-growth counterfactual unexpectedly became supported"
    )


def _select_new_target(
    frontier_state: Mapping[str, Any],
    *,
    new_fingerprints: set[str],
    candidate_id: str,
    seed: str,
) -> dict[str, Any]:
    eligible = [
        dict(item)
        for item in frontier_state["frontier"]
        if str(item["behavior_fingerprint"]) in new_fingerprints
        and candidate_id in {str(value) for value in item["candidate_ids"]}
    ]
    if not eligible:
        raise CumulativeCounterexampleFrontierGrowthError(
            "new Candidate introduced no selectable new multistage frontier behavior"
        )
    eligible.sort(
        key=lambda item: _digest(
            {
                "seed": seed,
                "candidate_id": candidate_id,
                "behavior_fingerprint": item["behavior_fingerprint"],
                "candidate_ids": list(item["candidate_ids"]),
                "phase": "cumulative-counterexample-frontier-target-rank-v1",
            }
        )
    )
    return _public_target(0, eligible[0])


def _replay_retained_targets(
    root: Path,
    targets: Sequence[Mapping[str, Any]],
    *,
    source_ids: Sequence[str],
    source_tree: Mapping[str, str],
    source_trials: Mapping[str, Mapping[str, str]],
    seed: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, target in enumerate(targets):
        replay_target = dict(target)
        replay_target["target_index"] = index
        commitment = _digest(
            {
                "seed": seed,
                "target_index": index,
                "behavior_fingerprint": target["behavior_fingerprint"],
                "phase": "cumulative-counterexample-retention-replay-v1",
            }
        )
        record = _execute_dynamic_target(
            root,
            replay_target,
            plan_commitment=commitment,
            source_ids=source_ids,
            source_tree=source_tree,
            source_trials=source_trials,
        )
        if record["solver_candidate_ids_supplied_by_caller"] is not False:
            raise CumulativeCounterexampleFrontierGrowthError(
                "retention replay relied on caller Candidate IDs"
            )
        if int(record["solver_selected_chain_depth"]) < 2:
            raise CumulativeCounterexampleFrontierGrowthError(
                "retained target collapsed to a one-stage route"
            )
        if not record["fresh_engine_runs_unique"]:
            raise CumulativeCounterexampleFrontierGrowthError(
                "retention replay reused a fresh Engine run"
            )
        records.append(record)
    return records


def _run_growth_round(
    root: Path,
    *,
    seed: str,
    frontier_seed: str,
    prior_targets: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    before = _runtime_admissible_behavior_frontier(root, frontier_seed)
    before_fingerprints = _fingerprints(before)
    prior_ids = _all_candidate_ids(root)
    prior_tree = _tree_snapshot(root, prior_ids)
    prior_trials = _trial_snapshot(root, prior_ids)
    if not prior_ids or not all(prior_trials.get(candidate_id) for candidate_id in prior_ids):
        raise CumulativeCounterexampleFrontierGrowthError(
            "round source graph lacks durable Candidate trial evidence"
        )

    acquisition = run_seed_committed_gap_acquisition(
        root,
        f"{seed}:acquisition",
        max_target_attempts=24,
    )
    if not acquisition.get("passed"):
        raise CumulativeCounterexampleFrontierGrowthError(
            "round counterexample-driven acquisition did not pass"
        )
    candidate_id = str(acquisition["candidate_id"])
    if candidate_id in prior_ids:
        raise CumulativeCounterexampleFrontierGrowthError(
            "round acquisition reused a prior Candidate identity"
        )
    if not acquisition.get("acquisition_target_failed_closed_before_learning"):
        raise CumulativeCounterexampleFrontierGrowthError(
            "round acquisition target was not a pre-learning fail-closed gap"
        )
    if not acquisition.get("new_candidate_negative_evidence_retained"):
        raise CumulativeCounterexampleFrontierGrowthError(
            "round acquisition lost negative evidence"
        )
    if not acquisition.get("control_gap_remained_failed_closed"):
        raise CumulativeCounterexampleFrontierGrowthError(
            "round untouched generated control did not remain fail-closed"
        )
    if _tree_snapshot(root, prior_ids) != prior_tree:
        raise CumulativeCounterexampleFrontierGrowthError(
            "round acquisition changed prior Candidate bytes"
        )
    if _trial_snapshot(root, prior_ids) != prior_trials:
        raise CumulativeCounterexampleFrontierGrowthError(
            "round acquisition changed prior Candidate trials"
        )

    enlarged_ids = _all_candidate_ids(root)
    if candidate_id not in enlarged_ids:
        raise CumulativeCounterexampleFrontierGrowthError(
            "round learned Candidate was not durably retained"
        )
    enlarged_tree = _tree_snapshot(root, enlarged_ids)
    enlarged_trials = _trial_snapshot(root, enlarged_ids)
    if not all(enlarged_trials.get(item_id) for item_id in enlarged_ids):
        raise CumulativeCounterexampleFrontierGrowthError(
            "round enlarged graph lacks durable trial evidence"
        )

    after = _runtime_admissible_behavior_frontier(root, frontier_seed)
    after_fingerprints = _fingerprints(after)
    added = after_fingerprints - before_fingerprints
    if int(after["frontier_behavior_count"]) <= int(before["frontier_behavior_count"]):
        raise CumulativeCounterexampleFrontierGrowthError(
            "round did not strictly increase behavior-frontier cardinality"
        )
    if not added:
        raise CumulativeCounterexampleFrontierGrowthError(
            "round introduced no new behavior fingerprint"
        )
    prior_target_fingerprints = {
        str(target["behavior_fingerprint"]) for target in prior_targets
    }
    missing_retained = sorted(prior_target_fingerprints - after_fingerprints)
    if missing_retained:
        raise CumulativeCounterexampleFrontierGrowthError(
            "later learning removed a previously selected frontier behavior: "
            + ",".join(missing_retained)
        )

    target = _select_new_target(
        after,
        new_fingerprints=added,
        candidate_id=candidate_id,
        seed=seed,
    )
    new_commitment = _digest(
        {
            "seed": seed,
            "before_registry_digest": before["frontier_registry_digest"],
            "after_registry_digest": after["frontier_registry_digest"],
            "candidate_id": candidate_id,
            "behavior_fingerprint": target["behavior_fingerprint"],
            "phase": "cumulative-counterexample-frontier-plan-v1",
        }
    )
    new_transfer = _execute_dynamic_target(
        root,
        target,
        plan_commitment=new_commitment,
        source_ids=enlarged_ids,
        source_tree=enlarged_tree,
        source_trials=enlarged_trials,
    )
    if candidate_id not in {
        str(value) for value in new_transfer["generator_hidden_candidate_ids"]
    }:
        raise CumulativeCounterexampleFrontierGrowthError(
            "round selected behavior did not depend on the new Candidate"
        )
    if candidate_id not in {
        str(value) for value in new_transfer["solver_selected_candidate_ids"]
    }:
        raise CumulativeCounterexampleFrontierGrowthError(
            "round behavior-only solver did not rediscover the new Candidate"
        )

    retained_replays = _replay_retained_targets(
        root,
        prior_targets,
        source_ids=enlarged_ids,
        source_tree=enlarged_tree,
        source_trials=enlarged_trials,
        seed=f"{seed}:retention",
    )
    counterfactual_failure = _counterfactual_failure(root, target)
    if _tree_snapshot(root, enlarged_ids) != enlarged_tree:
        raise CumulativeCounterexampleFrontierGrowthError(
            "round transfers changed Candidate bytes"
        )
    if _trial_snapshot(root, enlarged_ids) != enlarged_trials:
        raise CumulativeCounterexampleFrontierGrowthError(
            "round transfers changed Candidate trials"
        )

    round_report: dict[str, Any] = {
        "seed": seed,
        "before_frontier_behavior_count": int(before["frontier_behavior_count"]),
        "after_frontier_behavior_count": int(after["frontier_behavior_count"]),
        "frontier_cardinality_increased": (
            int(after["frontier_behavior_count"]) > int(before["frontier_behavior_count"])
        ),
        "before_frontier_registry_digest": str(before["frontier_registry_digest"]),
        "after_frontier_registry_digest": str(after["frontier_registry_digest"]),
        "new_behavior_fingerprints": sorted(added),
        "new_behavior_count": len(added),
        "counterexample_schedule_precommitted_before_support_checks": bool(
            acquisition["target_schedule_precommitted_before_support_checks"]
        ),
        "acquisition_target_failed_closed_before_learning": bool(
            acquisition["acquisition_target_failed_closed_before_learning"]
        ),
        "new_candidate_id": candidate_id,
        "new_candidate_negative_evidence_retained": bool(
            acquisition["new_candidate_negative_evidence_retained"]
        ),
        "untouched_generated_control_gap_failed_closed": bool(
            acquisition["control_gap_remained_failed_closed"]
        ),
        "prior_candidate_state_unchanged": True,
        "prior_trial_state_unchanged": True,
        "prior_selected_frontier_behaviors_retained": not missing_retained,
        "selected_new_target": target,
        "new_behavior_transfer": new_transfer,
        "new_candidate_rediscovered_by_behavior_only_solver": candidate_id
        in {str(value) for value in new_transfer["solver_selected_candidate_ids"]},
        "retained_target_replays": retained_replays,
        "all_retained_targets_replayed": len(retained_replays) == len(prior_targets),
        "post_growth_counterfactual_control": {
            "failed_closed": True,
            "failure": counterfactual_failure,
        },
        "runtime_budget_unchanged": bool(
            before["runtime_budget_unchanged"] and after["runtime_budget_unchanged"]
        ),
        "heldback_challenge_shape_unchanged": bool(
            before["heldback_challenge_shape_unchanged"]
            and after["heldback_challenge_shape_unchanged"]
        ),
        "source_candidate_state_unchanged": True,
        "source_trial_state_unchanged": True,
    }
    if not all(
        (
            round_report["frontier_cardinality_increased"],
            round_report["new_behavior_count"] >= 1,
            round_report["counterexample_schedule_precommitted_before_support_checks"],
            round_report["acquisition_target_failed_closed_before_learning"],
            round_report["new_candidate_negative_evidence_retained"],
            round_report["untouched_generated_control_gap_failed_closed"],
            round_report["prior_candidate_state_unchanged"],
            round_report["prior_trial_state_unchanged"],
            round_report["prior_selected_frontier_behaviors_retained"],
            round_report["new_candidate_rediscovered_by_behavior_only_solver"],
            round_report["all_retained_targets_replayed"],
            round_report["post_growth_counterfactual_control"]["failed_closed"],
            round_report["runtime_budget_unchanged"],
            round_report["heldback_challenge_shape_unchanged"],
            round_report["source_candidate_state_unchanged"],
            round_report["source_trial_state_unchanged"],
        )
    ):
        raise CumulativeCounterexampleFrontierGrowthError(
            "counterexample frontier-growth round aggregate invariant failed"
        )
    return round_report, target


def run_cumulative_counterexample_frontier_growth(
    root: Path,
    seed: str,
    *,
    rounds: int = 2,
) -> dict[str, Any]:
    """Demonstrate repeated counterexample-driven frontier growth with functional no-forgetting."""

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    if rounds != 2:
        raise ValueError("this bounded milestone requires exactly two rounds")
    root = root.resolve()

    bootstrap = run_dynamic_skillgraph_growth_campaign(root, f"{seed}:bootstrap")
    if not bootstrap.get("passed"):
        raise CumulativeCounterexampleFrontierGrowthError(
            "dynamic skill-graph bootstrap did not pass"
        )

    frontier_seed = f"{seed}:shared-frontier"
    initial = _runtime_admissible_behavior_frontier(root, frontier_seed)
    selected_targets: list[dict[str, Any]] = []
    round_reports: list[dict[str, Any]] = []
    for index in range(rounds):
        round_report, target = _run_growth_round(
            root,
            seed=f"{seed}:round:{index}",
            frontier_seed=frontier_seed,
            prior_targets=tuple(selected_targets),
        )
        round_reports.append(round_report)
        selected_targets.append(target)

    final = _runtime_admissible_behavior_frontier(root, frontier_seed)
    counts = [
        int(initial["frontier_behavior_count"]),
        *[int(item["after_frontier_behavior_count"]) for item in round_reports],
    ]
    if not all(
        later > earlier
        for earlier, later in zip(counts[:-1], counts[1:], strict=True)
    ):
        raise CumulativeCounterexampleFrontierGrowthError(
            "frontier cardinality did not strictly increase in every round"
        )
    learned_ids = [str(item["new_candidate_id"]) for item in round_reports]
    if len(set(learned_ids)) != rounds:
        raise CumulativeCounterexampleFrontierGrowthError(
            "cumulative growth reused a learned Candidate identity"
        )
    final_fingerprints = _fingerprints(final)
    selected_fingerprints = {
        str(target["behavior_fingerprint"]) for target in selected_targets
    }
    if not selected_fingerprints.issubset(final_fingerprints):
        raise CumulativeCounterexampleFrontierGrowthError(
            "final frontier lost a selected learned behavior"
        )

    final_ids = _all_candidate_ids(root)
    final_tree = _tree_snapshot(root, final_ids)
    final_trials = _trial_snapshot(root, final_ids)
    final_replays = _replay_retained_targets(
        root,
        tuple(selected_targets),
        source_ids=final_ids,
        source_tree=final_tree,
        source_trials=final_trials,
        seed=f"{seed}:final-retention",
    )
    if _tree_snapshot(root, final_ids) != final_tree:
        raise CumulativeCounterexampleFrontierGrowthError(
            "final retention replay changed Candidate bytes"
        )
    if _trial_snapshot(root, final_ids) != final_trials:
        raise CumulativeCounterexampleFrontierGrowthError(
            "final retention replay changed Candidate trials"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "cumulative-counterexample-frontier-growth-v1",
        "seed": seed,
        "round_count": rounds,
        "frontier_seed_reused_across_all_rounds": True,
        "frontier_behavior_counts": counts,
        "frontier_strictly_grew_each_round": True,
        "learned_candidate_ids": learned_ids,
        "learned_candidate_ids_unique": len(set(learned_ids)) == rounds,
        "round_reports": round_reports,
        "selected_behavior_fingerprints": sorted(selected_fingerprints),
        "all_selected_behaviors_retained_in_final_frontier": True,
        "final_retained_target_replays": final_replays,
        "all_final_retained_targets_replayed": len(final_replays) == rounds,
        "all_round_counterexamples_retained": all(
            bool(item["new_candidate_negative_evidence_retained"])
            for item in round_reports
        ),
        "all_round_control_gaps_failed_closed": all(
            bool(item["untouched_generated_control_gap_failed_closed"])
            for item in round_reports
        ),
        "all_round_counterfactual_controls_failed_closed": all(
            bool(item["post_growth_counterfactual_control"]["failed_closed"])
            for item in round_reports
        ),
        "all_prior_candidate_state_unchanged": all(
            bool(item["prior_candidate_state_unchanged"])
            for item in round_reports
        ),
        "all_prior_trial_state_unchanged": all(
            bool(item["prior_trial_state_unchanged"])
            for item in round_reports
        ),
        "runtime_budget_unchanged": all(
            bool(item["runtime_budget_unchanged"]) for item in round_reports
        ),
        "heldback_challenge_shape_unchanged": all(
            bool(item["heldback_challenge_shape_unchanged"])
            for item in round_reports
        ),
        "source_candidate_state_unchanged": True,
        "source_trial_state_unchanged": True,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded repeated self-improvement evidence only. Two independent seed-committed "
            "generic grammar gaps must each fail closed before learning, retain their negative evidence, "
            "and produce distinct exact-scope regression-gated Candidates. The same runtime-admissible "
            "behavior frontier must strictly grow after each round, while every behavior selected in an "
            "earlier round remains present and replays through fresh Engines after later learning without "
            "caller Candidate IDs. Runtime budgets, held-back challenge shapes, Candidate bytes/trials, "
            "per-round untouched gaps, and contradictory controls remain protected. The generator, grammar, "
            "learning, search, runtime, evaluator, and scoring are repository-authored; this does not "
            "establish open-domain self-improvement, independent production evaluation, or AGI."
        ),
    }
    if not all(
        (
            report["frontier_seed_reused_across_all_rounds"],
            report["frontier_strictly_grew_each_round"],
            report["learned_candidate_ids_unique"],
            report["all_selected_behaviors_retained_in_final_frontier"],
            report["all_final_retained_targets_replayed"],
            report["all_round_counterexamples_retained"],
            report["all_round_control_gaps_failed_closed"],
            report["all_round_counterfactual_controls_failed_closed"],
            report["all_prior_candidate_state_unchanged"],
            report["all_prior_trial_state_unchanged"],
            report["runtime_budget_unchanged"],
            report["heldback_challenge_shape_unchanged"],
            report["source_candidate_state_unchanged"],
            report["source_trial_state_unchanged"],
        )
    ):
        raise CumulativeCounterexampleFrontierGrowthError(
            "cumulative counterexample frontier-growth aggregate invariant failed"
        )

    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "cumulative-counterexample-frontier-growth"
        / f"cumulative-{_digest(seed)[:16]}.json",
        report,
    )
    return report
