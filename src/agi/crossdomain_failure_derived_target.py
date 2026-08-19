from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, _infer_expression, execute_program, validate_program_descriptor
from agi.adaptive_depth_composition import AdaptiveDepthCompositionError, synthesize_shallowest_verified_route
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.durable_state_rehydration import _tree_snapshot
from agi.generated_crossdomain_cegis import generated_crossdomain_target, run_generated_crossdomain_cegis_campaign
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.transactional_multisession_commit import _commit_verified_candidate
from agi.verified_macro_synthesis import _load_verified_macro_items


class CrossDomainFailureDerivedTargetError(RuntimeError):
    pass


def _unique_inputs(records: Sequence[Mapping[str, Any]]) -> tuple[Any, ...]:
    seen: set[str] = set()
    values: list[Any] = []
    for record in records:
        value = record["input"]
        key = _digest(value)
        if key in seen:
            continue
        seen.add(key)
        values.append(copy.deepcopy(value))
    if len(values) < 2:
        raise CrossDomainFailureDerivedTargetError("persisted failure history has fewer than two inputs")
    return tuple(values)


def _expand_support_inputs(
    base_program: Mapping[str, Any],
    persisted_inputs: Sequence[Any],
    *,
    history_digest: str,
) -> tuple[Any, ...]:
    """Deterministically enrich persisted support without consulting sealed held-out answers."""
    values = [copy.deepcopy(value) for value in persisted_inputs]
    seen = {_digest(value) for value in values}
    input_domain = str(base_program["input_domain"])
    candidates: list[Any] = []

    if input_domain == "sequence":
        sequences = [list(value) for value in persisted_inputs if isinstance(value, list)]
        numeric = [
            value
            for value in sequences
            if value and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
        ]
        if not numeric:
            raise CrossDomainFailureDerivedTargetError(
                "persisted sequence failure history has no numeric anchor for support expansion"
            )
        anchor = next((value for value in numeric if sum(value) != 0), numeric[0])
        canonical_sums = list(range(-10, 11))
        sum_rotation = int(history_digest[24:32], 16) % len(canonical_sums)
        canonical_sums = [*canonical_sums[sum_rotation:], *canonical_sums[:sum_rotation]]
        candidates = [
            [*anchor, *anchor],
            [-item for item in anchor],
            [*anchor, *anchor, *anchor],
            [*anchor, *[-item for item in anchor], *anchor],
            list(reversed(anchor)),
            [*list(reversed(anchor)), *anchor],
            *[[value, 0, 0] for value in canonical_sums],
        ]
    elif input_domain == "string":
        strings = [value for value in persisted_inputs if isinstance(value, str)]
        nonempty = [value for value in strings if value]
        if not nonempty:
            raise CrossDomainFailureDerivedTargetError(
                "persisted string failure history has no non-empty anchor for support expansion"
            )
        anchor = next((value for value in nonempty if value != value[::-1]), max(nonempty, key=len))
        candidates = [
            anchor * 2,
            anchor * 3,
            anchor * 4,
            anchor[::-1],
            anchor + anchor[:1],
            anchor[:1] + anchor,
            anchor.swapcase(),
            anchor.upper() + anchor.lower(),
        ]
    else:
        raise CrossDomainFailureDerivedTargetError(
            f"unsupported persisted input domain for support expansion: {input_domain}"
        )

    if candidates:
        rotation = int(history_digest[16:24], 16) % len(candidates)
        candidates = [*candidates[rotation:], *candidates[:rotation]]
    for candidate in candidates:
        if input_domain == "sequence" and (not isinstance(candidate, list) or len(candidate) > 32):
            continue
        if input_domain == "string" and (not isinstance(candidate, str) or len(candidate) > 128):
            continue
        key = _digest(candidate)
        if key in seen:
            continue
        seen.add(key)
        values.append(copy.deepcopy(candidate))
        if len(values) >= 32:
            break
    if len(values) < 5:
        raise CrossDomainFailureDerivedTargetError(
            "persisted failure history could not produce enough distinct bounded support inputs"
        )
    return tuple(values)


def _semantic_signature(target: Mapping[str, Any]) -> str:
    return _digest(
        {
            "input_domain": target["input_domain"],
            "output_domain": target["output_domain"],
            "support_examples": target["support_examples"],
            "phase": "crossdomain-canonical-support-behavior-v1",
        }
    )


def _target_from_expression(
    base_program: Mapping[str, Any],
    expression: Mapping[str, Any],
    support_inputs: Sequence[Any],
) -> dict[str, Any]:
    input_domain = str(base_program["input_domain"])
    output_domain, _nodes, _depth = _infer_expression(expression, input_domain=input_domain)
    descriptor = {
        "input_domain": input_domain,
        "output_domain": output_domain,
        "expression": copy.deepcopy(dict(expression)),
        "effects": [],
        "max_steps": 64,
        "max_output_length": 1024,
    }
    meta = validate_program_descriptor(descriptor)
    examples = [
        {"input": copy.deepcopy(value), "output": execute_program(descriptor, value)}
        for value in support_inputs
    ]
    if len({_digest(item["output"]) for item in examples}) <= 1:
        raise CrossDomainFailureDerivedTargetError("derived cross-domain target is constant on support")
    target: dict[str, Any] = {
        "input_domain": str(meta["input_domain"]),
        "output_domain": str(meta["output_domain"]),
        "program": descriptor,
        "program_nodes": int(meta["nodes"]),
        "support_examples": examples,
    }
    target["semantic_signature"] = _semantic_signature(target)
    return target


def _candidate_expressions(base_program: Mapping[str, Any], salt: int) -> list[dict[str, Any]]:
    base = copy.deepcopy(base_program["expression"])
    output_domain = str(base_program["output_domain"])

    def numeric_const(value: int) -> dict[str, Any]:
        return {"op": "const", "domain": "numeric", "value": value}

    def numeric_variants(value: Mapping[str, Any]) -> list[dict[str, Any]]:
        numeric = copy.deepcopy(dict(value))
        magnitude = 2 + salt % 4
        return [
            {"op": "neg", "arg": copy.deepcopy(numeric)},
            {"op": "abs", "arg": copy.deepcopy(numeric)},
            {"op": "add", "left": copy.deepcopy(numeric), "right": numeric_const(magnitude)},
            {"op": "sub", "left": copy.deepcopy(numeric), "right": numeric_const(magnitude)},
            {"op": "mul", "left": copy.deepcopy(numeric), "right": numeric_const(2)},
            {"op": "ge_numeric", "left": copy.deepcopy(numeric), "right": numeric_const(0)},
        ]

    if output_domain == "numeric":
        return [
            *numeric_variants(base),
            {"op": "to_string", "arg": copy.deepcopy(base)},
        ]
    if output_domain == "string":
        expressions = [
            {"op": "reverse", "arg": copy.deepcopy(base)},
            {"op": "upper", "arg": copy.deepcopy(base)},
            {"op": "lower", "arg": copy.deepcopy(base)},
            {"op": "length_string", "arg": copy.deepcopy(base)},
            {"op": "chars", "arg": copy.deepcopy(base)},
        ]
        if isinstance(base, dict) and base.get("op") == "to_string" and isinstance(base.get("arg"), dict):
            inner = copy.deepcopy(base["arg"])
            inner_variants = numeric_variants(inner)
            expressions = [
                *inner_variants,
                *[
                    {"op": "to_string", "arg": copy.deepcopy(item)}
                    for item in inner_variants[:5]
                ],
                *expressions,
            ]
        return expressions
    if output_domain == "sequence":
        input_domain = str(base_program["input_domain"])
        expressions = [
            {"op": "length_sequence", "arg": copy.deepcopy(base)},
            {"op": "concat_sequence", "left": copy.deepcopy(base), "right": copy.deepcopy(base)},
        ]
        if input_domain == "string":
            chars_input = {"op": "chars", "arg": {"op": "input"}}
            expressions.extend(
                [
                    {"op": "concat_sequence", "left": copy.deepcopy(base), "right": copy.deepcopy(chars_input)},
                    {"op": "concat_sequence", "left": copy.deepcopy(chars_input), "right": copy.deepcopy(base)},
                ]
            )
        return expressions
    raise CrossDomainFailureDerivedTargetError(
        f"unsupported persisted cross-domain output for derivation: {output_domain}"
    )


def _precommit_schedule(
    base_program: Mapping[str, Any],
    *,
    history_digest: str,
    support_inputs: Sequence[Any],
) -> dict[str, Any]:
    base_target = _target_from_expression(base_program, base_program["expression"], support_inputs)
    base_signature = str(base_target["semantic_signature"])
    salt = int(history_digest[:8], 16)
    targets: list[dict[str, Any]] = []
    seen: set[str] = {base_signature}
    for expression in _candidate_expressions(base_program, salt):
        try:
            target = _target_from_expression(base_program, expression, support_inputs)
        except (ValueError, TypeError, CrossDomainFailureDerivedTargetError):
            continue
        if int(target["program_nodes"]) > 9:
            continue
        signature = str(target["semantic_signature"])
        if signature in seen:
            continue
        seen.add(signature)
        targets.append(target)
    targets.sort(
        key=lambda item: _digest(
            {
                "history_digest": history_digest,
                "semantic_signature": item["semantic_signature"],
                "program": item["program"],
                "phase": "crossdomain-failure-derived-rank-v1",
            }
        )
    )
    if len(targets) < 2:
        raise CrossDomainFailureDerivedTargetError("fewer than two derived behaviors fit the unchanged bounds")
    for index, target in enumerate(targets):
        target["target_index"] = index
    commitment = _digest(
        {
            "history_digest": history_digest,
            "base_semantic_signature": base_signature,
            "targets": targets,
            "phase": "crossdomain-failure-derived-schedule-v1",
        }
    )
    return {
        "base_target": base_target,
        "target_schedule": targets,
        "schedule_commitment": commitment,
    }


def _examples(target: Mapping[str, Any]) -> tuple[ProgramExample, ...]:
    return tuple(
        ProgramExample(copy.deepcopy(item["input"]), copy.deepcopy(item["output"]))
        for item in target["support_examples"]
    )


def _attempt(root: Path, target: Mapping[str, Any]) -> dict[str, Any]:
    try:
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
    except AdaptiveDepthCompositionError as exc:
        return {
            "supported": False,
            "candidate_ids_supplied_by_caller": False,
            "failure": str(exc),
        }
    return {
        "supported": True,
        "candidate_ids_supplied_by_caller": bool(solved["candidate_ids_supplied_by_caller"]),
        "selected_candidate_ids": list(solved["selected_candidate_ids"]),
        "selected_chain_depth": int(solved["selected_chain_depth"]),
    }


def _solve_and_replay(
    root: Path,
    target: Mapping[str, Any],
    *,
    required_candidate_id: str,
    challenge_inputs: Sequence[Any],
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
    if solved["candidate_ids_supplied_by_caller"] is not False:
        raise CrossDomainFailureDerivedTargetError("behavior-only resolver used caller Candidate IDs")
    selected_ids = [str(value) for value in solved["selected_candidate_ids"]]
    if required_candidate_id not in selected_ids:
        raise CrossDomainFailureDerivedTargetError("required learned Candidate was not rediscovered")
    selected = _load_verified_macro_items(root, selected_ids)
    run_ids: list[str] = []
    outputs: list[dict[str, Any]] = []
    for value in challenge_inputs:
        expected = execute_program(target["program"], value)
        compiled = execute_program(solved["compiled_program"], value)
        runtime, run_id, refs = _execute_chain(root, selected, value)
        if compiled != expected or runtime != expected:
            raise CrossDomainFailureDerivedTargetError("fresh cross-domain replay failed held-back behavior")
        if len(refs) != int(solved["selected_chain_depth"]):
            raise CrossDomainFailureDerivedTargetError("fresh replay executed the wrong stage count")
        run_ids.append(run_id)
        outputs.append(
            {
                "input_sha256": _digest(value),
                "expected_sha256": _digest(expected),
                "output_sha256": _digest(runtime),
            }
        )
    if len(set(run_ids)) != len(run_ids):
        raise CrossDomainFailureDerivedTargetError("fresh cross-domain replay reused an Engine run")
    return {
        "solver_candidate_ids_supplied_by_caller": False,
        "solver_selected_candidate_ids": selected_ids,
        "solver_selected_chain_depth": int(solved["selected_chain_depth"]),
        "fresh_engine_run_ids": run_ids,
        "fresh_engine_runs_unique": True,
        "heldback_outputs": outputs,
    }


def run_crossdomain_failure_derived_target(root: Path, seed: str) -> dict[str, Any]:
    """Turn persisted cross-domain CEGIS failure structure into a distinct bounded next target."""
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    source_seed = f"{seed}:source-cegis"
    source = run_generated_crossdomain_cegis_campaign(root, source_seed)
    if not source.get("passed") or int(source.get("counterexample_count", 0)) < 1:
        raise CrossDomainFailureDerivedTargetError("source cross-domain CEGIS did not retain a real failure")
    if not source.get("negative_evidence_retained"):
        raise CrossDomainFailureDerivedTargetError("source cross-domain negative evidence was not retained")

    candidate_id = str(source["candidate_id"])
    candidate_dir = root / ".continual" / "candidates" / candidate_id
    candidate = json.loads((candidate_dir / "candidate.json").read_text(encoding="utf-8"))
    history_path = candidate_dir / "learning-history" / "generated-crossdomain-cegis.json"
    history = json.loads(history_path.read_text(encoding="utf-8"))
    if not history.get("counterexamples") or not candidate.get("contradictory_evidence"):
        raise CrossDomainFailureDerivedTargetError("source failure structure is not durable")
    history_digest = _digest(history)
    base_program = dict(candidate["acquired_program"]["program"])
    meta = validate_program_descriptor(base_program)
    if str(meta["input_domain"]) != source["input_domain"] or str(meta["output_domain"]) != source["output_domain"]:
        raise CrossDomainFailureDerivedTargetError("durable source Candidate type changed")

    support_records = [*history["initial_demonstrations"], *history["counterexamples"]]
    persisted_support_inputs = _unique_inputs(support_records)
    support_inputs = _expand_support_inputs(
        base_program,
        persisted_support_inputs,
        history_digest=history_digest,
    )
    schedule = _precommit_schedule(
        base_program,
        history_digest=history_digest,
        support_inputs=support_inputs,
    )
    attempts: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    for target in schedule["target_schedule"]:
        attempt = _attempt(root, target)
        attempts.append(
            {
                "target_index": int(target["target_index"]),
                "semantic_signature": str(target["semantic_signature"]),
                "input_domain": str(target["input_domain"]),
                "output_domain": str(target["output_domain"]),
                "supported_before_learning": bool(attempt["supported"]),
                "candidate_ids_supplied_by_caller": bool(attempt["candidate_ids_supplied_by_caller"]),
            }
        )
        if not attempt["supported"]:
            unsupported.append(target)
        if len(unsupported) >= 2:
            break
    if len(unsupported) < 2:
        raise CrossDomainFailureDerivedTargetError(
            "precommitted cross-domain evidence-derived schedule exposed fewer than two gaps"
        )
    target, control = unsupported[:2]

    prior_ids = _all_candidate_ids(root)
    prior_tree = _tree_snapshot(root, prior_ids)
    prior_trials = _trial_snapshot(root, prior_ids)
    if candidate_id not in prior_ids:
        raise CrossDomainFailureDerivedTargetError("source CEGIS Candidate disappeared before next learning")

    source_target, _source_meta, _initial, _diagnostic, final_inputs = generated_crossdomain_target(source_seed)
    if _digest(source_target) != source["target_program_sha256"]:
        raise CrossDomainFailureDerivedTargetError("sealed source generator reconstruction changed")
    support_digests = {_digest(item) for item in support_inputs}
    if any(_digest(value) in support_digests for value in final_inputs):
        raise CrossDomainFailureDerivedTargetError("held-back cross-domain inputs overlap expanded support")
    commitment = _digest(
        {
            "schedule_commitment": schedule["schedule_commitment"],
            "acquisition_signature": target["semantic_signature"],
            "control_signature": control["semantic_signature"],
            "history_digest": history_digest,
            "phase": "crossdomain-failure-derived-choice-v1",
        }
    )
    challenge_inputs = tuple(copy.deepcopy(value) for value in final_inputs)
    expected = tuple(execute_program(target["program"], value) for value in challenge_inputs)
    spec = {
        "name": f"crossdomain-failure-derived-{str(target['semantic_signature'])[:12]}",
        "input_domain": str(target["input_domain"]),
        "output_domain": str(target["output_domain"]),
        "examples": _examples(target),
        "probe": challenge_inputs[0],
        "expected": expected[0],
        "challenge": challenge_inputs[1],
        "challenge_expected": expected[1],
        "max_nodes": int(target["program_nodes"]),
    }

    with tempfile.TemporaryDirectory(prefix="agi-crossdomain-failure-derived-learn-") as temporary:
        learner = Path(temporary).resolve()
        _copy_persistent_state(root, learner)
        if _tree_snapshot(learner, prior_ids) != prior_tree or _trial_snapshot(learner, prior_ids) != prior_trials:
            raise CrossDomainFailureDerivedTargetError("state-only learner reconstruction changed prior state")
        if _attempt(learner, target)["supported"]:
            raise CrossDomainFailureDerivedTargetError("derived target became supported before learning")
        learned = _promote_task(learner, seed=f"{seed}:learn", spec=spec)
        learned_id = str(learned["candidate_id"])
        if learned_id in prior_ids or not learned.get("negative_evidence_retained"):
            raise CrossDomainFailureDerivedTargetError("derived Candidate identity/evidence invariant failed")
        for value, expected_value in zip(challenge_inputs, expected, strict=True):
            if execute_program(learned["program"], value) != expected_value:
                raise CrossDomainFailureDerivedTargetError("derived Candidate failed sealed cross-domain input")
        if _tree_snapshot(learner, prior_ids) != prior_tree or _trial_snapshot(learner, prior_ids) != prior_trials:
            raise CrossDomainFailureDerivedTargetError("derived learning changed prior Candidate state")
        commit = _commit_verified_candidate(
            learner, root, candidate_id=learned_id, scope=str(learned["scope"])
        )

    if _tree_snapshot(root, prior_ids) != prior_tree or _trial_snapshot(root, prior_ids) != prior_trials:
        raise CrossDomainFailureDerivedTargetError("transactional commit changed prior Candidate state")
    final_ids = _all_candidate_ids(root)
    final_tree = _tree_snapshot(root, final_ids)
    final_trials = _trial_snapshot(root, final_ids)
    if learned_id not in final_ids or not final_trials.get(learned_id):
        raise CrossDomainFailureDerivedTargetError("derived Candidate lacks durable regression trials")

    with tempfile.TemporaryDirectory(prefix="agi-crossdomain-failure-derived-resolver-") as temporary:
        resolver = Path(temporary).resolve()
        _copy_persistent_state(root, resolver)
        transfer = _solve_and_replay(
            resolver,
            target,
            required_candidate_id=learned_id,
            challenge_inputs=challenge_inputs,
        )
        base_transfer = _solve_and_replay(
            resolver,
            schedule["base_target"],
            required_candidate_id=candidate_id,
            challenge_inputs=challenge_inputs,
        )
        if _attempt(resolver, control)["supported"]:
            raise CrossDomainFailureDerivedTargetError("derived cross-domain control was overgeneralized")
        if _tree_snapshot(resolver, final_ids) != final_tree or _trial_snapshot(resolver, final_ids) != final_trials:
            raise CrossDomainFailureDerivedTargetError("fresh resolver changed durable Candidate state")
    if _tree_snapshot(root, final_ids) != final_tree or _trial_snapshot(root, final_ids) != final_trials:
        raise CrossDomainFailureDerivedTargetError("post-transfer durable state changed")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "crossdomain-failure-derived-target-v1",
        "seed": seed,
        "source_campaign_id": str(source["campaign_id"]),
        "source_candidate_id": candidate_id,
        "source_input_domain": str(source["input_domain"]),
        "source_output_domain": str(source["output_domain"]),
        "source_counterexample_count": int(source["counterexample_count"]),
        "source_negative_evidence_retained": True,
        "source_history_digest": history_digest,
        "target_generation_source": "persisted_crossdomain_counterexample_history",
        "source_seed_value_persisted": False,
        "persisted_support_input_count": len(persisted_support_inputs),
        "expanded_support_input_count": len(support_inputs),
        "support_inputs_expanded_from_persisted_history": True,
        "sealed_final_answers_exposed_to_learner": False,
        "derived_schedule_precommitted_before_support_checks": True,
        "derived_schedule_commitment": str(schedule["schedule_commitment"]),
        "support_attempts": attempts,
        "derived_target_failed_closed_before_learning": True,
        "derived_target_semantic_signature": str(target["semantic_signature"]),
        "derived_target_input_domain": str(target["input_domain"]),
        "derived_target_output_domain": str(target["output_domain"]),
        "derived_control_failed_closed_before_learning": True,
        "new_candidate_id": learned_id,
        "new_candidate_negative_evidence_retained": True,
        "transactional_commit": commit,
        "prior_candidate_state_unchanged": True,
        "prior_trial_state_unchanged": True,
        "fresh_behavior_only_transfer": transfer,
        "new_candidate_rediscovered_without_caller_ids": learned_id in transfer["solver_selected_candidate_ids"],
        "source_behavior_retention": base_transfer,
        "source_candidate_rediscovered_after_new_learning": candidate_id in base_transfer["solver_selected_candidate_ids"],
        "derived_control_gap_remained_failed_closed": True,
        "source_candidate_state_unchanged": True,
        "source_trial_state_unchanged": True,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded cross-domain evidence-driven curriculum evidence only. The next target is "
            "derived from a regression-verified learned program plus its persisted CEGIS counterexample "
            "history. Support enrichment is deterministic from persisted inputs and is committed before "
            "support checks; sealed final answers remain outside target selection and learning. The source "
            "generator, transformations, learner, evaluator, and held-out probes remain repository-authored; "
            "this is not independent production evidence, open-domain autonomous objective invention, or AGI."
        ),
    }
    if not all(
        (
            report["source_negative_evidence_retained"],
            report["support_inputs_expanded_from_persisted_history"],
            report["sealed_final_answers_exposed_to_learner"] is False,
            report["derived_schedule_precommitted_before_support_checks"],
            report["derived_target_failed_closed_before_learning"],
            report["derived_control_failed_closed_before_learning"],
            report["new_candidate_negative_evidence_retained"],
            report["prior_candidate_state_unchanged"],
            report["prior_trial_state_unchanged"],
            report["new_candidate_rediscovered_without_caller_ids"],
            report["source_candidate_rediscovered_after_new_learning"],
            report["derived_control_gap_remained_failed_closed"],
            report["source_candidate_state_unchanged"],
            report["source_trial_state_unchanged"],
        )
    ):
        raise CrossDomainFailureDerivedTargetError("cross-domain failure-derived aggregate invariant failed")
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "crossdomain-failure-derived-target"
        / f"crossdomain-derived-{_digest(seed)[:16]}.json",
        report,
    )
    return report
