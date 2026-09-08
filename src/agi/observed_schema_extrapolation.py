from __future__ import annotations

import copy
import json
import math
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import execute_program, validate_program_descriptor
from agi.crossdomain_failure_derived_target import _attempt, _examples, _solve_and_replay
from agi.durable_state_rehydration import _tree_snapshot
from agi.evidence_frontier_expansion import _challenge_inputs
from agi.evidence_frontier_iterated_expansion import _reconstruct_first_expansion
from agi.evidence_frontier_objective_selection import _assert_prior_unchanged
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.recursive_evidence_frontier_growth import (
    _first_expansion_report,
    _recursive_history_commitment,
    run_recursive_evidence_frontier_growth,
)
from agi.crossdomain_failure_derived_target import _expand_support_inputs, _precommit_schedule
from agi.transactional_multisession_commit import _commit_verified_candidate


class ObservedSchemaExtrapolationError(RuntimeError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ObservedSchemaExtrapolationError(f"expected object evidence at {path}")
    return value


def _shape(value: Any) -> Any:
    """Canonical AST shape with numeric literal values erased but all observed operators retained."""
    if isinstance(value, Mapping):
        if (
            value.get("op") == "const"
            and value.get("domain") == "numeric"
            and isinstance(value.get("value"), (int, float))
            and not isinstance(value.get("value"), bool)
        ):
            return {"op": "const", "domain": "numeric", "value": "<numeric>"}
        return {str(key): _shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(item) for item in value]
    return value


def _numeric_constant_paths(
    expression: Mapping[str, Any],
    path: tuple[str, ...] = (),
) -> tuple[tuple[str, ...], ...]:
    paths: list[tuple[str, ...]] = []
    if (
        expression.get("op") == "const"
        and expression.get("domain") == "numeric"
        and isinstance(expression.get("value"), (int, float))
        and not isinstance(expression.get("value"), bool)
    ):
        paths.append(path)
    for key, value in expression.items():
        if isinstance(value, Mapping):
            paths.extend(_numeric_constant_paths(value, (*path, str(key))))
    return tuple(paths)


def _node_at(expression: Mapping[str, Any], path: Sequence[str]) -> Mapping[str, Any]:
    node: Any = expression
    for key in path:
        if not isinstance(node, Mapping):
            raise ObservedSchemaExtrapolationError("numeric-literal path left the expression tree")
        node = node.get(key)
    if not isinstance(node, Mapping):
        raise ObservedSchemaExtrapolationError("numeric-literal path did not identify an AST node")
    return node


def _set_numeric_constant(
    expression: Mapping[str, Any],
    path: Sequence[str],
    value: int | float,
) -> dict[str, Any]:
    result = copy.deepcopy(dict(expression))
    node: Any = result
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise ObservedSchemaExtrapolationError("cannot rewrite missing numeric-literal path")
        node = node[key]
    if (
        not isinstance(node, dict)
        or node.get("op") != "const"
        or node.get("domain") != "numeric"
    ):
        raise ObservedSchemaExtrapolationError("rewrite path is not a numeric constant")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ObservedSchemaExtrapolationError("extrapolated literal is not finite numeric")
    if abs(float(value)) > 1_000_000:
        raise ObservedSchemaExtrapolationError("extrapolated literal exceeds the unchanged safe bound")
    node["value"] = value
    return result


def _target_from_program(
    program: Mapping[str, Any],
    support_inputs: Sequence[Any],
) -> dict[str, Any]:
    descriptor = copy.deepcopy(dict(program))
    meta = validate_program_descriptor(descriptor)
    examples = [
        {"input": copy.deepcopy(value), "output": execute_program(descriptor, value)}
        for value in support_inputs
    ]
    if len({_digest(item["output"]) for item in examples}) <= 1:
        raise ObservedSchemaExtrapolationError("schema extrapolation became constant on support")
    signature = _digest(
        {
            "input_domain": str(meta["input_domain"]),
            "output_domain": str(meta["output_domain"]),
            "support_examples": examples,
            "phase": "observed-schema-extrapolation-support-v1",
        }
    )
    return {
        "input_domain": str(meta["input_domain"]),
        "output_domain": str(meta["output_domain"]),
        "program": descriptor,
        "program_nodes": int(meta["nodes"]),
        "support_examples": examples,
        "semantic_signature": signature,
    }


def _program_behavior(program: Mapping[str, Any], support_inputs: Sequence[Any]) -> str | None:
    try:
        outputs = [execute_program(program, value) for value in support_inputs]
    except (TypeError, ValueError, RuntimeError):
        return None
    return _digest(outputs)


def _precommit_observed_schema_schedule(
    observed_targets: Sequence[Mapping[str, Any]],
    *,
    history_digest: str,
) -> dict[str, Any]:
    """Extrapolate literal deltas learned from observed same-shape programs, not a fixed mutation list.

    Every proposed program is a one-step continuation of a numeric-literal difference that is actually
    present between two already learned programs with identical AST shape and input/output domains.
    Operators, tree shape, literal positions, and delta magnitudes therefore all come from durable
    observed capability semantics. Validation and node/output bounds remain unchanged.
    """
    if not isinstance(history_digest, str) or len(history_digest) != 64:
        raise ObservedSchemaExtrapolationError("history_digest must be a SHA-256 digest")
    if len(observed_targets) < 2:
        raise ObservedSchemaExtrapolationError("at least two observed targets are required")

    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(observed_targets):
        program = raw.get("program")
        support = raw.get("support_examples")
        if not isinstance(program, Mapping) or not isinstance(support, list) or len(support) < 2:
            raise ObservedSchemaExtrapolationError("observed target lacks program/support semantics")
        meta = validate_program_descriptor(program)
        expression = program.get("expression")
        if not isinstance(expression, Mapping):
            raise ObservedSchemaExtrapolationError("observed target lacks expression")
        normalized.append(
            {
                "index": index,
                "target": raw,
                "program": copy.deepcopy(dict(program)),
                "expression": copy.deepcopy(dict(expression)),
                "input_domain": str(meta["input_domain"]),
                "output_domain": str(meta["output_domain"]),
                "shape": _shape(expression),
                "shape_digest": _digest(_shape(expression)),
                "program_digest": _digest(program),
            }
        )

    candidates: list[dict[str, Any]] = []
    seen_behaviors: set[str] = set()
    seen_programs: set[str] = {str(item["program_digest"]) for item in normalized}

    for left in normalized:
        for right in normalized:
            if left["index"] == right["index"]:
                continue
            if (
                left["input_domain"] != right["input_domain"]
                or left["output_domain"] != right["output_domain"]
                or left["shape_digest"] != right["shape_digest"]
            ):
                continue
            left_paths = _numeric_constant_paths(left["expression"])
            right_paths = _numeric_constant_paths(right["expression"])
            if left_paths != right_paths or not left_paths:
                continue

            support_inputs = tuple(
                copy.deepcopy(item["input"]) for item in right["target"]["support_examples"]
            )
            observed_behaviors = {
                behavior
                for item in normalized
                if item["input_domain"] == right["input_domain"]
                for behavior in [_program_behavior(item["program"], support_inputs)]
                if behavior is not None
            }

            rewrites: list[tuple[dict[str, Any], tuple[dict[str, Any], ...]]] = []
            vector_expression = copy.deepcopy(right["expression"])
            vector_lineage: list[dict[str, Any]] = []
            vector_changed = False
            for path in left_paths:
                left_node = _node_at(left["expression"], path)
                right_node = _node_at(right["expression"], path)
                left_value = left_node.get("value")
                right_value = right_node.get("value")
                if (
                    isinstance(left_value, bool)
                    or isinstance(right_value, bool)
                    or not isinstance(left_value, (int, float))
                    or not isinstance(right_value, (int, float))
                ):
                    continue
                delta = right_value - left_value
                if delta == 0:
                    continue
                extrapolated = right_value + delta
                try:
                    single = _set_numeric_constant(right["expression"], path, extrapolated)
                except ObservedSchemaExtrapolationError:
                    continue
                lineage = ({
                    "path": list(path),
                    "left_value": left_value,
                    "right_value": right_value,
                    "observed_delta": delta,
                    "extrapolated_value": extrapolated,
                },)
                rewrites.append((single, lineage))
                try:
                    vector_expression = _set_numeric_constant(vector_expression, path, extrapolated)
                except ObservedSchemaExtrapolationError:
                    continue
                vector_changed = True
                vector_lineage.append(dict(lineage[0]))
            if vector_changed and len(vector_lineage) > 1:
                rewrites.append((vector_expression, tuple(vector_lineage)))

            for expression, lineage in rewrites:
                descriptor = copy.deepcopy(right["program"])
                descriptor["expression"] = expression
                try:
                    meta = validate_program_descriptor(descriptor)
                    if int(meta["nodes"]) > 9:
                        continue
                    target = _target_from_program(descriptor, support_inputs)
                except (TypeError, ValueError, ObservedSchemaExtrapolationError):
                    continue
                program_digest = _digest(descriptor)
                if program_digest in seen_programs:
                    continue
                behavior = _program_behavior(descriptor, support_inputs)
                if behavior is None or behavior in observed_behaviors or behavior in seen_behaviors:
                    continue
                seen_programs.add(program_digest)
                seen_behaviors.add(behavior)
                target["lineage"] = {
                    "left_program_digest": left["program_digest"],
                    "right_program_digest": right["program_digest"],
                    "shape_digest": right["shape_digest"],
                    "literal_extrapolations": [dict(item) for item in lineage],
                }
                candidates.append(target)

    candidates.sort(
        key=lambda item: _digest(
            {
                "history_digest": history_digest,
                "semantic_signature": item["semantic_signature"],
                "lineage": item["lineage"],
                "phase": "observed-schema-extrapolation-rank-v1",
            }
        )
    )
    if len(candidates) < 2:
        raise ObservedSchemaExtrapolationError(
            "observed learned programs exposed fewer than two novel extrapolated behaviors"
        )
    for index, target in enumerate(candidates):
        target["target_index"] = index
    commitment = _digest(
        {
            "history_digest": history_digest,
            "targets": candidates,
            "phase": "observed-schema-extrapolation-precommit-v1",
        }
    )
    return {"target_schedule": candidates, "schedule_commitment": commitment}


def _load_iterated_report(root: Path, iterated_seed: str) -> dict[str, Any]:
    path = (
        root
        / ".continual"
        / "evidence"
        / "evidence-frontier-iterated-expansion"
        / f"evidence-frontier-iterated-expansion-{_digest(iterated_seed)[:16]}.json"
    )
    report = _load_json(path)
    if not report.get("passed") or str(report.get("seed")) != iterated_seed:
        raise ObservedSchemaExtrapolationError("persisted iterated evidence is not reusable")
    return report


def _reconstruct_observed_targets(
    root: Path,
    *,
    recursive_seed: str,
    recursive_report: Mapping[str, Any],
) -> dict[str, Any]:
    iterated_seed = f"{recursive_seed}:iterated"
    iterated = _load_iterated_report(root, iterated_seed)
    expansion_seed = f"{iterated_seed}:first-expansion"
    expansion = _first_expansion_report(root, expansion_seed)
    reconstructed = _reconstruct_first_expansion(
        root,
        expansion_seed=expansion_seed,
        expansion_report=expansion,
    )
    first_control = reconstructed["first_control"]
    second_control = reconstructed["second_control"]
    first_expanded = reconstructed["expanded_target"]
    second_expanded = reconstructed["untouched_control"]
    history_digest = _recursive_history_commitment(
        iterated_report=iterated,
        learned_target=second_expanded,
    )
    persisted_inputs = tuple(
        copy.deepcopy(item["input"]) for item in second_expanded["support_examples"]
    )
    support_inputs = _expand_support_inputs(
        second_expanded["program"],
        persisted_inputs,
        history_digest=history_digest,
    )
    schedule = _precommit_schedule(
        second_expanded["program"],
        history_digest=history_digest,
        support_inputs=support_inputs,
    )
    recursive_signature = str(recursive_report["recursive_semantic_signature"])
    control_signature = str(recursive_report["recursive_control_semantic_signature"])
    recursive_matches = [
        target
        for target in schedule["target_schedule"]
        if str(target["semantic_signature"]) == recursive_signature
    ]
    control_matches = [
        target
        for target in schedule["target_schedule"]
        if str(target["semantic_signature"]) == control_signature
    ]
    if len(recursive_matches) != 1 or len(control_matches) != 1:
        raise ObservedSchemaExtrapolationError("recursive target/control reconstruction changed")
    return {
        "iterated": iterated,
        "reconstructed": reconstructed,
        "recursive_target": recursive_matches[0],
        "recursive_control": control_matches[0],
        "observed_targets": [
            first_control,
            second_control,
            first_expanded,
            second_expanded,
            recursive_matches[0],
        ],
    }


def run_observed_schema_extrapolation(root: Path, seed: str) -> dict[str, Any]:
    """Infer a new objective family from parameter differences in learned program schemas.

    Unlike the earlier finite repository-authored mutation list, target proposals here are generated
    only by extrapolating literal deltas actually observed between same-shape learned programs. The
    schedule is committed before support checks, one fail-closed novel behavior is learned, a second is
    retained as a control, all prior Candidate/trial bytes remain immutable, and six accumulated
    behaviors must be rediscovered from fresh state without caller Candidate IDs.

    This still uses repository-authored extrapolation, synthesis, regression and scoring and therefore
    remains internal bounded development evidence rather than independent evidence or an AGI claim.

    Diagnostic status (2026-08-23): the currently persisted capability chain exposes no pair of
    same-shape learned programs with differing numeric literals among its deterministically
    reconstructible members, so this campaign fails closed at schedule construction (CI run
    32607076987; reproduced locally on heads 2376ed2 and a097ab1). The recursive prerequisite's
    target selection also varied across processes with identical seeds; an adversarially verified
    sweep established the mechanism as per-process entropy (uuid4 engine run ids and timestamped
    trial-ledger bytes) entering the iterated report digest and being folded into the v1 history
    commitment that salts rotation, mutation constants, and schedule ordering - not hash-seed
    dependence in the synthesis search, as an earlier record stated. The v2 commitment in
    recursive_evidence_frontier_growth binds only stable behavior semantics, restoring
    deterministic selection. The campaign body is retained unchanged for a future chain state
    that truthfully contains observed same-shape literal variation.
    """
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    recursive_seed = f"{seed}:recursive"
    recursive = run_recursive_evidence_frontier_growth(root, recursive_seed)
    if not recursive.get("passed") or not recursive.get("all_five_capabilities_rediscovered"):
        raise ObservedSchemaExtrapolationError("recursive prerequisite did not retain five capabilities")

    observed = _reconstruct_observed_targets(
        root,
        recursive_seed=recursive_seed,
        recursive_report=recursive,
    )
    history_digest = _digest(
        {
            "recursive_digest": recursive["digest"],
            "observed_program_digests": [
                _digest(target["program"]) for target in observed["observed_targets"]
            ],
            "phase": "observed-schema-history-v1",
        }
    )
    schedule = _precommit_observed_schema_schedule(
        observed["observed_targets"],
        history_digest=history_digest,
    )

    attempts: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    for target in schedule["target_schedule"]:
        attempt = _attempt(root, target)
        attempts.append(
            {
                "target_index": int(target["target_index"]),
                "semantic_signature": str(target["semantic_signature"]),
                "supported_before_learning": bool(attempt["supported"]),
                "candidate_ids_supplied_by_caller": bool(
                    attempt["candidate_ids_supplied_by_caller"]
                ),
            }
        )
        if not attempt["supported"]:
            unsupported.append(target)
        if len(unsupported) >= 2:
            break
    if len(unsupported) < 2:
        raise ObservedSchemaExtrapolationError(
            "precommitted observed-schema schedule exposed fewer than two fail-closed behaviors"
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
    )
    if not set(required_prior_ids).issubset(prior_ids):
        raise ObservedSchemaExtrapolationError("one or more observed-schema prerequisites disappeared")
    if not all(prior_trials.get(candidate_id) for candidate_id in prior_ids):
        raise ObservedSchemaExtrapolationError("prior capability graph lacks durable trial evidence")

    second_seed = str(observed["reconstructed"]["second_seed"])
    challenges = _challenge_inputs(second_seed)
    expected = tuple(execute_program(target["program"], value) for value in challenges)
    spec = {
        "name": f"observed-schema-{str(target['semantic_signature'])[:12]}",
        "input_domain": str(target["input_domain"]),
        "output_domain": str(target["output_domain"]),
        "examples": _examples(target),
        "probe": challenges[0],
        "expected": expected[0],
        "challenge": challenges[1],
        "challenge_expected": expected[1],
        "max_nodes": int(target["program_nodes"]),
    }

    with tempfile.TemporaryDirectory(prefix="agi-observed-schema-learn-") as temporary:
        learner = Path(temporary).resolve()
        _copy_persistent_state(root, learner)
        _assert_prior_unchanged(
            learner, prior_ids, prior_tree, prior_trials, label="observed-schema learner reconstruction"
        )
        if _attempt(learner, target)["supported"]:
            raise ObservedSchemaExtrapolationError("schema target became supported before learning")
        learned = _promote_task(
            learner,
            seed=f"{seed}:observed-schema-learn",
            spec=spec,
        )
        candidate_id = str(learned["candidate_id"])
        if candidate_id in prior_ids:
            raise ObservedSchemaExtrapolationError("schema extrapolation reused a prior Candidate identity")
        if not learned.get("negative_evidence_retained"):
            raise ObservedSchemaExtrapolationError("schema extrapolation discarded negative evidence")
        for value, expected_value in zip(challenges, expected, strict=True):
            if execute_program(learned["program"], value) != expected_value:
                raise ObservedSchemaExtrapolationError("schema Candidate failed the sealed challenge")
        _assert_prior_unchanged(
            learner, prior_ids, prior_tree, prior_trials, label="observed-schema learning"
        )
        commit = _commit_verified_candidate(
            learner,
            root,
            candidate_id=candidate_id,
            scope=str(learned["scope"]),
        )

    _assert_prior_unchanged(
        root, prior_ids, prior_tree, prior_trials, label="observed-schema transactional commit"
    )
    final_ids = _all_candidate_ids(root)
    if len(final_ids) != len(prior_ids) + 1 or candidate_id not in final_ids:
        raise ObservedSchemaExtrapolationError("schema extrapolation did not commit exactly one Candidate")
    final_tree = _tree_snapshot(root, final_ids)
    final_trials = _trial_snapshot(root, final_ids)
    if not final_trials.get(candidate_id):
        raise ObservedSchemaExtrapolationError("schema Candidate lacks durable trial evidence")

    reconstructed = observed["reconstructed"]
    targets = (
        reconstructed["first_control"],
        reconstructed["second_control"],
        reconstructed["expanded_target"],
        reconstructed["untouched_control"],
        observed["recursive_target"],
        target,
    )
    challenge_sets = (
        _challenge_inputs(str(reconstructed["first_seed"])),
        challenges,
        challenges,
        challenges,
        challenges,
        challenges,
    )
    required_ids = (*required_prior_ids, candidate_id)
    replays: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="agi-observed-schema-resolver-") as temporary:
        resolver = Path(temporary).resolve()
        _copy_persistent_state(root, resolver)
        if _tree_snapshot(resolver, final_ids) != final_tree:
            raise ObservedSchemaExtrapolationError("fresh resolver changed Candidate bytes")
        if _trial_snapshot(resolver, final_ids) != final_trials:
            raise ObservedSchemaExtrapolationError("fresh resolver changed Candidate trials")
        for prior_target, required_id, replay_inputs in zip(
            targets, required_ids, challenge_sets, strict=True
        ):
            replays.append(
                _solve_and_replay(
                    resolver,
                    prior_target,
                    required_candidate_id=required_id,
                    challenge_inputs=replay_inputs,
                )
            )
        control_attempt = _attempt(resolver, control)
        if control_attempt["supported"]:
            raise ObservedSchemaExtrapolationError(
                "observed-schema learning overgeneralized the untouched extrapolated control"
            )
        if _tree_snapshot(resolver, final_ids) != final_tree:
            raise ObservedSchemaExtrapolationError("behavior replay changed Candidate bytes")
        if _trial_snapshot(resolver, final_ids) != final_trials:
            raise ObservedSchemaExtrapolationError("behavior replay changed Candidate trials")

    all_six = all(
        required in {str(value) for value in replay["solver_selected_candidate_ids"]}
        for required, replay in zip(required_ids, replays, strict=True)
    )
    all_without_ids = all(
        replay["solver_candidate_ids_supplied_by_caller"] is False for replay in replays
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "observed-schema-extrapolation-v1",
        "seed": seed,
        "recursive_prerequisite_digest": str(recursive["digest"]),
        "history_digest": history_digest,
        "proposal_source": "numeric_literal_deltas_observed_between_same_shape_learned_programs",
        "fixed_repository_mutation_list_used_for_new_objective": False,
        "schedule_commitment": str(schedule["schedule_commitment"]),
        "schedule_precommitted_before_support_checks": True,
        "support_attempts": attempts,
        "selected_semantic_signature": str(target["semantic_signature"]),
        "selected_lineage": target["lineage"],
        "selected_failed_closed_before_learning": True,
        "control_semantic_signature": str(control["semantic_signature"]),
        "control_remained_failed_closed": True,
        "new_candidate_id": candidate_id,
        "new_candidate_negative_evidence_retained": True,
        "transactional_commit": commit,
        "prior_candidate_state_unchanged": True,
        "prior_trial_state_unchanged": True,
        "all_six_capabilities_rediscovered": all_six,
        "all_replays_avoided_caller_candidate_ids": all_without_ids,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded schema-extrapolation development evidence only. The new objective family "
            "is proposed by extrapolating numeric-literal deltas actually observed between same-shape "
            "learned programs rather than selecting from the earlier fixed mutation list, but the "
            "extrapolator, synthesis, regression, evaluator, probes and scoring remain repository-authored. "
            "This is not open-domain objective invention, independent production evidence, or AGI."
        ),
    }
    required = (
        report["fixed_repository_mutation_list_used_for_new_objective"] is False,
        report["schedule_precommitted_before_support_checks"],
        report["selected_failed_closed_before_learning"],
        report["control_remained_failed_closed"],
        report["new_candidate_negative_evidence_retained"],
        report["prior_candidate_state_unchanged"],
        report["prior_trial_state_unchanged"],
        report["all_six_capabilities_rediscovered"],
        report["all_replays_avoided_caller_candidate_ids"],
    )
    if not all(required):
        raise ObservedSchemaExtrapolationError("observed-schema aggregate invariant failed")
    report["digest"] = _digest(report)
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "observed-schema-extrapolation"
        / f"observed-schema-extrapolation-{_digest(seed)[:16]}.json",
        report,
    )
    return report
