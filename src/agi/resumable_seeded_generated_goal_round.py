from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import execute_program
from agi.autonomous_heterogeneous_goal_acquisition import _classify_goals, _goal_specs, _library_attempt
from agi.durable_state_rehydration import _run_one, _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.seeded_generated_goal_acquisition import _generated_goal_specs
from agi.transactional_multisession_commit import _commit_verified_candidate
from agi.verified_macro_library_discovery import synthesize_from_verified_macro_library
from continual.candidate_regression import candidate_verified_for_scope


class ResumableGeneratedGoalError(RuntimeError):
    pass


class SimulatedGeneratedGoalInterruption(RuntimeError):
    """Test-only process loss after durable Candidate commit and immutable receipt."""


_SCHEMA_VERSION = 1
_RECEIPT_SCHEMA_VERSION = 1
_PHASE_ORDER = ("select", "learn_commit", "verify")


def _safe_id(value: str) -> bool:
    return bool(value) and all(ch in "abcdefghijklmnopqrstuvwxyz0123456789-_." for ch in value)


def _state_path(root: Path, campaign_id: str) -> Path:
    return root / ".continual" / "system" / "generated-goal-curricula" / f"{campaign_id}.json"


def _receipt_path(root: Path, campaign_id: str) -> Path:
    return (
        root
        / ".continual"
        / "system"
        / "generated-goal-curricula"
        / f"{campaign_id}.learn-receipt.json"
    )


def _evidence_path(root: Path, campaign_id: str) -> Path:
    return (
        root
        / ".continual"
        / "evidence"
        / "resumable-seeded-generated-goal-round"
        / f"{campaign_id}.json"
    )


def _fingerprint(root: Path) -> dict[str, Any]:
    ids = _all_candidate_ids(root)
    tree = _tree_snapshot(root, ids)
    trials = _trial_snapshot(root, ids)
    return {
        "candidate_ids": ids,
        "candidate_ids_digest": _digest(ids),
        "candidate_tree_digest": _digest(tree),
        "trial_ledgers_digest": _digest(trials),
        "aggregate_digest": _digest({"ids": ids, "tree": tree, "trials": trials}),
    }


def _initial_state(campaign_id: str, seed_commitment: str) -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "seed_commitment": seed_commitment,
        "phase_order": list(_PHASE_ORDER),
        "phases": {
            phase: {
                "status": "pending",
                "attempts": 0,
                "metadata": {},
                "result_digest": None,
            }
            for phase in _PHASE_ORDER
        },
        "completed_phases": [],
        "active_phase": None,
        "status": "in_progress",
        "passed": False,
        "claim_boundary": (
            "Internal bounded crash-recovery evidence only. Selection intent, Candidate commit receipt, "
            "fresh restart verification, task generation, grammar, regression, search, and evaluator are "
            "repository-authored and do not establish independent production AGI evidence."
        ),
    }


def _load_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResumableGeneratedGoalError("generated-goal state must be an object")
    return value


def _validate_state(
    state: Mapping[str, Any],
    *,
    campaign_id: str,
    seed_commitment: str,
) -> None:
    if state.get("schema_version") != _SCHEMA_VERSION:
        raise ResumableGeneratedGoalError("generated-goal state schema mismatch")
    if state.get("campaign_id") != campaign_id:
        raise ResumableGeneratedGoalError("generated-goal campaign identity mismatch")
    if state.get("seed_commitment") != seed_commitment:
        raise ResumableGeneratedGoalError("generated-goal seed commitment changed")
    if state.get("phase_order") != list(_PHASE_ORDER):
        raise ResumableGeneratedGoalError("generated-goal phase order changed")
    phases = state.get("phases")
    if not isinstance(phases, dict):
        raise ResumableGeneratedGoalError("generated-goal phases must be an object")
    for phase in _PHASE_ORDER:
        entry = phases.get(phase)
        if not isinstance(entry, dict) or entry.get("status") not in {
            "pending",
            "running",
            "completed",
        }:
            raise ResumableGeneratedGoalError(f"invalid generated-goal phase state: {phase}")


def _spec_by_name(seed: str, name: str) -> dict[str, Any]:
    specs = _generated_goal_specs(seed)
    try:
        return next(spec for spec in specs if spec["name"] == name)
    except StopIteration as exc:
        raise ResumableGeneratedGoalError(
            "persisted generated-goal selection is not derivable from committed seed"
        ) from exc


def _assert_fixed_goal_base(root: Path) -> None:
    classification = _classify_goals(root, _goal_specs())
    unsupported = [str(item["goal"]) for item in classification if not item["supported"]]
    if unsupported:
        raise ResumableGeneratedGoalError(
            "resumable generated-goal round requires the fixed heterogeneous base to be exhausted first"
        )
    if any(item.get("candidate_ids_supplied_by_caller") is True for item in classification):
        raise ResumableGeneratedGoalError(
            "fixed-goal prerequisite classification relied on caller Candidate IDs"
        )


def _run_select(root: Path, *, seed: str) -> dict[str, Any]:
    _assert_fixed_goal_base(root)
    specs = _generated_goal_specs(seed)
    prior_ids = _all_candidate_ids(root)
    prior_tree = _tree_snapshot(root, prior_ids)
    prior_trials = _trial_snapshot(root, prior_ids)
    with tempfile.TemporaryDirectory(prefix="agi-resumable-generated-select-") as temporary:
        fresh_root = Path(temporary).resolve()
        _copy_persistent_state(root, fresh_root)
        if _tree_snapshot(fresh_root, prior_ids) != prior_tree:
            raise ResumableGeneratedGoalError("selection restart changed Candidate bytes")
        if _trial_snapshot(fresh_root, prior_ids) != prior_trials:
            raise ResumableGeneratedGoalError("selection restart changed Candidate trials")
        classification = _classify_goals(fresh_root, specs)
        if any(item.get("candidate_ids_supplied_by_caller") is True for item in classification):
            raise ResumableGeneratedGoalError(
                "generated-goal selection relied on caller Candidate IDs"
            )
        unsupported = sorted(
            str(item["goal"]) for item in classification if not item["supported"]
        )
        if len(unsupported) < 2:
            raise ResumableGeneratedGoalError(
                "generated-goal selection did not expose multiple fail-closed gaps"
            )
    spec_digest = _digest(
        [
            {
                "name": spec["name"],
                "input_domain": spec["input_domain"],
                "output_domain": spec["output_domain"],
                "generated_parameter": spec["generated_parameter"],
                "probe": spec["probe"],
                "expected": spec["expected"],
                "challenge": spec["challenge"],
                "challenge_expected": spec["challenge_expected"],
            }
            for spec in specs
        ]
    )
    commitment = _digest(
        {
            "seed": seed,
            "spec_digest": spec_digest,
            "unsupported": unsupported,
            "classification": classification,
            "phase": "resumable-generated-goal-selection-v1",
        }
    )
    selected = unsupported[int(commitment[:8], 16) % len(unsupported)]
    return {
        "selected_goal": selected,
        "unsupported_goals": unsupported,
        "initial_classification": classification,
        "selection_commitment": commitment,
        "generated_spec_digest": spec_digest,
        "prior_candidate_ids": prior_ids,
        "prior_tree_digest": _digest(prior_tree),
        "prior_trials_digest": _digest(prior_trials),
    }


def _run_learn_commit(
    root: Path,
    *,
    seed: str,
    selected_goal: str,
    attempt: int,
) -> dict[str, Any]:
    spec = _spec_by_name(seed, selected_goal)
    prior_ids = _all_candidate_ids(root)
    prior_tree = _tree_snapshot(root, prior_ids)
    prior_trials = _trial_snapshot(root, prior_ids)
    with tempfile.TemporaryDirectory(prefix="agi-resumable-generated-learn-") as temporary:
        learner_root = Path(temporary).resolve()
        _copy_persistent_state(root, learner_root)
        if _tree_snapshot(learner_root, prior_ids) != prior_tree:
            raise ResumableGeneratedGoalError("learner restart changed Candidate bytes")
        if _trial_snapshot(learner_root, prior_ids) != prior_trials:
            raise ResumableGeneratedGoalError("learner restart changed Candidate trials")
        if _library_attempt(learner_root, spec)["supported"]:
            raise ResumableGeneratedGoalError(
                "persisted selected generated goal was already supported before learning"
            )
        acquired = _promote_task(
            learner_root,
            seed=f"{seed}:resumable-generated:{selected_goal}",
            spec=spec,
        )
        candidate_id = str(acquired["candidate_id"])
        scope = str(acquired["scope"])
        if candidate_id in prior_ids:
            raise ResumableGeneratedGoalError(
                "generated-goal learn phase reused an existing Candidate identity"
            )
        if not acquired.get("negative_evidence_retained"):
            raise ResumableGeneratedGoalError(
                "generated-goal learn phase lost protected negative evidence"
            )
        if not _trial_snapshot(learner_root, [candidate_id]).get(candidate_id):
            raise ResumableGeneratedGoalError(
                "generated-goal learn phase lacks Candidate regression trials"
            )
        if _tree_snapshot(learner_root, prior_ids) != prior_tree:
            raise ResumableGeneratedGoalError("learning changed protected Candidate bytes")
        if _trial_snapshot(learner_root, prior_ids) != prior_trials:
            raise ResumableGeneratedGoalError("learning changed protected Candidate trials")
        commit = _commit_verified_candidate(
            learner_root,
            root,
            candidate_id=candidate_id,
            scope=scope,
        )
    if _tree_snapshot(root, prior_ids) != prior_tree:
        raise ResumableGeneratedGoalError("transactional commit changed prior Candidate bytes")
    if _trial_snapshot(root, prior_ids) != prior_trials:
        raise ResumableGeneratedGoalError("transactional commit changed prior Candidate trials")
    return {
        "candidate_id": candidate_id,
        "scope": scope,
        "negative_evidence_retained": True,
        "transactional_commit": commit,
        "attempt": attempt,
        "post_commit_fingerprint": _fingerprint(root),
    }


def _write_learn_receipt(
    root: Path,
    *,
    campaign_id: str,
    seed_commitment: str,
    selected_goal: str,
    attempt: int,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema_version": _RECEIPT_SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "seed_commitment": seed_commitment,
        "selected_goal": selected_goal,
        "attempt": attempt,
        "candidate_id": result["candidate_id"],
        "scope": result["scope"],
        "negative_evidence_retained": bool(result["negative_evidence_retained"]),
        "transactional_commit": result["transactional_commit"],
        "post_commit_fingerprint": result["post_commit_fingerprint"],
    }
    receipt["receipt_digest"] = _digest(receipt)
    path = _receipt_path(root, campaign_id)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != receipt:
            raise ResumableGeneratedGoalError(
                "generated-goal learn receipt already exists with different content"
            )
        return receipt
    _atomic_json(path, receipt)
    return receipt


def _validate_learn_receipt(
    root: Path,
    receipt: Mapping[str, Any],
    *,
    campaign_id: str,
    seed_commitment: str,
    selected_goal: str,
    expected_attempt: int,
) -> dict[str, Any]:
    if receipt.get("schema_version") != _RECEIPT_SCHEMA_VERSION:
        raise ResumableGeneratedGoalError("generated-goal receipt schema mismatch")
    if receipt.get("campaign_id") != campaign_id:
        raise ResumableGeneratedGoalError("generated-goal receipt campaign mismatch")
    if receipt.get("seed_commitment") != seed_commitment:
        raise ResumableGeneratedGoalError("generated-goal receipt seed mismatch")
    if receipt.get("selected_goal") != selected_goal:
        raise ResumableGeneratedGoalError("generated-goal receipt selection mismatch")
    if receipt.get("attempt") != expected_attempt:
        raise ResumableGeneratedGoalError("generated-goal receipt attempt mismatch")
    supplied_digest = receipt.get("receipt_digest")
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    if not isinstance(supplied_digest, str) or supplied_digest != _digest(unsigned):
        raise ResumableGeneratedGoalError("generated-goal receipt digest mismatch")
    if receipt.get("post_commit_fingerprint") != _fingerprint(root):
        raise ResumableGeneratedGoalError(
            "durable Candidate/trial state no longer matches generated-goal receipt"
        )
    candidate_id = receipt.get("candidate_id")
    scope = receipt.get("scope")
    if not isinstance(candidate_id, str) or not isinstance(scope, str):
        raise ResumableGeneratedGoalError("generated-goal receipt lacks Candidate identity/scope")
    candidate_path = root / ".continual" / "candidates" / candidate_id / "candidate.json"
    if not candidate_path.is_file():
        raise ResumableGeneratedGoalError("generated-goal receipt Candidate is missing")
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    if not isinstance(candidate, dict) or candidate.get("candidate_id") != candidate_id:
        raise ResumableGeneratedGoalError("generated-goal receipt Candidate identity is invalid")
    if not candidate_verified_for_scope(root, candidate, scope):
        raise ResumableGeneratedGoalError(
            "generated-goal receipt Candidate is not verified for exact scope"
        )
    return {
        "candidate_id": candidate_id,
        "scope": scope,
        "negative_evidence_retained": bool(receipt.get("negative_evidence_retained")),
        "transactional_commit": receipt.get("transactional_commit"),
        "post_commit_fingerprint": receipt.get("post_commit_fingerprint"),
        "receipt_digest": supplied_digest,
    }


def _load_valid_receipt(
    root: Path,
    *,
    campaign_id: str,
    seed_commitment: str,
    selected_goal: str,
    expected_attempt: int,
) -> dict[str, Any] | None:
    path = _receipt_path(root, campaign_id)
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResumableGeneratedGoalError("generated-goal receipt must be an object")
    return _validate_learn_receipt(
        root,
        value,
        campaign_id=campaign_id,
        seed_commitment=seed_commitment,
        selected_goal=selected_goal,
        expected_attempt=expected_attempt,
    )


def _run_verify(
    root: Path,
    *,
    seed: str,
    selected_goal: str,
    candidate_id: str,
    initial_unsupported: list[str],
) -> dict[str, Any]:
    spec = _spec_by_name(seed, selected_goal)
    all_ids = _all_candidate_ids(root)
    tree = _tree_snapshot(root, all_ids)
    trials = _trial_snapshot(root, all_ids)
    with tempfile.TemporaryDirectory(prefix="agi-resumable-generated-verify-") as temporary:
        fresh_root = Path(temporary).resolve()
        _copy_persistent_state(root, fresh_root)
        if _tree_snapshot(fresh_root, all_ids) != tree:
            raise ResumableGeneratedGoalError("verification restart changed Candidate bytes")
        if _trial_snapshot(fresh_root, all_ids) != trials:
            raise ResumableGeneratedGoalError("verification restart changed Candidate trials")
        result = synthesize_from_verified_macro_library(
            fresh_root,
            input_domain=str(spec["input_domain"]),
            output_domain=str(spec["output_domain"]),
            examples=spec["examples"],
            max_depth=3,
            max_candidates=64,
        )
        if result["candidate_ids_supplied_by_caller"]:
            raise ResumableGeneratedGoalError(
                "verification relied on caller Candidate IDs"
            )
        selected_ids = [str(value) for value in result["selected_candidate_ids"]]
        if candidate_id not in selected_ids:
            raise ResumableGeneratedGoalError(
                "verification did not rediscover receipt Candidate"
            )
        if execute_program(result["compiled_program"], spec["challenge"]) != spec[
            "challenge_expected"
        ]:
            raise ResumableGeneratedGoalError(
                "receipt-recovered generated skill failed held-back challenge"
            )
        fresh_run = _run_one(
            fresh_root,
            candidate_id=candidate_id,
            value=spec["challenge"],
            expected=spec["challenge_expected"],
        )
        post = _classify_goals(fresh_root, _generated_goal_specs(seed))
        by_goal = {str(item["goal"]): item for item in post}
        if not by_goal[selected_goal]["supported"]:
            raise ResumableGeneratedGoalError(
                "receipt-recovered selected goal remained unsupported"
            )
        remaining = sorted(
            name
            for name in initial_unsupported
            if name != selected_goal and not by_goal[name]["supported"]
        )
        if not remaining:
            raise ResumableGeneratedGoalError(
                "receipt-recovered one-gap learning unexpectedly erased all generated gaps"
            )
        if _tree_snapshot(fresh_root, all_ids) != tree:
            raise ResumableGeneratedGoalError("verification changed Candidate bytes")
        if _trial_snapshot(fresh_root, all_ids) != trials:
            raise ResumableGeneratedGoalError("verification changed Candidate trials")
    if _tree_snapshot(root, all_ids) != tree or _trial_snapshot(root, all_ids) != trials:
        raise ResumableGeneratedGoalError("verification mutated durable source learning state")
    return {
        "selected_candidate_ids": selected_ids,
        "fresh_engine_run": fresh_run,
        "remaining_unsupported_goals": remaining,
        "remaining_unsupported_count": len(remaining),
        "post_classification": post,
    }


def run_resumable_seeded_generated_goal_round(
    root: Path,
    campaign_id: str,
    *,
    seed: str,
    max_new_phases: int | None = None,
    interrupt_after_learn_receipt: bool = False,
) -> dict[str, Any]:
    """Persist selection intent and reconcile a post-commit/pre-journal process loss without relearning.

    The fixed heterogeneous prerequisite must already be exhausted by an earlier verified campaign. The
    first invocation selects from fresh seed-generated fail-closed gaps and persists that exact intent.
    The learning phase sets a running journal entry *before* side effects, performs bounded exact-scope
    acquisition in a fresh learner, transactionally commits the Candidate, and writes an immutable receipt
    bound to the campaign, seed, selection, attempt, Candidate/scope, and complete Candidate/trial
    fingerprint before marking the phase completed. If the process disappears in that crash window, a
    later invocation validates the receipt and reconciles the running phase without repeating learning or
    the commit. Tampered, rebound, stale, or state-mismatched receipts fail closed.

    This remains internal repository-authored resilience/self-improvement evidence and cannot satisfy the
    independent production AGI evidence gate.
    """

    if not _safe_id(campaign_id):
        raise ValueError("campaign_id must be a safe lowercase identifier")
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    if max_new_phases is not None and max_new_phases < 1:
        raise ValueError("max_new_phases must be positive when supplied")

    root = root.resolve()
    seed_commitment = _digest(seed)
    path = _state_path(root, campaign_id)
    state = _load_state(path)
    if state is None:
        state = _initial_state(campaign_id, seed_commitment)
        _atomic_json(path, state)
    else:
        _validate_state(
            state,
            campaign_id=campaign_id,
            seed_commitment=seed_commitment,
        )

    completed_before = tuple(state["completed_phases"])
    immutable = {
        phase: {
            "attempts": int(state["phases"][phase]["attempts"]),
            "result_digest": state["phases"][phase]["result_digest"],
            "metadata": json.loads(json.dumps(state["phases"][phase]["metadata"], sort_keys=True)),
        }
        for phase in completed_before
    }

    processed = 0
    advanced = 0
    reconciled = 0
    for phase in _PHASE_ORDER:
        entry = state["phases"][phase]
        if entry["status"] == "completed":
            continue
        if max_new_phases is not None and processed >= max_new_phases:
            break

        if phase == "learn_commit" and entry["status"] == "running":
            selection = state["phases"]["select"]["metadata"]
            selected_goal = selection.get("selected_goal")
            if not isinstance(selected_goal, str):
                raise ResumableGeneratedGoalError(
                    "running learn phase lacks persisted selection intent"
                )
            receipt = _load_valid_receipt(
                root,
                campaign_id=campaign_id,
                seed_commitment=seed_commitment,
                selected_goal=selected_goal,
                expected_attempt=int(entry["attempts"]),
            )
            if receipt is not None:
                entry["status"] = "completed"
                entry["metadata"] = receipt
                entry["result_digest"] = str(receipt["receipt_digest"])
                if phase not in state["completed_phases"]:
                    state["completed_phases"].append(phase)
                state["active_phase"] = None
                reconciled += 1
                processed += 1
                _atomic_json(path, state)
                continue

        entry["status"] = "running"
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        state["active_phase"] = phase
        _atomic_json(path, state)

        if phase == "select":
            result = _run_select(root, seed=seed)
        elif phase == "learn_commit":
            selection = state["phases"]["select"]["metadata"]
            selected_goal = selection.get("selected_goal")
            if not isinstance(selected_goal, str):
                raise ResumableGeneratedGoalError(
                    "learn phase lacks completed persisted selection"
                )
            learned = _run_learn_commit(
                root,
                seed=seed,
                selected_goal=selected_goal,
                attempt=int(entry["attempts"]),
            )
            receipt = _write_learn_receipt(
                root,
                campaign_id=campaign_id,
                seed_commitment=seed_commitment,
                selected_goal=selected_goal,
                attempt=int(entry["attempts"]),
                result=learned,
            )
            if interrupt_after_learn_receipt:
                raise SimulatedGeneratedGoalInterruption(
                    "simulated process loss after generated-goal Candidate commit receipt"
                )
            result = _validate_learn_receipt(
                root,
                receipt,
                campaign_id=campaign_id,
                seed_commitment=seed_commitment,
                selected_goal=selected_goal,
                expected_attempt=int(entry["attempts"]),
            )
        elif phase == "verify":
            selection = state["phases"]["select"]["metadata"]
            learning = state["phases"]["learn_commit"]["metadata"]
            selected_goal = selection.get("selected_goal")
            candidate_id = learning.get("candidate_id")
            unsupported = selection.get("unsupported_goals")
            if (
                not isinstance(selected_goal, str)
                or not isinstance(candidate_id, str)
                or not isinstance(unsupported, list)
                or not all(isinstance(value, str) for value in unsupported)
            ):
                raise ResumableGeneratedGoalError(
                    "verify phase lacks persisted selection/learning metadata"
                )
            result = _run_verify(
                root,
                seed=seed,
                selected_goal=selected_goal,
                candidate_id=candidate_id,
                initial_unsupported=list(unsupported),
            )
        else:  # pragma: no cover - fixed phase order
            raise ResumableGeneratedGoalError(phase)

        entry["status"] = "completed"
        entry["metadata"] = result
        entry["result_digest"] = _digest(result)
        if phase not in state["completed_phases"]:
            state["completed_phases"].append(phase)
        state["active_phase"] = None
        advanced += 1
        processed += 1
        _atomic_json(path, state)

    for phase, prior in immutable.items():
        entry = state["phases"][phase]
        if int(entry["attempts"]) != prior["attempts"]:
            raise ResumableGeneratedGoalError(
                "completed generated-goal phase was replayed during resume"
            )
        if entry["result_digest"] != prior["result_digest"]:
            raise ResumableGeneratedGoalError(
                "completed generated-goal phase digest changed during resume"
            )
        if entry["metadata"] != prior["metadata"]:
            raise ResumableGeneratedGoalError(
                "completed generated-goal phase metadata changed during resume"
            )

    complete = tuple(state["completed_phases"]) == _PHASE_ORDER
    state["status"] = "completed" if complete else "in_progress"
    state["passed"] = complete
    state["active_phase"] = None
    state["completed_count"] = len(state["completed_phases"])
    state["resume_skipped_completed"] = len(completed_before)
    state["last_advance_count"] = advanced
    state["last_reconciled_count"] = reconciled
    state["selection_intent_persisted_before_learning"] = state["phases"]["select"]["status"] == "completed"
    state["post_commit_receipt_enabled"] = True
    state["post_commit_pre_journal_crash_recoverable_without_relearning"] = True
    state["receipt_fail_closed_on_tamper_or_state_mismatch"] = True
    state["digest"] = _digest({key: value for key, value in state.items() if key != "digest"})
    _atomic_json(path, state)
    if complete:
        _atomic_json(_evidence_path(root, campaign_id), state)
    return state
