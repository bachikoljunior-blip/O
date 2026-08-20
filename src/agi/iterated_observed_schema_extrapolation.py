from __future__ import annotations

import copy
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import execute_program
from agi.crossdomain_failure_derived_target import _attempt, _examples, _solve_and_replay
from agi.durable_state_rehydration import _tree_snapshot
from agi.evidence_frontier_expansion import _challenge_inputs
from agi.evidence_frontier_objective_selection import _assert_prior_unchanged
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.observed_schema_extrapolation import (
    ObservedSchemaExtrapolationError,
    _load_json,
    _precommit_observed_schema_schedule,
    _reconstruct_observed_targets,
    _target_from_program,
    run_observed_schema_extrapolation,
)
from agi.transactional_multisession_commit import _commit_verified_candidate
from agi.verified_macro_synthesis import _load_verified_macro_items


class IteratedObservedSchemaExtrapolationError(RuntimeError):
    pass


def _recursive_report(root: Path, recursive_seed: str) -> dict[str, Any]:
    path = (
        root
        / ".continual"
        / "evidence"
        / "recursive-evidence-frontier-growth"
        / f"recursive-evidence-frontier-growth-{_digest(recursive_seed)[:16]}.json"
    )
    if not path.is_file():
        raise IteratedObservedSchemaExtrapolationError(
            "first schema stage did not retain its recursive prerequisite evidence"
        )
    report = _load_json(path)
    if not report.get("passed") or str(report.get("seed")) != recursive_seed:
        raise IteratedObservedSchemaExtrapolationError(
            "retained recursive prerequisite is not reusable"
        )
    return report


def _selected_first_target(
    observed_targets: Sequence[Mapping[str, Any]],
    *,
    first_report: Mapping[str, Any],
) -> dict[str, Any]:
    schedule = _precommit_observed_schema_schedule(
        observed_targets,
        history_digest=str(first_report["history_digest"]),
    )
    selected_signature = str(first_report["selected_semantic_signature"])
    matches = [
        copy.deepcopy(target)
        for target in schedule["target_schedule"]
        if str(target["semantic_signature"]) == selected_signature
    ]
    if len(matches) != 1:
        raise IteratedObservedSchemaExtrapolationError(
            "first schema target cannot be reconstructed uniquely from retained semantics"
        )
    if str(schedule["schedule_commitment"]) != str(first_report["schedule_commitment"]):
        raise IteratedObservedSchemaExtrapolationError(
            "first schema schedule commitment changed during reconstruction"
        )
    return matches[0]


def _learned_target_from_candidate(
    root: Path,
    *,
    candidate_id: str,
    selected_target: Mapping[str, Any],
) -> dict[str, Any]:
    items = _load_verified_macro_items(root, [candidate_id])
    if len(items) != 1:
        raise IteratedObservedSchemaExtrapolationError(
            "first schema Candidate could not be loaded uniquely"
        )
    program = items[0]["program"]
    support_inputs = tuple(
        copy.deepcopy(item["input"]) for item in selected_target["support_examples"]
    )
    learned_target = _target_from_program(program, support_inputs)
    if str(learned_target["semantic_signature"]) != str(selected_target["semantic_signature"]):
        raise IteratedObservedSchemaExtrapolationError(
            "verified first schema Candidate changed committed support behavior"
        )
    learned_target["source_candidate_id"] = candidate_id
    learned_target["first_stage_lineage"] = copy.deepcopy(selected_target.get("lineage"))
    return learned_target


def _precommit_second_schema_schedule(
    observed_targets: Sequence[Mapping[str, Any]],
    *,
    first_learned_target: Mapping[str, Any],
    first_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Require the next proposals to depend on the newly learned first-stage program.

    The underlying schema extrapolator remains unchanged. This wrapper removes proposals that could
    have been produced before the first schema Candidate existed and retains only proposals whose
    observed-delta lineage directly references the verified learned program. The filtered schedule is
    committed before support checks, so passing cannot be obtained by selecting a target after seeing
    which proposals the current library supports.
    """
    first_program_digest = _digest(first_learned_target["program"])
    history_digest = _digest(
        {
            "first_report_digest": first_report.get("digest"),
            "first_schedule_commitment": first_report.get("schedule_commitment"),
            "first_candidate_id": first_report.get("new_candidate_id"),
            "first_learned_program_digest": first_program_digest,
            "phase": "iterated-observed-schema-history-v1",
        }
    )
    raw = _precommit_observed_schema_schedule(
        observed_targets,
        history_digest=history_digest,
    )

    prior_signatures = {
        str(target["semantic_signature"])
        for target in _precommit_observed_schema_schedule(
            observed_targets[:-1],
            history_digest=str(first_report["history_digest"]),
        )["target_schedule"]
    }
    selected_signature = str(first_report["selected_semantic_signature"])
    prior_signatures.add(selected_signature)

    filtered: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()
    for candidate in raw["target_schedule"]:
        lineage = candidate.get("lineage")
        if not isinstance(lineage, Mapping):
            continue
        lineage_digests = {
            str(lineage.get("left_program_digest")),
            str(lineage.get("right_program_digest")),
        }
        signature = str(candidate["semantic_signature"])
        if first_program_digest not in lineage_digests:
            continue
        if signature in prior_signatures or signature in seen_signatures:
            continue
        item = copy.deepcopy(candidate)
        item["target_index"] = len(filtered)
        filtered.append(item)
        seen_signatures.add(signature)

    if len(filtered) < 2:
        raise IteratedObservedSchemaExtrapolationError(
            "newly learned schema program exposed fewer than two lineage-dependent novel behaviors"
        )
    commitment = _digest(
        {
            "history_digest": history_digest,
            "first_learned_program_digest": first_program_digest,
            "targets": filtered,
            "phase": "iterated-observed-schema-precommit-v1",
        }
    )
    return {
        "history_digest": history_digest,
        "first_learned_program_digest": first_program_digest,
        "target_schedule": filtered,
        "schedule_commitment": commitment,
    }


def run_iterated_observed_schema_extrapolation(root: Path, seed: str) -> dict[str, Any]:
    """Learn a second schema-extrapolated behavior from the first learned schema program.

    The first stage must already infer and learn a new behavior by extrapolating numeric-literal deltas
    observed between same-shape learned programs. This stage then loads the *verified learned program*
    from Candidate state, not the target generator's intended program, and admits it as a new observation.
    Only second-stage proposals whose lineage directly includes that learned program are eligible. One
    precommitted fail-closed behavior is learned, another remains fail-closed, and all seven accumulated
    capabilities must replay from fresh state without caller Candidate IDs.

    This remains bounded repository-authored schema induction and internal development evidence. It is
    not open-domain objective invention, independent production evidence, or an AGI claim.
    """
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    first_seed = f"{seed}:first-schema"
    first = run_observed_schema_extrapolation(root, first_seed)
    if not first.get("passed") or not first.get("all_six_capabilities_rediscovered"):
        raise IteratedObservedSchemaExtrapolationError(
            "first observed-schema stage did not retain six capabilities"
        )

    recursive_seed = f"{first_seed}:recursive"
    recursive = _recursive_report(root, recursive_seed)
    if str(recursive.get("digest")) != str(first.get("recursive_prerequisite_digest")):
        raise IteratedObservedSchemaExtrapolationError(
            "first schema stage no longer binds to its retained recursive prerequisite"
        )
    observed = _reconstruct_observed_targets(
        root,
        recursive_seed=recursive_seed,
        recursive_report=recursive,
    )
    first_selected = _selected_first_target(
        observed["observed_targets"],
        first_report=first,
    )
    first_candidate_id = str(first["new_candidate_id"])
    first_learned_target = _learned_target_from_candidate(
        root,
        candidate_id=first_candidate_id,
        selected_target=first_selected,
    )

    extended_observations = [
        copy.deepcopy(target) for target in observed["observed_targets"]
    ]
    extended_observations.append(copy.deepcopy(first_learned_target))
    second_schedule = _precommit_second_schema_schedule(
        extended_observations,
        first_learned_target=first_learned_target,
        first_report=first,
    )

    attempts: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    for proposed in second_schedule["target_schedule"]:
        attempt = _attempt(root, proposed)
        attempts.append(
            {
                "target_index": int(proposed["target_index"]),
                "semantic_signature": str(proposed["semantic_signature"]),
                "supported_before_learning": bool(attempt["supported"]),
                "candidate_ids_supplied_by_caller": bool(
                    attempt["candidate_ids_supplied_by_caller"]
                ),
                "lineage": copy.deepcopy(proposed["lineage"]),
            }
        )
        if not attempt["supported"]:
            unsupported.append(proposed)
        if len(unsupported) >= 2:
            break
    if len(unsupported) < 2:
        raise IteratedObservedSchemaExtrapolationError(
            "second precommitted schema schedule exposed fewer than two fail-closed behaviors"
        )
    target, control = unsupported[:2]

    prior_ids = _all_candidate_ids(root)
    prior_tree = _tree_snapshot(root, prior_ids)
    prior_trials = _trial_snapshot(root, prior_ids)
    iterated = observed["iterated"]
    required_prior_ids = (
        str(iterated["first_candidate_id"]),
        str(iterated["second_candidate_id"]),
        str(iterated["first_expansion_candidate_id"]),
        str(iterated["second_expansion_candidate_id"]),
        str(recursive["new_candidate_id"]),
        first_candidate_id,
    )
    if not set(required_prior_ids).issubset(prior_ids):
        raise IteratedObservedSchemaExtrapolationError(
            "one or more first-stage schema prerequisites disappeared"
        )
    if not all(prior_trials.get(candidate_id) for candidate_id in prior_ids):
        raise IteratedObservedSchemaExtrapolationError(
            "pre-second-schema capability graph contains a Candidate without durable trials"
        )

    second_seed = str(observed["reconstructed"]["second_seed"])
    challenges = _challenge_inputs(second_seed)
    expected = tuple(execute_program(target["program"], value) for value in challenges)
    spec = {
        "name": f"iterated-observed-schema-{str(target['semantic_signature'])[:12]}",
        "input_domain": str(target["input_domain"]),
        "output_domain": str(target["output_domain"]),
        "examples": _examples(target),
        "probe": challenges[0],
        "expected": expected[0],
        "challenge": challenges[1],
        "challenge_expected": expected[1],
        "max_nodes": int(target["program_nodes"]),
    }

    with tempfile.TemporaryDirectory(prefix="agi-iterated-observed-schema-learn-") as temporary:
        learner = Path(temporary).resolve()
        _copy_persistent_state(root, learner)
        _assert_prior_unchanged(
            learner,
            prior_ids,
            prior_tree,
            prior_trials,
            label="iterated observed-schema learner reconstruction",
        )
        if _attempt(learner, target)["supported"]:
            raise IteratedObservedSchemaExtrapolationError(
                "second schema target became supported before learning"
            )
        learned = _promote_task(
            learner,
            seed=f"{seed}:second-schema-learn",
            spec=spec,
        )
        candidate_id = str(learned["candidate_id"])
        if candidate_id in prior_ids:
            raise IteratedObservedSchemaExtrapolationError(
                "second schema stage reused a prior Candidate identity"
            )
        if not learned.get("negative_evidence_retained"):
            raise IteratedObservedSchemaExtrapolationError(
                "second schema stage discarded negative evidence"
            )
        for value, expected_value in zip(challenges, expected, strict=True):
            if execute_program(learned["program"], value) != expected_value:
                raise IteratedObservedSchemaExtrapolationError(
                    "second schema Candidate failed the sealed challenge"
                )
        _assert_prior_unchanged(
            learner,
            prior_ids,
            prior_tree,
            prior_trials,
            label="iterated observed-schema learning",
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
        label="iterated observed-schema transactional commit",
    )
    final_ids = _all_candidate_ids(root)
    if len(final_ids) != len(prior_ids) + 1 or candidate_id not in final_ids:
        raise IteratedObservedSchemaExtrapolationError(
            "second schema stage did not commit exactly one new Candidate"
        )
    final_tree = _tree_snapshot(root, final_ids)
    final_trials = _trial_snapshot(root, final_ids)
    if not final_trials.get(candidate_id):
        raise IteratedObservedSchemaExtrapolationError(
            "second schema Candidate lacks durable trial evidence"
        )

    reconstructed = observed["reconstructed"]
    replay_targets = (
        reconstructed["first_control"],
        reconstructed["second_control"],
        reconstructed["expanded_target"],
        reconstructed["untouched_control"],
        observed["recursive_target"],
        first_learned_target,
        target,
    )
    replay_inputs = (
        _challenge_inputs(str(reconstructed["first_seed"])),
        challenges,
        challenges,
        challenges,
        challenges,
        challenges,
        challenges,
    )
    required_ids = (*required_prior_ids, candidate_id)
    replays: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="agi-iterated-observed-schema-resolver-") as temporary:
        resolver = Path(temporary).resolve()
        _copy_persistent_state(root, resolver)
        if _tree_snapshot(resolver, final_ids) != final_tree:
            raise IteratedObservedSchemaExtrapolationError(
                "fresh second-schema resolver changed Candidate bytes"
            )
        if _trial_snapshot(resolver, final_ids) != final_trials:
            raise IteratedObservedSchemaExtrapolationError(
                "fresh second-schema resolver changed Candidate trials"
            )
        for replay_target, required_id, inputs in zip(
            replay_targets,
            required_ids,
            replay_inputs,
            strict=True,
        ):
            replays.append(
                _solve_and_replay(
                    resolver,
                    replay_target,
                    required_candidate_id=required_id,
                    challenge_inputs=inputs,
                )
            )
        control_attempt = _attempt(resolver, control)
        if control_attempt["supported"]:
            raise IteratedObservedSchemaExtrapolationError(
                "second schema learning overgeneralized the untouched lineage-dependent control"
            )
        if _tree_snapshot(resolver, final_ids) != final_tree:
            raise IteratedObservedSchemaExtrapolationError(
                "second schema replay changed Candidate bytes"
            )
        if _trial_snapshot(resolver, final_ids) != final_trials:
            raise IteratedObservedSchemaExtrapolationError(
                "second schema replay changed Candidate trials"
            )

    all_seven = all(
        required in {str(value) for value in replay["solver_selected_candidate_ids"]}
        for required, replay in zip(required_ids, replays, strict=True)
    )
    all_without_ids = all(
        replay["solver_candidate_ids_supplied_by_caller"] is False for replay in replays
    )
    first_program_digest = str(second_schedule["first_learned_program_digest"])
    selected_lineage = target["lineage"]
    selected_depends_on_first = first_program_digest in {
        str(selected_lineage.get("left_program_digest")),
        str(selected_lineage.get("right_program_digest")),
    }
    control_lineage = control["lineage"]
    control_depends_on_first = first_program_digest in {
        str(control_lineage.get("left_program_digest")),
        str(control_lineage.get("right_program_digest")),
    }

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "iterated-observed-schema-extrapolation-v1",
        "seed": seed,
        "first_stage_digest": str(first["digest"]),
        "first_stage_candidate_id": first_candidate_id,
        "first_learned_program_digest": first_program_digest,
        "second_history_digest": str(second_schedule["history_digest"]),
        "second_schedule_commitment": str(second_schedule["schedule_commitment"]),
        "second_schedule_precommitted_before_support_checks": True,
        "second_proposals_require_first_learned_program_lineage": True,
        "support_attempts": attempts,
        "selected_semantic_signature": str(target["semantic_signature"]),
        "selected_lineage": copy.deepcopy(selected_lineage),
        "selected_depends_on_first_learned_program": selected_depends_on_first,
        "selected_failed_closed_before_learning": True,
        "control_semantic_signature": str(control["semantic_signature"]),
        "control_lineage": copy.deepcopy(control_lineage),
        "control_depends_on_first_learned_program": control_depends_on_first,
        "control_remained_failed_closed": True,
        "new_candidate_id": candidate_id,
        "new_candidate_negative_evidence_retained": True,
        "transactional_commit": commit,
        "prior_candidate_state_unchanged": True,
        "prior_trial_state_unchanged": True,
        "all_seven_capabilities_rediscovered": all_seven,
        "all_replays_avoided_caller_candidate_ids": all_without_ids,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded iterated schema-induction evidence only. The second objective must be "
            "derived from a literal-delta lineage that directly includes the verified program learned "
            "by the first observed-schema stage, so it cannot be a mere leftover from the first schedule. "
            "The induction rule, synthesis, regression, evaluator, probes and scoring remain repository-"
            "authored; this is not open-domain objective invention, independent production evidence, or AGI."
        ),
    }
    required = (
        report["second_schedule_precommitted_before_support_checks"],
        report["second_proposals_require_first_learned_program_lineage"],
        report["selected_depends_on_first_learned_program"],
        report["control_depends_on_first_learned_program"],
        report["selected_failed_closed_before_learning"],
        report["control_remained_failed_closed"],
        report["new_candidate_negative_evidence_retained"],
        report["prior_candidate_state_unchanged"],
        report["prior_trial_state_unchanged"],
        report["all_seven_capabilities_rediscovered"],
        report["all_replays_avoided_caller_candidate_ids"],
    )
    if not all(required):
        raise IteratedObservedSchemaExtrapolationError(
            "iterated observed-schema aggregate invariant failed"
        )
    report["digest"] = _digest(report)
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "iterated-observed-schema-extrapolation"
        / f"iterated-observed-schema-extrapolation-{_digest(seed)[:16]}.json",
        report,
    )
    return report
