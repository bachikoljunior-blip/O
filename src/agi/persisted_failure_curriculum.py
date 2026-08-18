from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program
from agi.adaptive_depth_composition import _challenge_inputs, synthesize_shallowest_verified_route
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.durable_state_rehydration import _tree_snapshot
from agi.dynamic_skillgraph_growth_campaign import run_dynamic_skillgraph_growth_campaign
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.seed_committed_gap_acquisition import (
    _library_attempt,
    _precommit_gap_schedule,
    _program_expected,
    run_seed_committed_gap_acquisition,
)
from agi.transactional_multisession_commit import _commit_verified_candidate
from agi.verified_macro_synthesis import _load_verified_macro_items


class PersistedFailureCurriculumError(RuntimeError):
    pass


def _target(raw: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(raw)
    program = item.get("program")
    if not isinstance(program, Mapping):
        raise PersistedFailureCurriculumError("generated target lacks a program descriptor")
    item["input_domain"] = str(program["input_domain"])
    item["output_domain"] = str(program["output_domain"])
    return item


def _target_by_index(schedule: Mapping[str, Any], target_index: int) -> dict[str, Any]:
    targets = schedule.get("target_schedule")
    if not isinstance(targets, Sequence):
        raise PersistedFailureCurriculumError("reconstructed schedule lacks target_schedule")
    for raw in targets:
        if isinstance(raw, Mapping) and int(raw["target_index"]) == target_index:
            return _target(raw)
    raise PersistedFailureCurriculumError(
        f"persisted target index was not reconstructed: {target_index}"
    )


def _examples(target: Mapping[str, Any]) -> tuple[ProgramExample, ...]:
    support = target.get("support_examples")
    if not isinstance(support, Sequence):
        raise PersistedFailureCurriculumError("generated target lacks support_examples")
    return tuple(ProgramExample(item["input"], item["output"]) for item in support)


def _assert_unchanged(
    root: Path,
    candidate_ids: Sequence[str],
    tree_before: Mapping[str, str],
    trials_before: Mapping[str, Mapping[str, str]],
    *,
    label: str,
) -> None:
    if _tree_snapshot(root, candidate_ids) != dict(tree_before):
        raise PersistedFailureCurriculumError(f"{label} changed prior Candidate bytes")
    if _trial_snapshot(root, candidate_ids) != dict(trials_before):
        raise PersistedFailureCurriculumError(f"{label} changed prior Candidate trials")


def _solve_and_replay(
    root: Path,
    *,
    target: Mapping[str, Any],
    commitment: str,
    required_candidate_id: str,
) -> dict[str, Any]:
    solved = synthesize_shallowest_verified_route(
        root,
        input_domain=str(target["input_domain"]),
        output_domain=str(target["output_domain"]),
        examples=_examples(target),
        max_depth=4,
        max_candidates=64,
        max_search_nodes=8192,
        max_behavior_evaluations=4096,
    )
    if solved.get("candidate_ids_supplied_by_caller") is not False:
        raise PersistedFailureCurriculumError("behavior-only resolver used caller Candidate IDs")
    selected_ids = [str(value) for value in solved["selected_candidate_ids"]]
    if required_candidate_id not in selected_ids:
        raise PersistedFailureCurriculumError(
            "behavior-only resolver did not rediscover the evidence-selected learned Candidate"
        )
    selected_items = tuple(_load_verified_macro_items(root, selected_ids))
    challenge_inputs = tuple(_challenge_inputs(str(target["input_domain"]), commitment))
    expected = tuple(_program_expected(target["program"], value) for value in challenge_inputs)
    run_ids: list[str] = []
    outputs: list[dict[str, Any]] = []
    for value, expected_value in zip(challenge_inputs, expected, strict=True):
        compiled_output = execute_program(solved["compiled_program"], value)
        runtime_output, run_id, refs = _execute_chain(root, selected_items, value)
        if compiled_output != expected_value or runtime_output != expected_value:
            raise PersistedFailureCurriculumError(
                "fresh evidence-selected transfer failed its held-back challenge"
            )
        if len(refs) != int(solved["selected_chain_depth"]):
            raise PersistedFailureCurriculumError(
                "fresh evidence-selected transfer executed the wrong stage count"
            )
        run_ids.append(run_id)
        outputs.append(
            {
                "input_sha256": _digest(value),
                "expected_sha256": _digest(expected_value),
                "output_sha256": _digest(runtime_output),
                "runtime_refs": refs,
            }
        )
    if len(set(run_ids)) != len(run_ids):
        raise PersistedFailureCurriculumError("evidence-selected transfer reused an Engine run")
    return {
        "solver_candidate_ids_supplied_by_caller": False,
        "solver_selected_candidate_ids": selected_ids,
        "solver_selected_chain_depth": int(solved["selected_chain_depth"]),
        "heldback_commitment": commitment,
        "fresh_engine_run_ids": run_ids,
        "fresh_engine_runs_unique": True,
        "heldback_outputs": outputs,
    }


def run_persisted_failure_curriculum(root: Path, seed: str) -> dict[str, Any]:
    """Learn the next bounded objective from an earlier durable fail-closed control."""
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    bootstrap = run_dynamic_skillgraph_growth_campaign(root, f"{seed}:bootstrap")
    if not bootstrap.get("passed"):
        raise PersistedFailureCurriculumError("dynamic skill-graph bootstrap did not pass")
    bootstrap_ids = _all_candidate_ids(root)
    bootstrap_tree = _tree_snapshot(root, bootstrap_ids)
    bootstrap_trials = _trial_snapshot(root, bootstrap_ids)

    evidence_seed = f"{seed}:evidence-source"
    first = run_seed_committed_gap_acquisition(root, evidence_seed, max_target_attempts=24)
    if not first.get("passed") or not first.get("control_gap_remained_failed_closed"):
        raise PersistedFailureCurriculumError(
            "source acquisition did not persist an untouched fail-closed control"
        )
    _assert_unchanged(
        root, bootstrap_ids, bootstrap_tree, bootstrap_trials, label="source acquisition"
    )

    evidence_path = (
        root
        / ".continual"
        / "evidence"
        / "seed-committed-gap-acquisition"
        / f"gap-acquisition-{_digest(evidence_seed)[:16]}.json"
    )
    if not evidence_path.is_file():
        raise PersistedFailureCurriculumError("source acquisition evidence was not persisted")
    persisted = json.loads(evidence_path.read_text(encoding="utf-8"))
    if persisted.get("digest") != first.get("digest"):
        raise PersistedFailureCurriculumError("persisted source evidence digest does not match")

    schedule = _precommit_gap_schedule(root, evidence_seed, max_target_attempts=24)
    if schedule["schedule_commitment"] != persisted["target_schedule_commitment"]:
        raise PersistedFailureCurriculumError(
            "persisted evidence does not reconstruct the original target schedule"
        )
    first_target = _target_by_index(schedule, int(persisted["acquisition_target_index"]))
    control_target = _target_by_index(schedule, int(persisted["control_target_index"]))
    if control_target["behavior_fingerprint"] != persisted["control_behavior_fingerprint"]:
        raise PersistedFailureCurriculumError("persisted control fingerprint does not match")
    if _library_attempt(root, _examples(control_target)).get("supported"):
        raise PersistedFailureCurriculumError(
            "persisted control gap became supported before evidence-driven learning"
        )

    first_candidate_id = str(first["candidate_id"])
    before_ids = _all_candidate_ids(root)
    if first_candidate_id not in before_ids:
        raise PersistedFailureCurriculumError("first learned Candidate was not retained")
    before_tree = _tree_snapshot(root, before_ids)
    before_trials = _trial_snapshot(root, before_ids)

    untouched: dict[str, Any] | None = None
    for raw in schedule["target_schedule"]:
        if int(raw["target_index"]) in {
            int(persisted["acquisition_target_index"]),
            int(persisted["control_target_index"]),
        }:
            continue
        candidate = _target(raw)
        if not _library_attempt(root, _examples(candidate)).get("supported"):
            untouched = candidate
            break
    if untouched is None:
        raise PersistedFailureCurriculumError("no third fail-closed schedule control remained")

    next_commitment = _digest(
        {
            "persisted_evidence_digest": persisted["digest"],
            "schedule_commitment": persisted["target_schedule_commitment"],
            "target_index": int(persisted["control_target_index"]),
            "behavior_fingerprint": persisted["control_behavior_fingerprint"],
            "phase": "persisted-failure-next-objective-v1",
        }
    )
    challenges = tuple(_challenge_inputs(str(control_target["input_domain"]), next_commitment))
    expected = tuple(_program_expected(control_target["program"], value) for value in challenges)
    spec = {
        "name": f"persisted-control-{str(control_target['behavior_fingerprint'])[:12]}",
        "input_domain": str(control_target["input_domain"]),
        "output_domain": str(control_target["output_domain"]),
        "examples": _examples(control_target),
        "probe": challenges[0],
        "expected": expected[0],
        "challenge": challenges[1],
        "challenge_expected": expected[1],
        "max_nodes": int(control_target["program_nodes"]),
    }

    with tempfile.TemporaryDirectory(prefix="agi-persisted-failure-learn-") as temporary:
        learner = Path(temporary).resolve()
        _copy_persistent_state(root, learner)
        _assert_unchanged(
            learner, before_ids, before_tree, before_trials, label="learner reconstruction"
        )
        if _library_attempt(learner, _examples(control_target)).get("supported"):
            raise PersistedFailureCurriculumError("target became supported before learning")
        learned = _promote_task(
            learner, seed=f"{seed}:persisted-control-learning", spec=spec
        )
        candidate_id = str(learned["candidate_id"])
        if candidate_id in before_ids or not learned.get("negative_evidence_retained"):
            raise PersistedFailureCurriculumError(
                "evidence-driven Candidate identity/evidence invariant failed"
            )
        for value, expected_value in zip(challenges, expected, strict=True):
            if execute_program(learned["program"], value) != expected_value:
                raise PersistedFailureCurriculumError(
                    "evidence-driven Candidate failed precommitted challenge"
                )
        _assert_unchanged(
            learner, before_ids, before_tree, before_trials, label="evidence-driven learning"
        )
        commit = _commit_verified_candidate(
            learner, root, candidate_id=candidate_id, scope=str(learned["scope"])
        )

    _assert_unchanged(
        root, before_ids, before_tree, before_trials, label="transactional commit"
    )
    after_ids = _all_candidate_ids(root)
    after_tree = _tree_snapshot(root, after_ids)
    after_trials = _trial_snapshot(root, after_ids)
    if candidate_id not in after_ids or not after_trials.get(candidate_id):
        raise PersistedFailureCurriculumError("new Candidate lacks durable trial evidence")

    with tempfile.TemporaryDirectory(prefix="agi-persisted-failure-resolver-") as temporary:
        resolver = Path(temporary).resolve()
        _copy_persistent_state(root, resolver)
        transfer = _solve_and_replay(
            resolver,
            target=control_target,
            commitment=next_commitment,
            required_candidate_id=candidate_id,
        )
        first_commitment = _digest(
            {
                "schedule_commitment": persisted["target_schedule_commitment"],
                "acquisition_fingerprint": persisted["acquisition_behavior_fingerprint"],
                "control_fingerprint": persisted["control_behavior_fingerprint"],
                "phase": "seed-grammar-gap-choice-v1",
            }
        )
        first_retention = _solve_and_replay(
            resolver,
            target=first_target,
            commitment=first_commitment,
            required_candidate_id=first_candidate_id,
        )
        if _library_attempt(resolver, _examples(untouched)).get("supported"):
            raise PersistedFailureCurriculumError(
                "evidence-driven learning generalized into untouched control"
            )
        _assert_unchanged(
            resolver, after_ids, after_tree, after_trials, label="fresh resolver"
        )
    _assert_unchanged(root, after_ids, after_tree, after_trials, label="durable source")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "persisted-failure-curriculum-v1",
        "seed": seed,
        "target_selection_source": "persisted_fail_closed_control_evidence",
        "fresh_target_enumeration_used_for_next_objective": False,
        "source_evidence_path": evidence_path.relative_to(root).as_posix(),
        "source_evidence_digest": str(persisted["digest"]),
        "source_schedule_commitment_reconstructed": True,
        "persisted_control_target_index": int(persisted["control_target_index"]),
        "persisted_control_behavior_fingerprint": str(persisted["control_behavior_fingerprint"]),
        "persisted_control_gap_still_failed_closed_before_learning": True,
        "next_objective_commitment": next_commitment,
        "next_objective_precommitted_before_learning": True,
        "new_candidate_id": candidate_id,
        "new_candidate_negative_evidence_retained": True,
        "transactional_commit": commit,
        "prior_candidate_state_unchanged": True,
        "prior_trial_state_unchanged": True,
        "fresh_behavior_only_transfer": transfer,
        "solver_candidate_ids_supplied_by_caller": bool(
            transfer["solver_candidate_ids_supplied_by_caller"]
        ),
        "evidence_selected_candidate_rediscovered": candidate_id
        in transfer["solver_selected_candidate_ids"],
        "fresh_transfer_runs_unique": bool(transfer["fresh_engine_runs_unique"]),
        "first_learned_candidate_id": first_candidate_id,
        "first_learned_behavior_retention": first_retention,
        "first_learned_behavior_retained_after_second_learning": first_candidate_id
        in first_retention["solver_selected_candidate_ids"],
        "untouched_control_target_index": int(untouched["target_index"]),
        "untouched_control_behavior_fingerprint": str(untouched["behavior_fingerprint"]),
        "untouched_control_gap_remained_failed_closed": True,
        "source_candidate_state_unchanged": True,
        "source_trial_state_unchanged": True,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded evidence-driven curriculum evidence only. The next objective comes from "
            "a durable fail-closed control and the exact committed source schedule is reconstructed. "
            "Fresh behavior-only transfer and retention are required while an untouched control remains "
            "fail-closed. The generator, grammar, learner, oracle, evaluator, and bounds remain internal; "
            "this is not open-domain autonomous task invention, independent production evidence, or AGI."
        ),
    }
    if not all(
        (
            report["source_schedule_commitment_reconstructed"],
            report["persisted_control_gap_still_failed_closed_before_learning"],
            report["next_objective_precommitted_before_learning"],
            report["new_candidate_negative_evidence_retained"],
            report["prior_candidate_state_unchanged"],
            report["prior_trial_state_unchanged"],
            report["solver_candidate_ids_supplied_by_caller"] is False,
            report["evidence_selected_candidate_rediscovered"],
            report["fresh_transfer_runs_unique"],
            report["first_learned_behavior_retained_after_second_learning"],
            report["untouched_control_gap_remained_failed_closed"],
            report["source_candidate_state_unchanged"],
            report["source_trial_state_unchanged"],
        )
    ):
        raise PersistedFailureCurriculumError("persisted-failure aggregate invariant failed")
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "persisted-failure-curriculum"
        / f"curriculum-{_digest(seed)[:16]}.json",
        report,
    )
    return report
