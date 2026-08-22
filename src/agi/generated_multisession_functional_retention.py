from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import execute_program
from agi.autonomous_heterogeneous_goal_acquisition import _classify_goals
from agi.autonomous_heterogeneous_goal_exhaustion import (
    run_autonomous_heterogeneous_goal_exhaustion,
)
from agi.generated_cross_round_functional_retention import (
    GeneratedCrossRoundRetentionError,
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
    """Accumulate generated skills across state-only process boundaries and replay both fresh.

    The source root first materializes only the fixed heterogeneous prerequisite. Session 1 receives
    Candidate/system state only and performs one crash-recovered generated learning round. Session 2 is
    a fresh state-only reconstruction of Session 1, verifies byte/trial identity, and performs a second
    distinct crash-recovered generated learning round. Session 3 is another fresh state-only
    reconstruction and must rediscover both learned Candidates without caller Candidate IDs, solve both
    held-back challenges, retain earlier Candidate/trial bytes exactly, and leave at least one observed
    generated gap fail-closed.

    This is repository-authored bounded continual-learning evidence. It does not establish independent
    production generality or AGI.
    """
    if not _safe_id(campaign_id):
        raise ValueError("campaign_id must be a safe lowercase identifier")
    if not isinstance(seeds, Sequence) or isinstance(seeds, (str, bytes)):
        raise ValueError("seeds must be a sequence of strings")
    seed_values = [str(seed) for seed in seeds]
    if len(seed_values) != 2 or any(not seed.strip() for seed in seed_values):
        raise ValueError("exactly two non-empty seeds are required")
    if len(set(seed_values)) != 2:
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

    with tempfile.TemporaryDirectory(prefix="agi-generated-multisession-1-") as first_tmp:
        session1 = Path(first_tmp).resolve()
        _copy_persistent_state(root, session1)
        _assert_snapshot(
            session1,
            source_ids,
            source_tree,
            source_trials,
            label="generated multisession session 1 reconstruction",
        )
        first = _run_recovered_child(
            session1,
            campaign_id=f"{campaign_id}-r01",
            seed=seed_values[0],
        )
        first_id = str(first["candidate_id"])
        first_ids = _all_candidate_ids(session1)
        if sorted(set(first_ids) - set(source_ids)) != [first_id]:
            raise GeneratedMultiSessionRetentionError(
                "session 1 did not add exactly its generated Candidate"
            )
        _assert_snapshot(
            session1,
            source_ids,
            source_tree,
            source_trials,
            label="generated multisession session 1 learning",
        )
        first_tree, first_trials = _snapshot(session1, first_ids)
        first["pre_candidate_count"] = len(source_ids)
        first["post_candidate_count"] = len(first_ids)
        rounds.append(first)

        with tempfile.TemporaryDirectory(prefix="agi-generated-multisession-2-") as second_tmp:
            session2 = Path(second_tmp).resolve()
            _copy_persistent_state(session1, session2)
            session2_tree, session2_trials = _snapshot(session2, first_ids)
            session_transfer_checks.append(
                {
                    "from_session": 1,
                    "to_session": 2,
                    "candidate_bytes_identical": session2_tree == first_tree,
                    "trial_ledgers_identical": session2_trials == first_trials,
                    "runs_copied": False,
                    "episodes_copied": False,
                    "evidence_copied": False,
                }
            )
            if session2_tree != first_tree or session2_trials != first_trials:
                raise GeneratedMultiSessionRetentionError(
                    "session 2 state-only reconstruction changed durable capability state"
                )

            second = _run_recovered_child(
                session2,
                campaign_id=f"{campaign_id}-r02",
                seed=seed_values[1],
            )
            second_id = str(second["candidate_id"])
            second_ids = _all_candidate_ids(session2)
            if sorted(set(second_ids) - set(first_ids)) != [second_id]:
                raise GeneratedMultiSessionRetentionError(
                    "session 2 did not add exactly its generated Candidate"
                )
            if first_id == second_id:
                raise GeneratedMultiSessionRetentionError(
                    "successive sessions reused a Candidate identity"
                )
            _assert_snapshot(
                session2,
                first_ids,
                first_tree,
                first_trials,
                label="generated multisession session 2 learning",
            )
            second_tree, second_trials = _snapshot(session2, second_ids)
            second["pre_candidate_count"] = len(first_ids)
            second["post_candidate_count"] = len(second_ids)
            rounds.append(second)

            with tempfile.TemporaryDirectory(prefix="agi-generated-multisession-3-") as third_tmp:
                session3 = Path(third_tmp).resolve()
                _copy_persistent_state(session2, session3)
                third_tree, third_trials = _snapshot(session3, second_ids)
                session_transfer_checks.append(
                    {
                        "from_session": 2,
                        "to_session": 3,
                        "candidate_bytes_identical": third_tree == second_tree,
                        "trial_ledgers_identical": third_trials == second_trials,
                        "runs_copied": False,
                        "episodes_copied": False,
                        "evidence_copied": False,
                    }
                )
                if third_tree != second_tree or third_trials != second_trials:
                    raise GeneratedMultiSessionRetentionError(
                        "session 3 state-only reconstruction changed durable capability state"
                    )

                replays = [
                    _replay_without_candidate_ids(
                        session3,
                        seed=seed,
                        selected_goal=str(item["selected_goal"]),
                        candidate_id=str(item["candidate_id"]),
                    )
                    for seed, item in zip(seed_values, rounds, strict=True)
                ]

                remaining_fail_closed: list[dict[str, str]] = []
                for seed, item in zip(seed_values, rounds, strict=True):
                    classification = _classify_goals(session3, _generated_goal_specs(seed))
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
                        "two-session learning unexpectedly eliminated every observed gap"
                    )
                _assert_snapshot(
                    session3,
                    second_ids,
                    second_tree,
                    second_trials,
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
        "state_only_restart_count": 3,
        "learning_session_count": 2,
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
        "all_learned_skills_functionally_replayed": len(replays) == 2,
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
            "Internal bounded multi-session continual-learning evidence only. Two generated skills are "
            "learned in separate Candidate/system-only reconstructed sessions with crash reconciliation, "
            "then rediscovered in a third fresh session without caller Candidate IDs. Task families, "
            "generator, synthesis, regression, scoring, crash injection, and evaluator remain "
            "repository-authored. This is not independent production evidence and does not establish AGI."
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
