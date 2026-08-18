from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.autonomous_heterogeneous_goal_acquisition import _classify_goals
from agi.durable_state_rehydration import _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.resumable_seeded_generated_goal_round import (
    _fingerprint,
    _state_path as _child_state_path,
    _validate_state as _validate_child_state,
    run_resumable_seeded_generated_goal_round,
)
from agi.seeded_generated_goal_acquisition import _generated_goal_specs


class MultiSeedGeneratedCurriculumError(RuntimeError):
    pass


class SimulatedMultiSeedInterruption(RuntimeError):
    """Test-only process loss after a child round commits but before parent journaling."""


_SCHEMA_VERSION = 1


def _safe_id(value: str) -> bool:
    return bool(value) and all(ch in "abcdefghijklmnopqrstuvwxyz0123456789-_." for ch in value)


def _state_path(root: Path, campaign_id: str) -> Path:
    return (
        root
        / ".continual"
        / "system"
        / "multi-seed-generated-curricula"
        / f"{campaign_id}.json"
    )


def _evidence_path(root: Path, campaign_id: str) -> Path:
    return (
        root
        / ".continual"
        / "evidence"
        / "multi-seed-resumable-generated-curriculum"
        / f"{campaign_id}.json"
    )


def _seed_commitments(seeds: Sequence[str]) -> list[str]:
    return [_digest(seed) for seed in seeds]


def _snapshot_digest(root: Path, candidate_ids: Sequence[str]) -> tuple[str, str]:
    ids = [str(value) for value in candidate_ids]
    return _digest(_tree_snapshot(root, ids)), _digest(_trial_snapshot(root, ids))


def _initial_state(
    root: Path,
    campaign_id: str,
    seeds: Sequence[str],
) -> dict[str, Any]:
    initial_ids = _all_candidate_ids(root)
    tree_digest, trials_digest = _snapshot_digest(root, initial_ids)
    return {
        "schema_version": _SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "seed_commitments": _seed_commitments(seeds),
        "round_count": len(seeds),
        "initial_candidate_ids": initial_ids,
        "initial_candidate_tree_digest": tree_digest,
        "initial_trial_ledgers_digest": trials_digest,
        "rounds": [
            {
                "index": index,
                "child_campaign_id": f"{campaign_id}-r{index + 1:02d}",
                "seed_commitment": _digest(seed),
                "status": "pending",
                "attempts": 0,
                "metadata": {},
                "result_digest": None,
            }
            for index, seed in enumerate(seeds)
        ],
        "completed_rounds": [],
        "active_round": None,
        "status": "in_progress",
        "passed": False,
        "claim_boundary": (
            "Internal bounded multi-seed continual-learning evidence only. Seeds instantiate fresh exact "
            "behavior instances and child rounds use fresh state-only execution, bounded synthesis, "
            "exact-scope regression, transactional commit, receipt recovery, and fresh verification. "
            "Task families, generator, grammar, search, regression, scoring, and evaluator remain "
            "repository-authored; this is not independent production evidence and does not establish AGI."
        ),
    }


def _validate_state(
    state: dict[str, Any],
    *,
    campaign_id: str,
    seeds: Sequence[str],
) -> None:
    if state.get("schema_version") != _SCHEMA_VERSION:
        raise MultiSeedGeneratedCurriculumError("multi-seed curriculum schema mismatch")
    if state.get("campaign_id") != campaign_id:
        raise MultiSeedGeneratedCurriculumError("multi-seed curriculum campaign mismatch")
    commitments = _seed_commitments(seeds)
    if state.get("seed_commitments") != commitments:
        raise MultiSeedGeneratedCurriculumError("multi-seed curriculum seed configuration changed")
    if state.get("round_count") != len(seeds):
        raise MultiSeedGeneratedCurriculumError("multi-seed curriculum round count changed")
    rounds = state.get("rounds")
    if not isinstance(rounds, list) or len(rounds) != len(seeds):
        raise MultiSeedGeneratedCurriculumError("multi-seed curriculum rounds are invalid")
    for index, (entry, seed) in enumerate(zip(rounds, seeds, strict=True)):
        if not isinstance(entry, dict):
            raise MultiSeedGeneratedCurriculumError("multi-seed round entry must be an object")
        if entry.get("index") != index:
            raise MultiSeedGeneratedCurriculumError("multi-seed round index changed")
        if entry.get("child_campaign_id") != f"{campaign_id}-r{index + 1:02d}":
            raise MultiSeedGeneratedCurriculumError("multi-seed child campaign identity changed")
        if entry.get("seed_commitment") != _digest(seed):
            raise MultiSeedGeneratedCurriculumError("multi-seed round seed changed")
        if entry.get("status") not in {"pending", "running", "completed"}:
            raise MultiSeedGeneratedCurriculumError("multi-seed round status is invalid")


def _load_child_state(root: Path, child_campaign_id: str, seed: str) -> dict[str, Any] | None:
    path = _child_state_path(root, child_campaign_id)
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MultiSeedGeneratedCurriculumError("child generated-goal state must be an object")
    _validate_child_state(
        value,
        campaign_id=child_campaign_id,
        seed_commitment=_digest(seed),
    )
    return value


def _finalize_round(
    root: Path,
    *,
    entry: dict[str, Any],
    seed: str,
    child: dict[str, Any],
) -> dict[str, Any]:
    if child.get("status") != "completed" or child.get("passed") is not True:
        raise MultiSeedGeneratedCurriculumError("child generated-goal round is not completed")
    if child.get("completed_phases") != ["select", "learn_commit", "verify"]:
        raise MultiSeedGeneratedCurriculumError("child generated-goal phase order is incomplete")
    phases = child.get("phases")
    if not isinstance(phases, dict):
        raise MultiSeedGeneratedCurriculumError("child generated-goal phases are missing")
    for phase in ("select", "learn_commit", "verify"):
        phase_entry = phases.get(phase)
        if not isinstance(phase_entry, dict) or phase_entry.get("attempts") != 1:
            raise MultiSeedGeneratedCurriculumError(
                "child generated-goal phase was replayed or lacks exactly one attempt"
            )

    pre = entry.get("metadata")
    if not isinstance(pre, dict):
        raise MultiSeedGeneratedCurriculumError("running multi-seed round lacks pre-state metadata")
    pre_ids = pre.get("pre_candidate_ids")
    if not isinstance(pre_ids, list) or not all(isinstance(value, str) for value in pre_ids):
        raise MultiSeedGeneratedCurriculumError("running multi-seed round lacks Candidate pre-state")
    pre_tree_digest = pre.get("pre_candidate_tree_digest")
    pre_trials_digest = pre.get("pre_trial_ledgers_digest")
    current_tree_digest, current_trials_digest = _snapshot_digest(root, pre_ids)
    if current_tree_digest != pre_tree_digest:
        raise MultiSeedGeneratedCurriculumError("child round changed protected Candidate bytes")
    if current_trials_digest != pre_trials_digest:
        raise MultiSeedGeneratedCurriculumError("child round changed protected Candidate trials")

    selection = phases["select"].get("metadata")
    learning = phases["learn_commit"].get("metadata")
    verification = phases["verify"].get("metadata")
    if not all(isinstance(value, dict) for value in (selection, learning, verification)):
        raise MultiSeedGeneratedCurriculumError("child generated-goal metadata is incomplete")
    selected_goal = selection.get("selected_goal")
    candidate_id = learning.get("candidate_id")
    scope = learning.get("scope")
    initial_unsupported = selection.get("unsupported_goals")
    remaining = verification.get("remaining_unsupported_goals")
    selected_ids = verification.get("selected_candidate_ids")
    if (
        not isinstance(selected_goal, str)
        or not isinstance(candidate_id, str)
        or not isinstance(scope, str)
        or not isinstance(initial_unsupported, list)
        or not isinstance(remaining, list)
        or not isinstance(selected_ids, list)
    ):
        raise MultiSeedGeneratedCurriculumError("child generated-goal result identity is invalid")
    if candidate_id not in selected_ids:
        raise MultiSeedGeneratedCurriculumError("fresh child verification did not rediscover Candidate")
    if selected_goal not in initial_unsupported or selected_goal in remaining:
        raise MultiSeedGeneratedCurriculumError("child generated-goal support did not improve selected goal")
    if len(remaining) >= len(initial_unsupported):
        raise MultiSeedGeneratedCurriculumError("child generated-goal unsupported count did not decrease")
    if not remaining:
        raise MultiSeedGeneratedCurriculumError("child round unexpectedly erased every generated gap")

    post_ids = _all_candidate_ids(root)
    added_ids = sorted(set(post_ids) - set(pre_ids))
    if added_ids != [candidate_id]:
        raise MultiSeedGeneratedCurriculumError(
            "child round must add exactly its one verified Candidate"
        )
    post_fingerprint = learning.get("post_commit_fingerprint")
    if not isinstance(post_fingerprint, dict) or post_fingerprint != _fingerprint(root):
        raise MultiSeedGeneratedCurriculumError(
            "child receipt fingerprint does not match current durable Candidate/trial state"
        )

    return {
        "seed_commitment": _digest(seed),
        "child_campaign_id": entry["child_campaign_id"],
        "child_digest": child.get("digest"),
        "selected_goal": selected_goal,
        "candidate_id": candidate_id,
        "scope": scope,
        "initial_unsupported_count": len(initial_unsupported),
        "remaining_unsupported_count": len(remaining),
        "remaining_unsupported_goals": list(remaining),
        "selected_candidate_ids": list(selected_ids),
        "negative_evidence_retained": bool(learning.get("negative_evidence_retained")),
        "pre_candidate_count": len(pre_ids),
        "post_candidate_count": len(post_ids),
        "added_candidate_ids": added_ids,
        "child_phase_attempts": {
            phase: int(phases[phase]["attempts"])
            for phase in ("select", "learn_commit", "verify")
        },
    }


def _fresh_final_validation(
    root: Path,
    *,
    seeds: Sequence[str],
    round_metadata: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    all_ids = _all_candidate_ids(root)
    tree = _tree_snapshot(root, all_ids)
    trials = _trial_snapshot(root, all_ids)
    with tempfile.TemporaryDirectory(prefix="agi-multi-seed-generated-final-") as temporary:
        fresh_root = Path(temporary).resolve()
        _copy_persistent_state(root, fresh_root)
        if _tree_snapshot(fresh_root, all_ids) != tree:
            raise MultiSeedGeneratedCurriculumError("fresh final restart changed Candidate bytes")
        if _trial_snapshot(fresh_root, all_ids) != trials:
            raise MultiSeedGeneratedCurriculumError("fresh final restart changed Candidate trials")
        selected_support: list[dict[str, Any]] = []
        for seed, metadata in zip(seeds, round_metadata, strict=True):
            classification = _classify_goals(fresh_root, _generated_goal_specs(seed))
            if any(item.get("candidate_ids_supplied_by_caller") is True for item in classification):
                raise MultiSeedGeneratedCurriculumError(
                    "fresh final classification relied on caller Candidate IDs"
                )
            by_goal = {str(item["goal"]): item for item in classification}
            selected_goal = str(metadata["selected_goal"])
            if selected_goal not in by_goal or not by_goal[selected_goal]["supported"]:
                raise MultiSeedGeneratedCurriculumError(
                    "fresh final restart lost a previously learned generated goal"
                )
            selected_support.append(
                {
                    "seed_commitment": _digest(seed),
                    "selected_goal": selected_goal,
                    "supported": True,
                }
            )
        if _tree_snapshot(fresh_root, all_ids) != tree:
            raise MultiSeedGeneratedCurriculumError("fresh validation mutated Candidate bytes")
        if _trial_snapshot(fresh_root, all_ids) != trials:
            raise MultiSeedGeneratedCurriculumError("fresh validation mutated Candidate trials")
    if _tree_snapshot(root, all_ids) != tree or _trial_snapshot(root, all_ids) != trials:
        raise MultiSeedGeneratedCurriculumError("fresh validation mutated durable source state")
    return {
        "fresh_execution": True,
        "caller_supplied_candidate_ids": False,
        "selected_support": selected_support,
        "candidate_count": len(all_ids),
        "candidate_tree_digest": _digest(tree),
        "trial_ledgers_digest": _digest(trials),
    }


def run_multi_seed_resumable_generated_curriculum(
    root: Path,
    campaign_id: str,
    *,
    seeds: Sequence[str],
    max_new_rounds: int | None = None,
    interrupt_after_child_round: int | None = None,
) -> dict[str, Any]:
    """Accumulate one verified generated skill per seed with resumable parent/child journals.

    Every round persists its parent intent before invoking the existing resumable child campaign. If the
    process disappears after the child has fully committed and verified its Candidate but before the
    parent marks the round complete, recovery validates the completed child state and reconciles the
    parent without invoking learning again. Across distinct seeds, exactly one Candidate is added per
    round, all earlier Candidate bytes and trial ledgers remain unchanged, and a final fresh state-only
    restart must retain every selected generated behavior.
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
    if max_new_rounds is not None and max_new_rounds < 1:
        raise ValueError("max_new_rounds must be positive when supplied")
    if interrupt_after_child_round is not None and not (
        0 <= interrupt_after_child_round < len(seed_values)
    ):
        raise ValueError("interrupt_after_child_round is outside the configured rounds")

    root = root.resolve()
    path = _state_path(root, campaign_id)
    if path.is_file():
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise MultiSeedGeneratedCurriculumError("multi-seed state must be an object")
        _validate_state(state, campaign_id=campaign_id, seeds=seed_values)
    else:
        state = _initial_state(root, campaign_id, seed_values)
        _atomic_json(path, state)

    completed_before = tuple(int(value) for value in state["completed_rounds"])
    immutable = {
        index: {
            "attempts": int(state["rounds"][index]["attempts"]),
            "result_digest": state["rounds"][index]["result_digest"],
            "metadata": json.loads(
                json.dumps(state["rounds"][index]["metadata"], sort_keys=True)
            ),
        }
        for index in completed_before
    }

    processed = 0
    advanced = 0
    reconciled = 0
    for index, seed in enumerate(seed_values):
        entry = state["rounds"][index]
        if entry["status"] == "completed":
            continue
        if max_new_rounds is not None and processed >= max_new_rounds:
            break

        child: dict[str, Any] | None = None
        if entry["status"] == "running":
            child = _load_child_state(root, str(entry["child_campaign_id"]), seed)
            if child is not None and child.get("status") == "completed" and child.get("passed") is True:
                result = _finalize_round(root, entry=entry, seed=seed, child=child)
                entry["status"] = "completed"
                entry["metadata"] = result
                entry["result_digest"] = _digest(result)
                if index not in state["completed_rounds"]:
                    state["completed_rounds"].append(index)
                state["active_round"] = None
                reconciled += 1
                processed += 1
                _atomic_json(path, state)
                continue

        if entry["status"] != "running":
            pre_ids = _all_candidate_ids(root)
            pre_tree_digest, pre_trials_digest = _snapshot_digest(root, pre_ids)
            entry["status"] = "running"
            entry["attempts"] = int(entry.get("attempts", 0)) + 1
            entry["metadata"] = {
                "pre_candidate_ids": pre_ids,
                "pre_candidate_tree_digest": pre_tree_digest,
                "pre_trial_ledgers_digest": pre_trials_digest,
            }
            state["active_round"] = index
            _atomic_json(path, state)
        elif int(entry.get("attempts", 0)) != 1:
            raise MultiSeedGeneratedCurriculumError("running parent round attempt count changed")

        child = run_resumable_seeded_generated_goal_round(
            root,
            str(entry["child_campaign_id"]),
            seed=seed,
        )
        if child.get("status") != "completed" or child.get("passed") is not True:
            raise MultiSeedGeneratedCurriculumError("child generated-goal round did not complete")
        if interrupt_after_child_round == index:
            raise SimulatedMultiSeedInterruption(
                "simulated process loss after child completion before parent round journal"
            )

        result = _finalize_round(root, entry=entry, seed=seed, child=child)
        entry["status"] = "completed"
        entry["metadata"] = result
        entry["result_digest"] = _digest(result)
        if index not in state["completed_rounds"]:
            state["completed_rounds"].append(index)
        state["active_round"] = None
        advanced += 1
        processed += 1
        _atomic_json(path, state)

    for index, prior in immutable.items():
        entry = state["rounds"][index]
        if int(entry["attempts"]) != prior["attempts"]:
            raise MultiSeedGeneratedCurriculumError("completed parent round was replayed")
        if entry["result_digest"] != prior["result_digest"]:
            raise MultiSeedGeneratedCurriculumError("completed parent round digest changed")
        if entry["metadata"] != prior["metadata"]:
            raise MultiSeedGeneratedCurriculumError("completed parent round metadata changed")

    complete = state["completed_rounds"] == list(range(len(seed_values)))
    state["status"] = "completed" if complete else "in_progress"
    state["passed"] = complete
    state["active_round"] = None
    state["completed_count"] = len(state["completed_rounds"])
    state["resume_skipped_completed"] = len(completed_before)
    state["last_advance_count"] = advanced
    state["last_reconciled_count"] = reconciled
    state["parent_intent_persisted_before_child_side_effects"] = True
    state["completed_child_reconciled_without_relearning"] = True
    state["one_verified_candidate_per_round"] = True

    if complete:
        initial_ids = [str(value) for value in state["initial_candidate_ids"]]
        tree_digest, trials_digest = _snapshot_digest(root, initial_ids)
        if tree_digest != state["initial_candidate_tree_digest"]:
            raise MultiSeedGeneratedCurriculumError("campaign changed initial Candidate bytes")
        if trials_digest != state["initial_trial_ledgers_digest"]:
            raise MultiSeedGeneratedCurriculumError("campaign changed initial Candidate trials")
        round_metadata = [dict(entry["metadata"]) for entry in state["rounds"]]
        candidate_ids = [str(item["candidate_id"]) for item in round_metadata]
        if len(set(candidate_ids)) != len(seed_values):
            raise MultiSeedGeneratedCurriculumError("multi-seed rounds did not add distinct Candidates")
        final_ids = _all_candidate_ids(root)
        if len(final_ids) != len(initial_ids) + len(seed_values):
            raise MultiSeedGeneratedCurriculumError(
                "multi-seed campaign did not add exactly one Candidate per round"
            )
        state["selected_goals"] = [str(item["selected_goal"]) for item in round_metadata]
        state["learned_candidate_ids"] = candidate_ids
        state["unsupported_count_decreased_each_round"] = all(
            int(item["remaining_unsupported_count"]) < int(item["initial_unsupported_count"])
            for item in round_metadata
        )
        state["initial_state_unchanged"] = True
        state["fresh_final_validation"] = _fresh_final_validation(
            root,
            seeds=seed_values,
            round_metadata=round_metadata,
        )
        if not state["unsupported_count_decreased_each_round"]:
            raise MultiSeedGeneratedCurriculumError(
                "multi-seed aggregate unsupported count invariant failed"
            )

    state["digest"] = _digest({key: value for key, value in state.items() if key != "digest"})
    _atomic_json(path, state)
    if complete:
        _atomic_json(_evidence_path(root, campaign_id), state)
    return state
