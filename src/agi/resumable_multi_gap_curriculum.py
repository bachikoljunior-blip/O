from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.durable_state_rehydration import _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest
from agi.multi_gap_autonomous_curriculum import (
    MultiGapAutonomousCurriculumError,
    _all_candidate_ids,
    _learn_and_commit_gap,
    _numeric_to_string_spec,
    _square_spec,
    _trial_snapshot,
)
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.transactional_multisession_commit import run_transactional_multisession_commit
from agi.verified_macro_library_discovery import (
    discover_verified_acquired_program_ids,
    synthesize_from_verified_macro_library,
)
from agi.verified_macro_synthesis import _load_verified_macro_items


class ResumableMultiGapCurriculumError(RuntimeError):
    pass


_SCHEMA_VERSION = 1
_PHASE_ORDER = (
    "transactional_base",
    "square_gap",
    "string_gap",
    "final_composition",
)


def _state_path(root: Path, campaign_id: str) -> Path:
    return (
        root
        / ".continual"
        / "system"
        / "multi-gap-curricula"
        / f"{campaign_id}.json"
    )


def _evidence_path(root: Path, campaign_id: str) -> Path:
    return (
        root
        / ".continual"
        / "evidence"
        / "resumable-multi-gap-curriculum"
        / f"{campaign_id}.json"
    )


def _safe_campaign_id(campaign_id: str) -> bool:
    return bool(campaign_id) and all(
        ch in "abcdefghijklmnopqrstuvwxyz0123456789-_." for ch in campaign_id
    )


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
                "result_digest": None,
                "metadata": {},
            }
            for phase in _PHASE_ORDER
        },
        "completed_phases": [],
        "active_phase": None,
        "status": "in_progress",
        "passed": False,
        "claim_boundary": (
            "Internal deterministic long-horizon continual-learning evidence only. Phase-level "
            "checkpoint/resume, bounded acquisition, protected regression, transactional retention, "
            "fresh-state transfer, and autonomous composition remain repository-authored and do not "
            "establish independent production AGI evidence."
        ),
    }


def _load_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResumableMultiGapCurriculumError("curriculum state must be an object")
    return value


def _validate_state(
    state: Mapping[str, Any],
    *,
    campaign_id: str,
    seed_commitment: str,
) -> None:
    if state.get("schema_version") != _SCHEMA_VERSION:
        raise ResumableMultiGapCurriculumError("curriculum schema mismatch")
    if state.get("campaign_id") != campaign_id:
        raise ResumableMultiGapCurriculumError("curriculum identity mismatch")
    if state.get("seed_commitment") != seed_commitment:
        raise ResumableMultiGapCurriculumError(
            "curriculum seed commitment changed across resume"
        )
    if state.get("phase_order") != list(_PHASE_ORDER):
        raise ResumableMultiGapCurriculumError("curriculum phase order changed")
    phases = state.get("phases")
    if not isinstance(phases, dict):
        raise ResumableMultiGapCurriculumError("curriculum phases must be an object")
    for phase in _PHASE_ORDER:
        entry = phases.get(phase)
        if not isinstance(entry, dict) or entry.get("status") not in {
            "pending",
            "running",
            "completed",
        }:
            raise ResumableMultiGapCurriculumError(
                f"invalid resumable curriculum phase state: {phase}"
            )


def _run_transactional_base(root: Path, seed: str) -> dict[str, Any]:
    report = run_transactional_multisession_commit(
        root,
        f"{seed}:resumable-transactional-base",
    )
    if not report.get("passed"):
        raise ResumableMultiGapCurriculumError("transactional base did not pass")
    return {
        "passed": True,
        "result_digest": str(report["digest"]),
        "metadata": {
            "base_digest": str(report["digest"]),
            "transactional_commit_count": int(report["transactional_commit_count"]),
        },
    }


def _run_square_gap(root: Path, seed: str) -> dict[str, Any]:
    gap_examples = (
        ProgramExample(14, 196),
        ProgramExample(-10, 100),
        ProgramExample(9, 81),
    )
    result = _learn_and_commit_gap(
        root,
        seed=f"{seed}:resumable-square-gap",
        spec=_square_spec(),
        gap_examples=gap_examples,
        max_depth=2,
    )
    if not result.get("gap_failed_closed_before_learning"):
        raise ResumableMultiGapCurriculumError(
            "square gap did not fail closed before learning"
        )
    if not result.get("negative_evidence_retained"):
        raise ResumableMultiGapCurriculumError(
            "square gap did not retain negative evidence"
        )
    return {
        "passed": True,
        "result_digest": _digest(result),
        "metadata": {
            "candidate_id": str(result["candidate_id"]),
            "scope": str(result["scope"]),
            "gap_failed_closed_before_learning": True,
            "negative_evidence_retained": True,
        },
    }


def _run_string_gap(root: Path, seed: str) -> dict[str, Any]:
    gap_examples = (
        ProgramExample(17, "17"),
        ProgramExample(-12, "-12"),
        ProgramExample(4, "4"),
    )
    result = _learn_and_commit_gap(
        root,
        seed=f"{seed}:resumable-string-gap",
        spec=_numeric_to_string_spec(),
        gap_examples=gap_examples,
        max_depth=3,
    )
    if not result.get("gap_failed_closed_before_learning"):
        raise ResumableMultiGapCurriculumError(
            "string gap did not fail closed before learning"
        )
    if not result.get("negative_evidence_retained"):
        raise ResumableMultiGapCurriculumError(
            "string gap did not retain negative evidence"
        )
    return {
        "passed": True,
        "result_digest": _digest(result),
        "metadata": {
            "candidate_id": str(result["candidate_id"]),
            "scope": str(result["scope"]),
            "gap_failed_closed_before_learning": True,
            "negative_evidence_retained": True,
        },
    }


def _run_final_composition(
    root: Path,
    seed: str,
    *,
    square_id: str,
    string_id: str,
    campaign_id: str,
) -> dict[str, Any]:
    all_ids = _all_candidate_ids(root)
    if square_id not in all_ids or string_id not in all_ids:
        raise ResumableMultiGapCurriculumError(
            "resumed curriculum lost a previously completed acquisition"
        )
    tree_before = _tree_snapshot(root, all_ids)
    trials_before = _trial_snapshot(root, all_ids)

    library_ids = discover_verified_acquired_program_ids(root, max_candidates=64)
    negation_ids = [
        candidate_id
        for candidate_id in library_ids
        if candidate_id.startswith("hetero-numeric-neg-session3-")
    ]
    if len(negation_ids) != 1:
        raise ResumableMultiGapCurriculumError(
            "expected exactly one retained numeric-negation Candidate"
        )
    negation_id = negation_ids[0]
    for required in (square_id, negation_id, string_id):
        if required not in library_ids:
            raise ResumableMultiGapCurriculumError(
                f"verified library omitted completed curriculum skill: {required}"
            )

    with tempfile.TemporaryDirectory(
        prefix="agi-resumable-multigap-final-"
    ) as temporary:
        fresh_root = Path(temporary).resolve()
        _copy_persistent_state(root, fresh_root)
        prior_runs_copied = (fresh_root / ".continual" / "runs").exists()
        prior_episodes_copied = (fresh_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (fresh_root / ".continual" / "evidence").exists()
        if _tree_snapshot(fresh_root, all_ids) != tree_before:
            raise ResumableMultiGapCurriculumError(
                "fresh final restart changed accumulated Candidate bytes"
            )
        if _trial_snapshot(fresh_root, all_ids) != trials_before:
            raise ResumableMultiGapCurriculumError(
                "fresh final restart changed accumulated trial ledgers"
            )

        planning_examples = (
            ProgramExample(7, "-49"),
            ProgramExample(-5, "-25"),
            ProgramExample(11, "-121"),
        )
        synthesized = synthesize_from_verified_macro_library(
            fresh_root,
            input_domain="numeric",
            output_domain="string",
            examples=planning_examples,
            max_depth=3,
            max_candidates=64,
        )
        selected_ids = [str(value) for value in synthesized["selected_candidate_ids"]]
        expected_ids = [square_id, negation_id, string_id]
        if synthesized.get("candidate_ids_supplied_by_caller") is not False:
            raise ResumableMultiGapCurriculumError(
                "resumed final composition received caller Candidate IDs"
            )
        if int(synthesized["shortest_type_chain_depth"]) != 1:
            raise ResumableMultiGapCurriculumError(
                "resumed final task lacks shorter type-correct distractors"
            )
        if int(synthesized["selected_chain_depth"]) != 3:
            raise ResumableMultiGapCurriculumError(
                "resumed final task did not require three learned stages"
            )
        if selected_ids != expected_ids:
            raise ResumableMultiGapCurriculumError(
                "resumed final composition did not combine both new skills with retained negation"
            )

        selected_chain = _load_verified_macro_items(fresh_root, selected_ids)
        commitment = _digest(
            {
                "campaign_id": campaign_id,
                "seed_commitment": _digest(seed),
                "square_id": square_id,
                "negation_id": negation_id,
                "string_id": string_id,
                "selection_digest": synthesized["digest"],
                "phase": "resumed-post-selection-challenge-v1",
            }
        )
        challenge_values = (
            101 + int(commitment[:4], 16) % 80,
            -(181 + int(commitment[4:8], 16) % 80),
        )
        if len(set(challenge_values)) != 2:
            raise ResumableMultiGapCurriculumError(
                "resumed challenge generator produced duplicate inputs"
            )

        compiled_program = synthesized["compiled_program"]
        fresh_runs: list[str] = []
        stage_count = 0
        for value in challenge_values:
            expected = str(-(value * value))
            if execute_program(compiled_program, value) != expected:
                raise ResumableMultiGapCurriculumError(
                    "compiled resumed curriculum failed post-selection challenge"
                )
            output, run_id, refs = _execute_chain(fresh_root, selected_chain, value)
            if output != expected:
                raise ResumableMultiGapCurriculumError(
                    "fresh Engine resumed curriculum failed challenge"
                )
            fresh_runs.append(run_id)
            stage_count += len(refs)
        if len(set(fresh_runs)) != len(fresh_runs):
            raise ResumableMultiGapCurriculumError(
                "resumed curriculum reused a supposedly fresh Engine run"
            )
        if stage_count != 6:
            raise ResumableMultiGapCurriculumError(
                "two three-stage resumed challenges did not execute six stages"
            )

        fresh_state_unchanged = _tree_snapshot(fresh_root, all_ids) == tree_before
        fresh_trials_unchanged = _trial_snapshot(fresh_root, all_ids) == trials_before
        if not fresh_state_unchanged or not fresh_trials_unchanged:
            raise ResumableMultiGapCurriculumError(
                "resumed final composition changed fresh durable learning state"
            )

    source_state_unchanged = _tree_snapshot(root, all_ids) == tree_before
    source_trials_unchanged = _trial_snapshot(root, all_ids) == trials_before
    if not source_state_unchanged or not source_trials_unchanged:
        raise ResumableMultiGapCurriculumError(
            "resumed final composition mutated durable source state"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "resumable-multi-gap-autonomous-curriculum-v1",
        "campaign_id": campaign_id,
        "caller_supplied_candidate_ids": False,
        "square_candidate_id": square_id,
        "retained_negation_candidate_id": negation_id,
        "string_candidate_id": string_id,
        "selected_candidate_ids": selected_ids,
        "shortest_type_chain_depth": int(synthesized["shortest_type_chain_depth"]),
        "selected_chain_depth": int(synthesized["selected_chain_depth"]),
        "post_selection_challenge_commitment": commitment,
        "challenge_case_count": len(challenge_values),
        "fresh_engine_runs": fresh_runs,
        "fresh_engine_runs_unique": len(set(fresh_runs)) == len(fresh_runs),
        "stage_invocation_count": stage_count,
        "fresh_state_unchanged": fresh_state_unchanged,
        "fresh_trials_unchanged": fresh_trials_unchanged,
        "source_state_unchanged": source_state_unchanged,
        "source_trials_unchanged": source_trials_unchanged,
        "prior_runs_copied": prior_runs_copied,
        "prior_episodes_copied": prior_episodes_copied,
        "prior_evidence_copied": prior_evidence_copied,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded phase-resumable curriculum evidence only. Completed acquisition phases "
            "survived explicit checkpoints and were not replayed on later invocations; a later fresh "
            "state-only execution autonomously composed both retained gains with an older skill. The "
            "tasks, curriculum, synthesis grammar, regression, checkpoints, search, scoring, and evaluator "
            "remain repository-authored and therefore do not establish open-domain autonomy or AGI."
        ),
    }
    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(_evidence_path(root, campaign_id), report)
    return report


def _run_phase(
    root: Path,
    state: Mapping[str, Any],
    phase: str,
    *,
    seed: str,
    campaign_id: str,
) -> dict[str, Any]:
    if phase == "transactional_base":
        return _run_transactional_base(root, seed)
    if phase == "square_gap":
        return _run_square_gap(root, seed)
    if phase == "string_gap":
        return _run_string_gap(root, seed)
    if phase == "final_composition":
        phases = state["phases"]
        square_id = str(phases["square_gap"]["metadata"]["candidate_id"])
        string_id = str(phases["string_gap"]["metadata"]["candidate_id"])
        report = _run_final_composition(
            root,
            seed,
            square_id=square_id,
            string_id=string_id,
            campaign_id=campaign_id,
        )
        return {
            "passed": True,
            "result_digest": str(report["digest"]),
            "metadata": {
                "evidence_path": _evidence_path(root, campaign_id)
                .relative_to(root)
                .as_posix(),
                "selected_candidate_ids": list(report["selected_candidate_ids"]),
                "challenge_case_count": int(report["challenge_case_count"]),
            },
        }
    raise ResumableMultiGapCurriculumError(f"unknown curriculum phase: {phase}")


def run_resumable_multi_gap_curriculum(
    root: Path,
    campaign_id: str,
    *,
    seed: str,
    max_new_phases: int | None = None,
) -> dict[str, Any]:
    """Advance a multi-session learning curriculum across explicit durable checkpoints.

    Each invocation reconstructs progress from the persisted phase journal. Completed phases are skipped
    without repeating their learning or transactional Candidate commits; later phases consume only durable
    Candidate/system state plus the committed phase metadata. ``max_new_phases`` is an execution budget,
    not a success condition, so a caller may deliberately checkpoint after one phase and resume later.

    The guarantee is phase-level resumability: a process loss between completed phases is recoverable
    without replaying those phases. A phase left marked ``running`` is retried rather than silently treated
    as complete, preserving fail-closed semantics instead of claiming exactly-once execution.
    """

    if not _safe_campaign_id(campaign_id):
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
    immutable_digests = {
        phase: state["phases"][phase]["result_digest"]
        for phase in completed_before
    }
    immutable_attempts = {
        phase: int(state["phases"][phase]["attempts"])
        for phase in completed_before
    }

    advanced = 0
    for phase in _PHASE_ORDER:
        entry = state["phases"][phase]
        if entry["status"] == "completed":
            continue
        if max_new_phases is not None and advanced >= max_new_phases:
            break

        entry["status"] = "running"
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        state["active_phase"] = phase
        _atomic_json(path, state)

        result = _run_phase(
            root,
            state,
            phase,
            seed=seed,
            campaign_id=campaign_id,
        )
        if not result.get("passed"):
            raise ResumableMultiGapCurriculumError(
                f"resumable curriculum phase failed: {phase}"
            )
        entry["status"] = "completed"
        entry["result_digest"] = str(result["result_digest"])
        entry["metadata"] = dict(result.get("metadata") or {})
        if phase not in state["completed_phases"]:
            state["completed_phases"].append(phase)
        state["active_phase"] = None
        advanced += 1
        _atomic_json(path, state)

    for phase, digest in immutable_digests.items():
        entry = state["phases"][phase]
        if entry["result_digest"] != digest:
            raise ResumableMultiGapCurriculumError(
                "completed phase evidence changed during resume"
            )
        if int(entry["attempts"]) != immutable_attempts[phase]:
            raise ResumableMultiGapCurriculumError(
                "completed phase was unexpectedly replayed during resume"
            )

    complete = tuple(state["completed_phases"]) == _PHASE_ORDER
    state["status"] = "completed" if complete else "in_progress"
    state["passed"] = complete
    state["active_phase"] = None
    state["completed_count"] = len(state["completed_phases"])
    state["resume_skipped_completed"] = len(completed_before)
    state["last_advance_count"] = advanced
    state["phase_level_resumability"] = True
    state["running_phase_retries_fail_closed"] = True
    state["digest"] = _digest(
        {key: value for key, value in state.items() if key != "digest"}
    )
    _atomic_json(path, state)
    return state
