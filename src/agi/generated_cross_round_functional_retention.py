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
from agi.durable_state_rehydration import _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.resumable_seeded_generated_goal_round import (
    SimulatedGeneratedGoalInterruption,
    _fingerprint,
    _spec_by_name,
    run_resumable_seeded_generated_goal_round,
)
from agi.seeded_generated_goal_acquisition import _generated_goal_specs
from agi.verified_macro_library_discovery import synthesize_from_verified_macro_library


class GeneratedCrossRoundRetentionError(RuntimeError):
    pass


def _safe_id(value: str) -> bool:
    return bool(value) and all(ch in "abcdefghijklmnopqrstuvwxyz0123456789-_." for ch in value)


def _evidence_path(root: Path, campaign_id: str) -> Path:
    return (
        root
        / ".continual"
        / "evidence"
        / "generated-cross-round-functional-retention"
        / f"{campaign_id}.json"
    )


def _snapshot(
    root: Path,
    candidate_ids: Sequence[str],
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    ids = [str(value) for value in candidate_ids]
    return _tree_snapshot(root, ids), _trial_snapshot(root, ids)


def _assert_snapshot(
    root: Path,
    candidate_ids: Sequence[str],
    tree: dict[str, str],
    trials: dict[str, dict[str, str]],
    *,
    label: str,
) -> None:
    ids = [str(value) for value in candidate_ids]
    if _tree_snapshot(root, ids) != tree:
        raise GeneratedCrossRoundRetentionError(f"{label} changed protected Candidate bytes")
    if _trial_snapshot(root, ids) != trials:
        raise GeneratedCrossRoundRetentionError(f"{label} changed protected Candidate trials")


def _run_recovered_child(
    root: Path,
    *,
    campaign_id: str,
    seed: str,
) -> dict[str, Any]:
    state = run_resumable_seeded_generated_goal_round(
        root,
        campaign_id,
        seed=seed,
        max_new_phases=1,
    )
    interruption_observed = False
    reconciliation_observed = False

    if state.get("status") != "completed" and state.get("completed_phases") == ["select"]:
        try:
            run_resumable_seeded_generated_goal_round(
                root,
                campaign_id,
                seed=seed,
                max_new_phases=1,
                interrupt_after_learn_receipt=True,
            )
        except SimulatedGeneratedGoalInterruption:
            interruption_observed = True
        else:  # pragma: no cover - fail closed if the expected crash point disappears
            raise GeneratedCrossRoundRetentionError(
                "generated child did not interrupt after durable learn receipt"
            )
        reconciled = run_resumable_seeded_generated_goal_round(
            root,
            campaign_id,
            seed=seed,
            max_new_phases=1,
        )
        if reconciled.get("last_reconciled_count") != 1:
            raise GeneratedCrossRoundRetentionError(
                "generated child did not reconcile post-commit receipt without relearning"
            )
        reconciliation_observed = True
        state = reconciled

    finished = run_resumable_seeded_generated_goal_round(
        root,
        campaign_id,
        seed=seed,
    )
    if finished.get("status") != "completed" or finished.get("passed") is not True:
        raise GeneratedCrossRoundRetentionError("generated child round did not complete")
    if finished.get("completed_phases") != ["select", "learn_commit", "verify"]:
        raise GeneratedCrossRoundRetentionError("generated child phase order is incomplete")
    phases = finished.get("phases")
    if not isinstance(phases, dict):
        raise GeneratedCrossRoundRetentionError("generated child phases are missing")
    if any(
        not isinstance(phases.get(phase), dict)
        or int(phases[phase].get("attempts", 0)) != 1
        for phase in ("select", "learn_commit", "verify")
    ):
        raise GeneratedCrossRoundRetentionError(
            "generated child phase was replayed or attempted more than once"
        )

    selection = phases["select"].get("metadata")
    learning = phases["learn_commit"].get("metadata")
    verification = phases["verify"].get("metadata")
    if not all(isinstance(value, dict) for value in (selection, learning, verification)):
        raise GeneratedCrossRoundRetentionError("generated child metadata is incomplete")
    selected_goal = selection.get("selected_goal")
    candidate_id = learning.get("candidate_id")
    unsupported = selection.get("unsupported_goals")
    if (
        not isinstance(selected_goal, str)
        or not isinstance(candidate_id, str)
        or not isinstance(unsupported, list)
        or not all(isinstance(value, str) for value in unsupported)
    ):
        raise GeneratedCrossRoundRetentionError("generated child identity is invalid")
    if selected_goal not in unsupported:
        raise GeneratedCrossRoundRetentionError("generated child selected a supported goal")
    if candidate_id not in verification.get("selected_candidate_ids", []):
        raise GeneratedCrossRoundRetentionError(
            "generated child fresh verification did not rediscover its Candidate"
        )

    return {
        "campaign_id": campaign_id,
        "seed_commitment": _digest(seed),
        "selected_goal": selected_goal,
        "candidate_id": candidate_id,
        "scope": learning.get("scope"),
        "initial_unsupported_goals": list(unsupported),
        "initial_unsupported_count": len(unsupported),
        "child_digest": finished.get("digest"),
        "child_phase_attempts": {
            phase: int(phases[phase]["attempts"])
            for phase in ("select", "learn_commit", "verify")
        },
        "interruption_observed": interruption_observed,
        "reconciliation_observed": reconciliation_observed,
        "negative_evidence_retained": bool(learning.get("negative_evidence_retained")),
    }


def run_generated_cross_round_functional_retention(
    root: Path,
    campaign_id: str,
    *,
    seeds: Sequence[str],
) -> dict[str, Any]:
    """Learn generated skills in successive crash-recovered rounds and functionally replay all of them.

    Byte/trial immutability alone is not treated as retention. After every seed-local generated learning
    round has completed, a *fresh state-only resolver* receives no Candidate IDs and must independently
    rediscover each earlier and later learned Candidate from the durable verified library and solve that
    seed's held-back challenge. At least one previously unsupported generated behavior must remain
    fail-closed after the campaign so two successes are not mislabeled as general coverage.

    The generator, task families, grammar, search, scoring, regression, crash injection, and evaluator are
    repository-authored. The resulting evidence is internal development evidence only and cannot satisfy
    the independent production AGI claim gate.
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
    evidence_path = _evidence_path(root, campaign_id)
    if evidence_path.is_file():
        persisted = json.loads(evidence_path.read_text(encoding="utf-8"))
        if not isinstance(persisted, dict):
            raise GeneratedCrossRoundRetentionError("persisted retention evidence must be an object")
        if persisted.get("campaign_id") != campaign_id:
            raise GeneratedCrossRoundRetentionError("persisted retention campaign identity mismatch")
        if persisted.get("seed_commitments") != [_digest(seed) for seed in seed_values]:
            raise GeneratedCrossRoundRetentionError("persisted retention seed configuration changed")
        if persisted.get("final_fingerprint") != _fingerprint(root):
            raise GeneratedCrossRoundRetentionError(
                "durable Candidate/trial state diverged from persisted retention evidence"
            )
        return persisted

    prerequisite = run_autonomous_heterogeneous_goal_exhaustion(
        root,
        f"{campaign_id}:fixed-prerequisite",
    )
    if prerequisite.get("passed") is not True or prerequisite.get("bounded_goal_set_exhausted") is not True:
        raise GeneratedCrossRoundRetentionError("fixed heterogeneous prerequisite did not pass")

    initial_ids = _all_candidate_ids(root)
    initial_tree, initial_trials = _snapshot(root, initial_ids)
    rounds: list[dict[str, Any]] = []

    for index, seed in enumerate(seed_values):
        pre_ids = _all_candidate_ids(root)
        pre_tree, pre_trials = _snapshot(root, pre_ids)
        child = _run_recovered_child(
            root,
            campaign_id=f"{campaign_id}-r{index + 1:02d}",
            seed=seed,
        )
        _assert_snapshot(
            root,
            pre_ids,
            pre_tree,
            pre_trials,
            label=f"generated round {index + 1}",
        )
        post_ids = _all_candidate_ids(root)
        added = sorted(set(post_ids) - set(pre_ids))
        if added != [str(child["candidate_id"])]:
            raise GeneratedCrossRoundRetentionError(
                "each generated round must add exactly its one verified Candidate"
            )
        child["pre_candidate_count"] = len(pre_ids)
        child["post_candidate_count"] = len(post_ids)
        child["added_candidate_ids"] = added
        rounds.append(child)

    _assert_snapshot(
        root,
        initial_ids,
        initial_tree,
        initial_trials,
        label="cross-round campaign",
    )
    learned_ids = [str(item["candidate_id"]) for item in rounds]
    if len(set(learned_ids)) != len(learned_ids):
        raise GeneratedCrossRoundRetentionError("successive generated rounds reused Candidate identity")

    final_ids = _all_candidate_ids(root)
    final_tree, final_trials = _snapshot(root, final_ids)
    replayed: list[dict[str, Any]] = []
    remaining_fail_closed: list[dict[str, str]] = []

    with tempfile.TemporaryDirectory(prefix="agi-generated-cross-round-retention-") as temporary:
        fresh_root = Path(temporary).resolve()
        _copy_persistent_state(root, fresh_root)
        _assert_snapshot(
            fresh_root,
            final_ids,
            final_tree,
            final_trials,
            label="fresh cross-round restart",
        )

        for seed, item in zip(seed_values, rounds, strict=True):
            selected_goal = str(item["selected_goal"])
            candidate_id = str(item["candidate_id"])
            spec = _spec_by_name(seed, selected_goal)
            result = synthesize_from_verified_macro_library(
                fresh_root,
                input_domain=str(spec["input_domain"]),
                output_domain=str(spec["output_domain"]),
                examples=spec["examples"],
                max_depth=3,
                max_candidates=64,
            )
            if result["candidate_ids_supplied_by_caller"]:
                raise GeneratedCrossRoundRetentionError(
                    "fresh cross-round replay relied on caller Candidate IDs"
                )
            selected_ids = [str(value) for value in result["selected_candidate_ids"]]
            if candidate_id not in selected_ids:
                raise GeneratedCrossRoundRetentionError(
                    "fresh cross-round replay did not rediscover an earlier learned Candidate"
                )
            actual = execute_program(result["compiled_program"], spec["challenge"])
            if actual != spec["challenge_expected"]:
                raise GeneratedCrossRoundRetentionError(
                    "fresh cross-round replay failed a held-back generated challenge"
                )

            classification = _classify_goals(fresh_root, _generated_goal_specs(seed))
            if any(entry.get("candidate_ids_supplied_by_caller") is True for entry in classification):
                raise GeneratedCrossRoundRetentionError(
                    "fresh generated classification relied on caller Candidate IDs"
                )
            by_goal = {str(entry["goal"]): entry for entry in classification}
            if selected_goal not in by_goal or not by_goal[selected_goal]["supported"]:
                raise GeneratedCrossRoundRetentionError(
                    "fresh classification lost a previously learned generated goal"
                )
            for name in item["initial_unsupported_goals"]:
                if name != selected_goal and not by_goal[str(name)]["supported"]:
                    remaining_fail_closed.append(
                        {"seed_commitment": _digest(seed), "goal": str(name)}
                    )
            replayed.append(
                {
                    "seed_commitment": _digest(seed),
                    "selected_goal": selected_goal,
                    "candidate_id": candidate_id,
                    "selected_candidate_ids": selected_ids,
                    "challenge": spec["challenge"],
                    "expected": spec["challenge_expected"],
                    "actual": actual,
                    "caller_supplied_candidate_ids": False,
                }
            )

        if not remaining_fail_closed:
            raise GeneratedCrossRoundRetentionError(
                "successive generated learning unexpectedly eliminated every observed gap"
            )
        _assert_snapshot(
            fresh_root,
            final_ids,
            final_tree,
            final_trials,
            label="fresh cross-round functional replay",
        )

    _assert_snapshot(
        root,
        final_ids,
        final_tree,
        final_trials,
        label="source after fresh cross-round replay",
    )

    report: dict[str, Any] = {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "campaign_kind": "generated-cross-round-functional-retention-v1",
        "passed": True,
        "seed_commitments": [_digest(seed) for seed in seed_values],
        "round_count": len(rounds),
        "rounds": rounds,
        "learned_candidate_ids": learned_ids,
        "distinct_candidate_per_round": len(set(learned_ids)) == len(rounds),
        "all_child_phase_attempts_once": all(
            set(item["child_phase_attempts"].values()) == {1} for item in rounds
        ),
        "all_observed_crashes_reconciled_without_relearning": all(
            item["interruption_observed"] and item["reconciliation_observed"] for item in rounds
        ),
        "initial_candidate_state_unchanged": _tree_snapshot(root, initial_ids) == initial_tree,
        "initial_trial_ledgers_unchanged": _trial_snapshot(root, initial_ids) == initial_trials,
        "fresh_execution_used_for_retention": True,
        "fresh_replay": replayed,
        "all_learned_skills_functionally_replayed": len(replayed) == len(rounds),
        "remaining_fail_closed_goals": remaining_fail_closed,
        "remaining_fail_closed_count": len(remaining_fail_closed),
        "final_fingerprint": _fingerprint(root),
        "claim_boundary": (
            "Internal bounded continual-learning and functional-retention evidence only. The exact task "
            "instances are seed-generated and all learned skills are rediscovered after a fresh state-only "
            "restart without caller Candidate IDs, but task families, generator, grammar, search, regression, "
            "scoring, crash injection, and evaluator remain repository-authored. This is not independent "
            "production evidence and does not establish AGI."
        ),
    }
    if not all(
        (
            report["distinct_candidate_per_round"],
            report["all_child_phase_attempts_once"],
            report["all_observed_crashes_reconciled_without_relearning"],
            report["initial_candidate_state_unchanged"],
            report["initial_trial_ledgers_unchanged"],
            report["all_learned_skills_functionally_replayed"],
            report["remaining_fail_closed_count"] >= 1,
        )
    ):
        raise GeneratedCrossRoundRetentionError("cross-round retention aggregate invariant failed")

    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(evidence_path, report)
    return report
