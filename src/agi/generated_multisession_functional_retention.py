from __future__ import annotations

import json
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import execute_program
from agi.autonomous_heterogeneous_goal_acquisition import _classify_goals
from agi.autonomous_heterogeneous_goal_exhaustion import (
    run_autonomous_heterogeneous_goal_exhaustion,
)
from agi.generated_cross_round_functional_retention import (
    _assert_snapshot,
    _run_recovered_child,
    _snapshot,
)
from agi.heterogeneous_retention_campaign import _digest
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.resumable_seeded_generated_goal_round import _spec_by_name
from agi.seeded_generated_goal_acquisition import _generated_goal_specs
from agi.verified_macro_library_discovery import synthesize_from_verified_macro_library


class GeneratedMultiSessionRetentionError(RuntimeError):
    pass


def _safe_id(value: str) -> bool:
    return bool(value) and all(ch in "abcdefghijklmnopqrstuvwxyz0123456789-_." for ch in value)


def _evidence_path(root: Path, campaign_id: str) -> Path:
    return (
        root
        / ".continual"
        / "evidence"
        / "generated-multisession-functional-retention"
        / f"{campaign_id}.json"
    )


def _replay_without_candidate_ids(
    root: Path,
    *,
    seed: str,
    selected_goal: str,
    candidate_id: str,
) -> dict[str, Any]:
    spec = _spec_by_name(seed, selected_goal)
    result = synthesize_from_verified_macro_library(
        root,
        input_domain=str(spec["input_domain"]),
        output_domain=str(spec["output_domain"]),
        examples=spec["examples"],
        max_depth=3,
        max_candidates=64,
    )
    if result["candidate_ids_supplied_by_caller"]:
        raise GeneratedMultiSessionRetentionError(
            "fresh session replay relied on caller Candidate IDs"
        )
    selected_ids = [str(value) for value in result["selected_candidate_ids"]]
    if candidate_id not in selected_ids:
        raise GeneratedMultiSessionRetentionError(
            "fresh session replay did not rediscover the required learned Candidate"
        )
    actual = execute_program(result["compiled_program"], spec["challenge"])
    if actual != spec["challenge_expected"]:
        raise GeneratedMultiSessionRetentionError(
            "fresh session replay failed a held-back generated challenge"
        )
    return {
        "seed_commitment": _digest(seed),
        "selected_goal": selected_goal,
        "candidate_id": candidate_id,
        "selected_candidate_ids": selected_ids,
        "challenge": spec["challenge"],
        "expected": spec["challenge_expected"],
        "actual": actual,
        "caller_supplied_candidate_ids": False,
    }


def run_generated_multisession_functional_retention(
    root: Path,
    campaign_id: str,
    *,
    seeds: Sequence[str],
) -> dict[str, Any]:
    """Accumulate generated skills across repeated state-only learning-session boundaries.

    The source root first materializes only the fixed heterogeneous prerequisite. Each supplied seed is
    then learned in its own fresh Candidate/system-only reconstruction of the immediately preceding
    session. After every learning session, one additional fresh state-only session must rediscover every
    learned Candidate without caller Candidate IDs, solve all held-back challenges, retain Candidate/trial
    bytes exactly, and leave at least one observed generated gap fail-closed.

    This is repository-authored bounded continual-learning evidence. It does not establish independent
    production generality or AGI.
    """
    if not _safe_id(campaign_id):
        raise ValueError("campaign_id must be a safe lowercase identifier")
    if not isinstance(seeds, Sequence) or isinstance(seeds, (str, bytes)):
        raise ValueError("seeds must be a sequence of strings")
    seed_values = [str(seed) for seed in seeds]
    if len(seed_values) < 2 or any(not seed.strip() for seed in seed_values):
        raise ValueError("at least two non-empty seeds are required")
    if len(set(seed_values)) != len(seed_values):
        raise ValueError("seeds must be distinct")

    root = root.resolve()
    prerequisite = run_autonomous_heterogeneous_goal_exhaustion(
        root,
        f"{campaign_id}:fixed-prerequisite",
    )
    if prerequisite.get("passed") is not True or prerequisite.get("bounded_goal_set_exhausted") is not True:
        raise GeneratedMultiSessionRetentionError("fixed heterogeneous prerequisite did not pass")

    source_ids = _all_candidate_ids(root)
    source_tree, source_trials = _snapshot(root, source_ids)
    rounds: list[dict[str, Any]] = []
    session_transfer_checks: list[dict[str, Any]] = []
    replays: list[dict[str, Any]] = []
    remaining_fail_closed: list[dict[str, str]] = []

    with ExitStack() as stack:
        previous_root = root
        previous_ids = source_ids
        previous_tree = source_tree
        previous_trials = source_trials

        for index, seed in enumerate(seed_values, start=1):
            temporary = stack.enter_context(
                tempfile.TemporaryDirectory(prefix=f"agi-generated-multisession-{index}-")
            )
            session = Path(temporary).resolve()
            _copy_persistent_state(previous_root, session)
            transferred_tree, transferred_trials = _snapshot(session, previous_ids)

            if index == 1:
                if transferred_tree != source_tree or transferred_trials != source_trials:
                    raise GeneratedMultiSessionRetentionError(
                        "session 1 state-only reconstruction changed durable capability state"
                    )
            else:
                transfer = {
                    "from_session": index - 1,
                    "to_session": index,
                    "candidate_bytes_identical": transferred_tree == previous_tree,
                    "trial_ledgers_identical": transferred_trials == previous_trials,
                    "runs_copied": False,
                    "episodes_copied": False,
                    "evidence_copied": False,
                }
                session_transfer_checks.append(transfer)
                if not transfer["candidate_bytes_identical"] or not transfer["trial_ledgers_identical"]:
                    raise GeneratedMultiSessionRetentionError(
                        f"session {index} state-only reconstruction changed durable capability state"
                    )

            child = _run_recovered_child(
                session,
                campaign_id=f"{campaign_id}-r{index:02d}",
                seed=seed,
            )
            candidate_id = str(child["candidate_id"])
            current_ids = _all_candidate_ids(session)
            if sorted(set(current_ids) - set(previous_ids)) != [candidate_id]:
                raise GeneratedMultiSessionRetentionError(
                    f"session {index} did not add exactly its generated Candidate"
                )
            if candidate_id in {str(item["candidate_id"]) for item in rounds}:
                raise GeneratedMultiSessionRetentionError(
                    "successive sessions reused a Candidate identity"
                )
            _assert_snapshot(
                session,
                previous_ids,
                previous_tree,
                previous_trials,
                label=f"generated multisession session {index} learning",
            )
            current_tree, current_trials = _snapshot(session, current_ids)
            child["pre_candidate_count"] = len(previous_ids)
            child["post_candidate_count"] = len(current_ids)
            rounds.append(child)

            previous_root = session
            previous_ids = current_ids
            previous_tree = current_tree
            previous_trials = current_trials

        replay_index = len(seed_values) + 1
        replay_tmp = stack.enter_context(
            tempfile.TemporaryDirectory(prefix=f"agi-generated-multisession-{replay_index}-")
        )
        replay_root = Path(replay_tmp).resolve()
        _copy_persistent_state(previous_root, replay_root)
        replay_tree, replay_trials = _snapshot(replay_root, previous_ids)
        final_transfer = {
            "from_session": len(seed_values),
            "to_session": replay_index,
            "candidate_bytes_identical": replay_tree == previous_tree,
            "trial_ledgers_identical": replay_trials == previous_trials,
            "runs_copied": False,
            "episodes_copied": False,
            "evidence_copied": False,
        }
        session_transfer_checks.append(final_transfer)
        if not final_transfer["candidate_bytes_identical"] or not final_transfer["trial_ledgers_identical"]:
            raise GeneratedMultiSessionRetentionError(
                "final replay reconstruction changed durable capability state"
            )

        replays = [
            _replay_without_candidate_ids(
                replay_root,
                seed=seed,
                selected_goal=str(item["selected_goal"]),
                candidate_id=str(item["candidate_id"]),
            )
            for seed, item in zip(seed_values, rounds, strict=True)
        ]

        for seed, item in zip(seed_values, rounds, strict=True):
            classification = _classify_goals(replay_root, _generated_goal_specs(seed))
            if any(
                entry.get("candidate_ids_supplied_by_caller") is True
                for entry in classification
            ):
                raise GeneratedMultiSessionRetentionError(
                    "fresh session classification relied on caller Candidate IDs"
                )
            by_goal = {str(entry["goal"]): entry for entry in classification}
            selected_goal = str(item["selected_goal"])
            if selected_goal not in by_goal or not by_goal[selected_goal]["supported"]:
                raise GeneratedMultiSessionRetentionError(
                    "fresh session classification lost a learned generated goal"
                )
            for goal in item["initial_unsupported_goals"]:
                name = str(goal)
                if name != selected_goal and not by_goal[name]["supported"]:
                    remaining_fail_closed.append(
                        {"seed_commitment": _digest(seed), "goal": name}
                    )
        if not remaining_fail_closed:
            raise GeneratedMultiSessionRetentionError(
                "multi-session learning unexpectedly eliminated every observed gap"
            )
        _assert_snapshot(
            replay_root,
            previous_ids,
            previous_tree,
            previous_trials,
            label="generated multisession fresh replay",
        )

    _assert_snapshot(
        root,
        source_ids,
        source_tree,
        source_trials,
        label="generated multisession source root",
    )

    report: dict[str, Any] = {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "campaign_kind": "generated-multisession-functional-retention-v1",
        "passed": True,
        "seed_commitments": [_digest(seed) for seed in seed_values],
        "state_only_restart_count": len(seed_values) + 1,
        "learning_session_count": len(seed_values),
        "rounds": rounds,
        "learned_candidate_ids": [str(item["candidate_id"]) for item in rounds],
        "session_transfer_checks": session_transfer_checks,
        "all_transfers_byte_and_trial_identical": all(
            item["candidate_bytes_identical"] and item["trial_ledgers_identical"]
            for item in session_transfer_checks
        ),
        "all_observed_crashes_reconciled_without_relearning": all(
            item["interruption_observed"] and item["reconciliation_observed"]
            for item in rounds
        ),
        "fresh_session_replays": replays,
        "all_learned_skills_functionally_replayed": len(replays) == len(rounds),
        "all_replays_avoided_caller_candidate_ids": all(
            item["caller_supplied_candidate_ids"] is False for item in replays
        ),
        "remaining_fail_closed_goals": remaining_fail_closed,
        "remaining_fail_closed_count": len(remaining_fail_closed),
        "source_candidate_state_unchanged": _snapshot(root, source_ids)[0] == source_tree,
        "source_trial_ledgers_unchanged": _snapshot(root, source_ids)[1] == source_trials,
        "prior_runs_copied_between_sessions": False,
        "prior_episodes_copied_between_sessions": False,
        "prior_evidence_copied_between_sessions": False,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded multi-session continual-learning evidence only. Generated skills are "
            "learned in separate Candidate/system-only reconstructed sessions with crash reconciliation, "
            "then all are rediscovered in one additional fresh session without caller Candidate IDs. "
            "Task families, generator, synthesis, regression, scoring, crash injection, and evaluator "
            "remain repository-authored. This is not independent production evidence and does not "
            "establish AGI."
        ),
    }
    required = (
        report["all_transfers_byte_and_trial_identical"],
        report["all_observed_crashes_reconciled_without_relearning"],
        report["all_learned_skills_functionally_replayed"],
        report["all_replays_avoided_caller_candidate_ids"],
        report["remaining_fail_closed_count"] >= 1,
        report["source_candidate_state_unchanged"],
        report["source_trial_ledgers_unchanged"],
    )
    if not all(required):
        raise GeneratedMultiSessionRetentionError(
            "generated multisession aggregate invariant failed"
        )
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(_evidence_path(root, campaign_id), report)
    return report
