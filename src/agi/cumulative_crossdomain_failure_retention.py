from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

from agi.acquired_program_runtime import _atomic_json
from agi.crossdomain_failure_derived_target import (
    CrossDomainFailureDerivedTargetError,
    _attempt,
    _expand_support_inputs,
    _precommit_schedule,
    _solve_and_replay,
    _unique_inputs,
    run_crossdomain_failure_derived_target,
)
from agi.durable_state_rehydration import _tree_snapshot
from agi.generated_crossdomain_cegis import generated_crossdomain_target
from agi.heterogeneous_retention_campaign import _digest
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state


class CumulativeCrossDomainFailureRetentionError(RuntimeError):
    pass


def _round_plan(seed: str) -> dict[str, Any]:
    selected: dict[str, dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []
    for index in range(64):
        round_seed = f"{seed}:round-seed:{index}"
        _target, meta, _initial, _diagnostic, _final = generated_crossdomain_target(
            f"{round_seed}:source-cegis"
        )
        record = {
            "round_seed": round_seed,
            "family_index": int(meta["family_index"]),
            "family": str(meta["family"]),
            "input_domain": str(meta["input_domain"]),
            "output_domain": str(meta["output_domain"]),
        }
        candidates.append(record)
        selected.setdefault(record["input_domain"], record)
        if "string" in selected and "sequence" in selected:
            break
    if "string" not in selected or "sequence" not in selected:
        raise CumulativeCrossDomainFailureRetentionError(
            "bounded round planning did not find both string and sequence source domains"
        )
    rounds = [selected["string"], selected["sequence"]]
    commitment = _digest(
        {
            "seed": seed,
            "rounds": rounds,
            "candidate_prefix": candidates,
            "phase": "cumulative-crossdomain-round-plan-v1",
        }
    )
    return {
        "rounds": rounds,
        "round_plan_commitment": commitment,
        "round_plan_precommitted_before_learning": True,
        "bounded_seed_candidates_examined": len(candidates),
    }


def _snapshot_subset(root: Path, candidate_ids: tuple[str, ...]) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    if not candidate_ids:
        return {}, {}
    return _tree_snapshot(root, candidate_ids), _trial_snapshot(root, candidate_ids)


def _reconstruct_targets(root: Path, report: Mapping[str, Any]) -> dict[str, Any]:
    source_candidate_id = str(report["source_candidate_id"])
    candidate_dir = root / ".continual" / "candidates" / source_candidate_id
    candidate = json.loads((candidate_dir / "candidate.json").read_text(encoding="utf-8"))
    raw_program = candidate.get("acquired_program", {}).get("program")
    if not isinstance(raw_program, Mapping):
        raise CumulativeCrossDomainFailureRetentionError(
            "durable source Candidate lost canonical acquired_program.program payload"
        )
    history = json.loads(
        (candidate_dir / "learning-history" / "generated-crossdomain-cegis.json").read_text(
            encoding="utf-8"
        )
    )
    history_digest = _digest(history)
    if history_digest != report["source_history_digest"]:
        raise CumulativeCrossDomainFailureRetentionError(
            "persisted cross-domain source history digest changed"
        )
    persisted_support_inputs = _unique_inputs(
        [*history["initial_demonstrations"], *history["counterexamples"]]
    )
    support_inputs = _expand_support_inputs(
        dict(raw_program),
        persisted_support_inputs,
        history_digest=history_digest,
    )
    schedule = _precommit_schedule(
        dict(raw_program),
        history_digest=history_digest,
        support_inputs=support_inputs,
    )
    target_signature = str(report["derived_target_semantic_signature"])
    failed_signatures = [
        str(item["semantic_signature"])
        for item in report["support_attempts"]
        if not bool(item["supported_before_learning"])
    ]
    if len(failed_signatures) < 2 or failed_signatures[0] != target_signature:
        raise CumulativeCrossDomainFailureRetentionError(
            "persisted support-attempt ordering no longer reconstructs target/control choice"
        )
    control_signature = failed_signatures[1]
    by_signature = {
        str(item["semantic_signature"]): dict(item)
        for item in schedule["target_schedule"]
    }
    target = by_signature.get(target_signature)
    control = by_signature.get(control_signature)
    if target is None or control is None:
        raise CumulativeCrossDomainFailureRetentionError(
            "persisted derived target/control signatures disappeared from reconstructed schedule"
        )
    return {
        "base_target": dict(schedule["base_target"]),
        "target": target,
        "control": control,
        "schedule_commitment": str(schedule["schedule_commitment"]),
    }


def run_cumulative_crossdomain_failure_retention(root: Path, seed: str) -> dict[str, Any]:
    """Learn from two heterogeneous persisted failure histories and retain both after later growth."""
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    plan = _round_plan(seed)

    initial_ids = _all_candidate_ids(root)
    initial_tree, initial_trials = _snapshot_subset(root, initial_ids)
    round_reports: list[dict[str, Any]] = []
    round_after_snapshots: list[dict[str, Any]] = []

    for round_index, round_plan in enumerate(plan["rounds"]):
        prior_ids = _all_candidate_ids(root)
        prior_tree, prior_trials = _snapshot_subset(root, prior_ids)
        report = run_crossdomain_failure_derived_target(root, str(round_plan["round_seed"]))
        if not report.get("passed"):
            raise CumulativeCrossDomainFailureRetentionError(
                f"cross-domain failure-derived round {round_index} did not pass"
            )
        if str(report["source_input_domain"]) != str(round_plan["input_domain"]):
            raise CumulativeCrossDomainFailureRetentionError(
                "source input domain changed after precommitment"
            )
        if not report.get("source_negative_evidence_retained"):
            raise CumulativeCrossDomainFailureRetentionError(
                "round lost persisted CEGIS negative evidence"
            )
        if not report.get("derived_target_failed_closed_before_learning"):
            raise CumulativeCrossDomainFailureRetentionError(
                "round derived target did not fail closed before learning"
            )
        if not report.get("derived_control_gap_remained_failed_closed"):
            raise CumulativeCrossDomainFailureRetentionError(
                "round derived control was overgeneralized"
            )
        current_prior_tree, current_prior_trials = _snapshot_subset(root, prior_ids)
        if current_prior_tree != prior_tree:
            raise CumulativeCrossDomainFailureRetentionError(
                "later cross-domain learning changed prior Candidate bytes"
            )
        if current_prior_trials != prior_trials:
            raise CumulativeCrossDomainFailureRetentionError(
                "later cross-domain learning changed prior Candidate trials"
            )
        after_ids = _all_candidate_ids(root)
        if str(report["source_candidate_id"]) not in after_ids or str(report["new_candidate_id"]) not in after_ids:
            raise CumulativeCrossDomainFailureRetentionError(
                "round source or derived Candidate was not durably retained"
            )
        after_tree, after_trials = _snapshot_subset(root, after_ids)
        if not all(after_trials.get(candidate_id) for candidate_id in after_ids):
            raise CumulativeCrossDomainFailureRetentionError(
                "round durable graph contains a Candidate without trial evidence"
            )
        round_reports.append(dict(report))
        round_after_snapshots.append(
            {
                "candidate_ids": after_ids,
                "tree": after_tree,
                "trials": after_trials,
            }
        )

    if initial_ids:
        if _tree_snapshot(root, initial_ids) != initial_tree:
            raise CumulativeCrossDomainFailureRetentionError(
                "cumulative campaign changed pre-existing Candidate bytes"
            )
        if _trial_snapshot(root, initial_ids) != initial_trials:
            raise CumulativeCrossDomainFailureRetentionError(
                "cumulative campaign changed pre-existing Candidate trials"
            )

    first_after = round_after_snapshots[0]
    first_ids = tuple(str(value) for value in first_after["candidate_ids"])
    if _tree_snapshot(root, first_ids) != first_after["tree"]:
        raise CumulativeCrossDomainFailureRetentionError(
            "second heterogeneous round changed first-round Candidate bytes"
        )
    if _trial_snapshot(root, first_ids) != first_after["trials"]:
        raise CumulativeCrossDomainFailureRetentionError(
            "second heterogeneous round changed first-round Candidate trials"
        )

    source_ids = [str(item["source_candidate_id"]) for item in round_reports]
    learned_ids = [str(item["new_candidate_id"]) for item in round_reports]
    if len(set(source_ids)) != 2 or len(set(learned_ids)) != 2:
        raise CumulativeCrossDomainFailureRetentionError(
            "heterogeneous rounds reused source or derived Candidate identity"
        )
    if {str(item["source_input_domain"]) for item in round_reports} != {"string", "sequence"}:
        raise CumulativeCrossDomainFailureRetentionError(
            "cumulative campaign did not cover both planned source input domains"
        )

    final_ids = _all_candidate_ids(root)
    final_tree, final_trials = _snapshot_subset(root, final_ids)
    replay_records: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="agi-cumulative-crossdomain-retention-") as temporary:
        resolver = Path(temporary).resolve()
        _copy_persistent_state(root, resolver)
        if _tree_snapshot(resolver, final_ids) != final_tree or _trial_snapshot(resolver, final_ids) != final_trials:
            raise CumulativeCrossDomainFailureRetentionError(
                "fresh state-only resolver changed durable Candidate/trial state"
            )
        for report in round_reports:
            reconstructed = _reconstruct_targets(resolver, report)
            source_seed = f"{report['seed']}:source-cegis"
            _source_target, _meta, _initial, _diagnostic, final_inputs = generated_crossdomain_target(
                source_seed
            )
            challenge_inputs = tuple(final_inputs)
            derived_replay = _solve_and_replay(
                resolver,
                reconstructed["target"],
                required_candidate_id=str(report["new_candidate_id"]),
                challenge_inputs=challenge_inputs,
            )
            source_replay = _solve_and_replay(
                resolver,
                reconstructed["base_target"],
                required_candidate_id=str(report["source_candidate_id"]),
                challenge_inputs=challenge_inputs,
            )
            control_attempt = _attempt(resolver, reconstructed["control"])
            if control_attempt["supported"]:
                raise CumulativeCrossDomainFailureRetentionError(
                    "later learning overgeneralized an earlier round control gap"
                )
            replay_records.append(
                {
                    "round_seed": str(report["seed"]),
                    "source_input_domain": str(report["source_input_domain"]),
                    "source_candidate_id": str(report["source_candidate_id"]),
                    "derived_candidate_id": str(report["new_candidate_id"]),
                    "derived_replay": derived_replay,
                    "source_replay": source_replay,
                    "control_gap_failed_closed_after_all_rounds": True,
                    "reconstructed_schedule_commitment": reconstructed["schedule_commitment"],
                }
            )
        if _tree_snapshot(resolver, final_ids) != final_tree:
            raise CumulativeCrossDomainFailureRetentionError(
                "fresh replay changed Candidate bytes"
            )
        if _trial_snapshot(resolver, final_ids) != final_trials:
            raise CumulativeCrossDomainFailureRetentionError(
                "fresh replay changed Candidate trials"
            )

    if _tree_snapshot(root, final_ids) != final_tree or _trial_snapshot(root, final_ids) != final_trials:
        raise CumulativeCrossDomainFailureRetentionError(
            "post-replay durable source state changed"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "cumulative-crossdomain-failure-retention-v1",
        "seed": seed,
        "round_plan_commitment": str(plan["round_plan_commitment"]),
        "round_plan_precommitted_before_learning": True,
        "bounded_seed_candidates_examined": int(plan["bounded_seed_candidates_examined"]),
        "round_count": 2,
        "planned_source_input_domains": [str(item["input_domain"]) for item in plan["rounds"]],
        "source_input_domains_heterogeneous": True,
        "source_candidate_ids": source_ids,
        "source_candidate_ids_unique": True,
        "learned_candidate_ids": learned_ids,
        "learned_candidate_ids_unique": True,
        "all_round_negative_evidence_retained": all(
            bool(item["source_negative_evidence_retained"]) for item in round_reports
        ),
        "all_round_targets_failed_closed_before_learning": all(
            bool(item["derived_target_failed_closed_before_learning"]) for item in round_reports
        ),
        "all_round_controls_failed_closed_during_learning": all(
            bool(item["derived_control_gap_remained_failed_closed"]) for item in round_reports
        ),
        "first_round_candidate_state_unchanged_after_second_round": True,
        "first_round_trial_state_unchanged_after_second_round": True,
        "preexisting_candidate_state_unchanged": True,
        "preexisting_trial_state_unchanged": True,
        "fresh_state_only_replays": replay_records,
        "all_derived_behaviors_replayed_after_later_learning": all(
            str(item["derived_candidate_id"])
            in {str(value) for value in item["derived_replay"]["solver_selected_candidate_ids"]}
            for item in replay_records
        ),
        "all_source_behaviors_replayed_after_later_learning": all(
            str(item["source_candidate_id"])
            in {str(value) for value in item["source_replay"]["solver_selected_candidate_ids"]}
            for item in replay_records
        ),
        "all_controls_failed_closed_after_later_learning": all(
            bool(item["control_gap_failed_closed_after_all_rounds"])
            for item in replay_records
        ),
        "all_replays_used_fresh_unique_engines": all(
            bool(item["derived_replay"]["fresh_engine_runs_unique"])
            and bool(item["source_replay"]["fresh_engine_runs_unique"])
            for item in replay_records
        ),
        "all_replays_avoided_caller_candidate_ids": all(
            item["derived_replay"]["solver_candidate_ids_supplied_by_caller"] is False
            and item["source_replay"]["solver_candidate_ids_supplied_by_caller"] is False
            for item in replay_records
        ),
        "source_candidate_state_unchanged": True,
        "source_trial_state_unchanged": True,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded heterogeneous continual-learning evidence only. Two source domains are "
            "precommitted before learning; each round must retain a real CEGIS failure, derive a new "
            "fail-closed target from persisted failure structure, learn it through unchanged regression "
            "and transactional gates, and preserve all earlier Candidate bytes/trials. After the second "
            "round, a fresh state-only resolver must rediscover and replay both earlier and later derived "
            "behaviors plus their source behaviors without caller Candidate IDs while each untouched "
            "control remains fail-closed. Generators, derivation, learning, evaluator, probes and scoring "
            "remain repository-authored; this is not independent production evidence, open-domain "
            "autonomous objective invention, or AGI."
        ),
    }
    if not all(
        (
            report["round_plan_precommitted_before_learning"],
            report["source_input_domains_heterogeneous"],
            report["source_candidate_ids_unique"],
            report["learned_candidate_ids_unique"],
            report["all_round_negative_evidence_retained"],
            report["all_round_targets_failed_closed_before_learning"],
            report["all_round_controls_failed_closed_during_learning"],
            report["first_round_candidate_state_unchanged_after_second_round"],
            report["first_round_trial_state_unchanged_after_second_round"],
            report["preexisting_candidate_state_unchanged"],
            report["preexisting_trial_state_unchanged"],
            report["all_derived_behaviors_replayed_after_later_learning"],
            report["all_source_behaviors_replayed_after_later_learning"],
            report["all_controls_failed_closed_after_later_learning"],
            report["all_replays_used_fresh_unique_engines"],
            report["all_replays_avoided_caller_candidate_ids"],
            report["source_candidate_state_unchanged"],
            report["source_trial_state_unchanged"],
        )
    ):
        raise CumulativeCrossDomainFailureRetentionError(
            "cumulative cross-domain failure-retention aggregate invariant failed"
        )
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "cumulative-crossdomain-failure-retention"
        / f"cumulative-crossdomain-{_digest(seed)[:16]}.json",
        report,
    )
    return report
