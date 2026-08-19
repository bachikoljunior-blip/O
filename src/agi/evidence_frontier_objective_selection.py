from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import execute_program
from agi.crossdomain_failure_derived_target import (
    _attempt,
    _examples,
    _solve_and_replay,
)
from agi.cumulative_crossdomain_failure_retention import (
    _reconstruct_targets,
    run_cumulative_crossdomain_failure_retention,
)
from agi.durable_state_rehydration import _tree_snapshot
from agi.generated_crossdomain_cegis import generated_crossdomain_target
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.transactional_multisession_commit import _commit_verified_candidate


class EvidenceFrontierObjectiveSelectionError(RuntimeError):
    pass


def _load_round_report(root: Path, round_seed: str) -> dict[str, Any]:
    path = (
        root
        / ".continual"
        / "evidence"
        / "crossdomain-failure-derived-target"
        / f"crossdomain-derived-{_digest(round_seed)[:16]}.json"
    )
    if not path.is_file():
        raise EvidenceFrontierObjectiveSelectionError(
            f"persisted heterogeneous round evidence is missing: {path}"
        )
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not value.get("passed"):
        raise EvidenceFrontierObjectiveSelectionError(
            "persisted heterogeneous round evidence is not a passing report"
        )
    if str(value.get("seed")) != round_seed:
        raise EvidenceFrontierObjectiveSelectionError(
            "persisted heterogeneous round evidence seed changed"
        )
    return value


def _control_frontier(
    root: Path,
    base: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    replays = base.get("fresh_state_only_replays")
    if not isinstance(replays, list) or len(replays) < 2:
        raise EvidenceFrontierObjectiveSelectionError(
            "cumulative prerequisite did not persist multiple heterogeneous rounds"
        )
    entries: list[dict[str, Any]] = []
    reports: dict[str, dict[str, Any]] = {}
    seen_signatures: set[str] = set()
    for replay in replays:
        if not isinstance(replay, Mapping):
            raise EvidenceFrontierObjectiveSelectionError("malformed cumulative replay record")
        round_seed = str(replay["round_seed"])
        report = _load_round_report(root, round_seed)
        reconstructed = _reconstruct_targets(root, report)
        control = reconstructed["control"]
        signature = str(control["semantic_signature"])
        if signature in seen_signatures:
            raise EvidenceFrontierObjectiveSelectionError(
                "heterogeneous failure histories reconstructed the same control semantics"
            )
        seen_signatures.add(signature)
        rank = _digest(
            {
                "semantic_signature": signature,
                "source_input_domain": str(report["source_input_domain"]),
                "input_domain": str(control["input_domain"]),
                "output_domain": str(control["output_domain"]),
                "program_nodes": int(control["program_nodes"]),
                "phase": "evidence-frontier-objective-rank-v1",
            }
        )
        entries.append(
            {
                "rank": rank,
                "round_seed": round_seed,
                "source_input_domain": str(report["source_input_domain"]),
                "semantic_signature": signature,
                "input_domain": str(control["input_domain"]),
                "output_domain": str(control["output_domain"]),
                "program_nodes": int(control["program_nodes"]),
                "reconstructed_schedule_commitment": str(
                    reconstructed["schedule_commitment"]
                ),
            }
        )
        reports[round_seed] = report
    entries.sort(key=lambda item: (str(item["rank"]), str(item["semantic_signature"])))
    return entries, reports


def _assert_prior_unchanged(
    root: Path,
    candidate_ids: tuple[str, ...],
    tree: Mapping[str, str],
    trials: Mapping[str, Mapping[str, str]],
    *,
    label: str,
) -> None:
    if _tree_snapshot(root, candidate_ids) != dict(tree):
        raise EvidenceFrontierObjectiveSelectionError(
            f"{label} changed prior Candidate bytes"
        )
    if _trial_snapshot(root, candidate_ids) != dict(trials):
        raise EvidenceFrontierObjectiveSelectionError(
            f"{label} changed prior Candidate trials"
        )


def run_evidence_frontier_objective_selection(root: Path, seed: str) -> dict[str, Any]:
    """Select and learn the next objective from persisted heterogeneous failure controls.

    The prerequisite accumulates two independently generated cross-domain failure histories. This
    milestone removes caller-selected source domains from the *next* learning decision: it reconstructs
    both still-unresolved controls from durable evidence, commits a deterministic evidence-only ordering
    before support checks, learns the first fail-closed control in that ordering, and then requires all
    earlier source/derived behaviors to remain behaviorally replayable from a fresh state-only resolver.
    The unselected control must remain fail closed and all prior Candidate bytes/trials remain unchanged.

    This remains bounded repository-authored development evidence, not independent production evidence.
    """
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    base = run_cumulative_crossdomain_failure_retention(root, f"{seed}:base")
    if not base.get("passed"):
        raise EvidenceFrontierObjectiveSelectionError(
            "cumulative heterogeneous failure-retention prerequisite did not pass"
        )
    if not base.get("all_controls_failed_closed_after_later_learning"):
        raise EvidenceFrontierObjectiveSelectionError(
            "cumulative prerequisite lost an unresolved control before objective selection"
        )

    frontier, reports = _control_frontier(root, base)
    frontier_commitment = _digest(
        {
            "base_round_plan_commitment": str(base["round_plan_commitment"]),
            "controls": [
                {
                    "rank": str(item["rank"]),
                    "source_input_domain": str(item["source_input_domain"]),
                    "semantic_signature": str(item["semantic_signature"]),
                    "input_domain": str(item["input_domain"]),
                    "output_domain": str(item["output_domain"]),
                    "program_nodes": int(item["program_nodes"]),
                }
                for item in frontier
            ],
            "phase": "evidence-frontier-objective-precommit-v1",
        }
    )

    support_attempts: list[dict[str, Any]] = []
    selected_entry: dict[str, Any] | None = None
    selected_control: dict[str, Any] | None = None
    reconstructed_by_seed: dict[str, dict[str, Any]] = {}
    for entry in frontier:
        round_seed = str(entry["round_seed"])
        reconstructed = _reconstruct_targets(root, reports[round_seed])
        reconstructed_by_seed[round_seed] = reconstructed
        control = reconstructed["control"]
        attempt = _attempt(root, control)
        support_attempts.append(
            {
                "rank": str(entry["rank"]),
                "round_seed": round_seed,
                "source_input_domain": str(entry["source_input_domain"]),
                "semantic_signature": str(entry["semantic_signature"]),
                "supported_before_learning": bool(attempt["supported"]),
                "candidate_ids_supplied_by_caller": bool(
                    attempt["candidate_ids_supplied_by_caller"]
                ),
            }
        )
        if selected_entry is None and not attempt["supported"]:
            selected_entry = dict(entry)
            selected_control = dict(control)
    if selected_entry is None or selected_control is None:
        raise EvidenceFrontierObjectiveSelectionError(
            "persisted heterogeneous evidence frontier exposed no fail-closed objective"
        )
    if len(frontier) < 2:
        raise EvidenceFrontierObjectiveSelectionError(
            "evidence frontier must contain at least two heterogeneous unresolved controls"
        )

    selected_seed = str(selected_entry["round_seed"])
    selected_report = reports[selected_seed]
    source_seed = f"{selected_seed}:source-cegis"
    _source_target, _meta, _initial, _diagnostic, final_inputs = generated_crossdomain_target(
        source_seed
    )
    challenge_inputs = tuple(copy.deepcopy(value) for value in final_inputs)
    expected = tuple(
        execute_program(selected_control["program"], value) for value in challenge_inputs
    )
    if len(challenge_inputs) < 2:
        raise EvidenceFrontierObjectiveSelectionError(
            "selected persisted failure lacks a multi-instance held-back challenge"
        )

    prior_ids = _all_candidate_ids(root)
    if not prior_ids:
        raise EvidenceFrontierObjectiveSelectionError(
            "evidence-frontier objective selection requires a retained capability graph"
        )
    prior_tree = _tree_snapshot(root, prior_ids)
    prior_trials = _trial_snapshot(root, prior_ids)
    if not all(prior_trials.get(candidate_id) for candidate_id in prior_ids):
        raise EvidenceFrontierObjectiveSelectionError(
            "prior capability graph contains a Candidate without durable trial evidence"
        )

    spec = {
        "name": f"evidence-frontier-{str(selected_entry['semantic_signature'])[:12]}",
        "input_domain": str(selected_control["input_domain"]),
        "output_domain": str(selected_control["output_domain"]),
        "examples": _examples(selected_control),
        "probe": challenge_inputs[0],
        "expected": expected[0],
        "challenge": challenge_inputs[1],
        "challenge_expected": expected[1],
        "max_nodes": int(selected_control["program_nodes"]),
    }

    with tempfile.TemporaryDirectory(prefix="agi-evidence-frontier-learn-") as temporary:
        learner = Path(temporary).resolve()
        _copy_persistent_state(root, learner)
        _assert_prior_unchanged(
            learner,
            prior_ids,
            prior_tree,
            prior_trials,
            label="fresh learner reconstruction",
        )
        if _attempt(learner, selected_control)["supported"]:
            raise EvidenceFrontierObjectiveSelectionError(
                "precommitted evidence-selected objective became supported before learning"
            )
        learned = _promote_task(
            learner,
            seed=f"{seed}:evidence-frontier-learn",
            spec=spec,
        )
        candidate_id = str(learned["candidate_id"])
        if candidate_id in prior_ids:
            raise EvidenceFrontierObjectiveSelectionError(
                "evidence-frontier learning reused a prior Candidate identity"
            )
        if not learned.get("negative_evidence_retained"):
            raise EvidenceFrontierObjectiveSelectionError(
                "evidence-frontier learning discarded its pre-learning failure"
            )
        for value, expected_value in zip(challenge_inputs, expected, strict=True):
            if execute_program(learned["program"], value) != expected_value:
                raise EvidenceFrontierObjectiveSelectionError(
                    "evidence-frontier Candidate failed the sealed challenge"
                )
        _assert_prior_unchanged(
            learner,
            prior_ids,
            prior_tree,
            prior_trials,
            label="evidence-frontier learning",
        )
        commit = _commit_verified_candidate(
            learner,
            root,
            candidate_id=candidate_id,
            scope=str(learned["scope"]),
        )

    _assert_prior_unchanged(
        root,
        prior_ids,
        prior_tree,
        prior_trials,
        label="transactional evidence-frontier commit",
    )
    final_ids = _all_candidate_ids(root)
    final_tree = _tree_snapshot(root, final_ids)
    final_trials = _trial_snapshot(root, final_ids)
    if candidate_id not in final_ids or not final_trials.get(candidate_id):
        raise EvidenceFrontierObjectiveSelectionError(
            "new evidence-frontier Candidate lacks durable trial evidence"
        )

    prior_replays: list[dict[str, Any]] = []
    untouched_controls: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="agi-evidence-frontier-resolver-") as temporary:
        resolver = Path(temporary).resolve()
        _copy_persistent_state(root, resolver)
        if _tree_snapshot(resolver, final_ids) != final_tree:
            raise EvidenceFrontierObjectiveSelectionError(
                "fresh resolver changed durable Candidate bytes"
            )
        if _trial_snapshot(resolver, final_ids) != final_trials:
            raise EvidenceFrontierObjectiveSelectionError(
                "fresh resolver changed durable Candidate trials"
            )

        selected_transfer = _solve_and_replay(
            resolver,
            selected_control,
            required_candidate_id=candidate_id,
            challenge_inputs=challenge_inputs,
        )
        if candidate_id not in {
            str(value) for value in selected_transfer["solver_selected_candidate_ids"]
        }:
            raise EvidenceFrontierObjectiveSelectionError(
                "fresh behavior-only resolver did not rediscover the new objective Candidate"
            )

        for entry in frontier:
            round_seed = str(entry["round_seed"])
            report = reports[round_seed]
            reconstructed = reconstructed_by_seed.get(round_seed)
            if reconstructed is None:
                reconstructed = _reconstruct_targets(resolver, report)
            _target, _meta, _initial, _diagnostic, round_final_inputs = generated_crossdomain_target(
                f"{round_seed}:source-cegis"
            )
            round_challenges = tuple(copy.deepcopy(value) for value in round_final_inputs)
            derived_replay = _solve_and_replay(
                resolver,
                reconstructed["target"],
                required_candidate_id=str(report["new_candidate_id"]),
                challenge_inputs=round_challenges,
            )
            source_replay = _solve_and_replay(
                resolver,
                reconstructed["base_target"],
                required_candidate_id=str(report["source_candidate_id"]),
                challenge_inputs=round_challenges,
            )
            prior_replays.append(
                {
                    "round_seed": round_seed,
                    "source_input_domain": str(report["source_input_domain"]),
                    "source_candidate_id": str(report["source_candidate_id"]),
                    "derived_candidate_id": str(report["new_candidate_id"]),
                    "source_replay": source_replay,
                    "derived_replay": derived_replay,
                }
            )
            if str(entry["semantic_signature"]) != str(
                selected_entry["semantic_signature"]
            ):
                attempt = _attempt(resolver, reconstructed["control"])
                if attempt["supported"]:
                    raise EvidenceFrontierObjectiveSelectionError(
                        "learning the evidence-selected objective overgeneralized an untouched control"
                    )
                untouched_controls.append(
                    {
                        "round_seed": round_seed,
                        "semantic_signature": str(entry["semantic_signature"]),
                        "failed_closed_after_learning": True,
                    }
                )

        if _tree_snapshot(resolver, final_ids) != final_tree:
            raise EvidenceFrontierObjectiveSelectionError(
                "fresh replay changed Candidate bytes"
            )
        if _trial_snapshot(resolver, final_ids) != final_trials:
            raise EvidenceFrontierObjectiveSelectionError(
                "fresh replay changed Candidate trials"
            )

    _assert_prior_unchanged(
        root,
        prior_ids,
        prior_tree,
        prior_trials,
        label="post-replay durable source",
    )
    if _tree_snapshot(root, final_ids) != final_tree or _trial_snapshot(root, final_ids) != final_trials:
        raise EvidenceFrontierObjectiveSelectionError(
            "post-replay final durable state changed"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "evidence-frontier-objective-selection-v1",
        "seed": seed,
        "base_campaign_digest": str(base["digest"]),
        "objective_selection_source": "persisted_heterogeneous_fail_closed_controls",
        "caller_selected_source_domain": False,
        "fresh_target_generator_used_for_next_objective": False,
        "frontier_precommitted_before_support_checks": True,
        "frontier_commitment": frontier_commitment,
        "frontier_size": len(frontier),
        "frontier": frontier,
        "support_attempts": support_attempts,
        "selected_round_seed": selected_seed,
        "selected_source_input_domain": str(selected_report["source_input_domain"]),
        "selected_semantic_signature": str(selected_entry["semantic_signature"]),
        "selected_objective_failed_closed_before_learning": True,
        "new_candidate_id": candidate_id,
        "new_candidate_negative_evidence_retained": True,
        "transactional_commit": commit,
        "prior_candidate_state_unchanged": True,
        "prior_trial_state_unchanged": True,
        "fresh_behavior_only_transfer": selected_transfer,
        "new_candidate_rediscovered_without_caller_ids": candidate_id
        in {str(value) for value in selected_transfer["solver_selected_candidate_ids"]},
        "prior_behavior_replays": prior_replays,
        "all_prior_source_behaviors_retained": all(
            str(item["source_candidate_id"])
            in {str(value) for value in item["source_replay"]["solver_selected_candidate_ids"]}
            for item in prior_replays
        ),
        "all_prior_derived_behaviors_retained": all(
            str(item["derived_candidate_id"])
            in {str(value) for value in item["derived_replay"]["solver_selected_candidate_ids"]}
            for item in prior_replays
        ),
        "untouched_controls": untouched_controls,
        "all_unselected_controls_failed_closed_after_learning": bool(untouched_controls)
        and all(bool(item["failed_closed_after_learning"]) for item in untouched_controls),
        "all_replays_used_fresh_unique_engines": all(
            bool(item["source_replay"]["fresh_engine_runs_unique"])
            and bool(item["derived_replay"]["fresh_engine_runs_unique"])
            for item in prior_replays
        )
        and bool(selected_transfer["fresh_engine_runs_unique"]),
        "all_replays_avoided_caller_candidate_ids": all(
            item["source_replay"]["solver_candidate_ids_supplied_by_caller"] is False
            and item["derived_replay"]["solver_candidate_ids_supplied_by_caller"] is False
            for item in prior_replays
        )
        and selected_transfer["solver_candidate_ids_supplied_by_caller"] is False,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded evidence-driven curriculum evidence only. The next objective is selected "
            "from a precommitted ordering of still-unresolved controls reconstructed from multiple "
            "persisted heterogeneous failure histories, rather than from a caller-selected source domain "
            "or a fresh target generator. Learning still uses repository-authored generators, synthesis, "
            "regression, evaluator, probes and scoring. This does not establish open-domain autonomous "
            "objective invention, independent production evidence, or AGI."
        ),
    }
    if not all(
        (
            report["frontier_precommitted_before_support_checks"],
            report["caller_selected_source_domain"] is False,
            report["fresh_target_generator_used_for_next_objective"] is False,
            report["selected_objective_failed_closed_before_learning"],
            report["new_candidate_negative_evidence_retained"],
            report["prior_candidate_state_unchanged"],
            report["prior_trial_state_unchanged"],
            report["new_candidate_rediscovered_without_caller_ids"],
            report["all_prior_source_behaviors_retained"],
            report["all_prior_derived_behaviors_retained"],
            report["all_unselected_controls_failed_closed_after_learning"],
            report["all_replays_used_fresh_unique_engines"],
            report["all_replays_avoided_caller_candidate_ids"],
        )
    ):
        raise EvidenceFrontierObjectiveSelectionError(
            "evidence-frontier objective-selection aggregate invariant failed"
        )
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "evidence-frontier-objective-selection"
        / f"evidence-frontier-{_digest(seed)[:16]}.json",
        report,
    )
    return report
