from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program
from agi.adaptive_depth_composition import (
    _challenge_inputs,
    run_adaptive_depth_composition,
    synthesize_shallowest_verified_route,
)
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.cross_domain_seed_gap_acquisition import (
    _library_attempt as _string_library_attempt,
    _numeric_retention_examples,
    _precommit_string_gap_schedule,
    _string_challenges,
)
from agi.durable_state_rehydration import _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.seed_committed_gap_acquisition import (
    _library_attempt as _numeric_library_attempt,
    _precommit_gap_schedule,
)
from agi.sequence_seed_gap_acquisition import (
    _library_attempt as _sequence_library_attempt,
    _precommit_sequence_gap_schedule,
    _sequence_challenges,
    _string_retention_examples,
    run_sequence_seed_gap_acquisition,
)
from agi.transactional_multisession_commit import _commit_verified_candidate
from agi.verified_macro_synthesis import _load_verified_macro_items


class ObservedGapDomainCurriculumError(RuntimeError):
    pass


_DOMAINS = ("numeric", "string", "sequence")


def _target_examples(target: Mapping[str, Any]) -> tuple[ProgramExample, ...]:
    return tuple(
        ProgramExample(item["input"], item["output"])
        for item in target["support_examples"]
    )


def _attempt_domain(
    root: Path,
    domain: str,
    examples: Sequence[ProgramExample],
) -> dict[str, Any]:
    if domain == "numeric":
        return _numeric_library_attempt(root, examples)
    if domain == "string":
        return _string_library_attempt(root, examples)
    if domain == "sequence":
        return _sequence_library_attempt(root, examples)
    raise ValueError(f"unsupported domain: {domain}")


def _precommit_domain_schedules(root: Path, seed: str) -> dict[str, Any]:
    """Commit the candidate target sources before any durable-library support observation."""

    numeric = _precommit_gap_schedule(
        root,
        f"{seed}:numeric",
        max_target_attempts=4,
    )
    numeric["domain"] = "numeric"
    string = _precommit_string_gap_schedule(
        f"{seed}:string",
        max_target_attempts=4,
    )
    sequence = _precommit_sequence_gap_schedule(
        f"{seed}:sequence",
        max_target_attempts=3,
    )
    schedules = {
        "numeric": numeric,
        "string": string,
        "sequence": sequence,
    }
    commitment = _digest(
        {
            "seed": seed,
            "schedule_commitments": {
                domain: str(schedules[domain]["schedule_commitment"])
                for domain in _DOMAINS
            },
            "phase": "observed-gap-domain-curriculum-precommit-v1",
        }
    )
    return {
        "schedules": schedules,
        "domain_schedule_commitment": commitment,
        "domains": list(_DOMAINS),
        "precommitted_before_support_checks": True,
    }


def _select_domain_from_observed_gaps(
    attempts_by_domain: Mapping[str, Sequence[Mapping[str, Any]]],
    domain_schedule_commitment: str,
) -> dict[str, Any]:
    """Select an eligible domain by observed unsupported behavior, not a fixed domain order.

    A domain is eligible only after at least two of its precommitted targets fail closed, leaving one
    target for acquisition and one untouched control. The primary score is the observed unsupported
    count. Ties are broken by a commitment-bound domain rank fixed before support observations.
    """

    if not isinstance(domain_schedule_commitment, str) or not domain_schedule_commitment:
        raise ValueError("domain_schedule_commitment must be non-empty")
    if set(attempts_by_domain) != set(_DOMAINS):
        raise ValueError("attempts_by_domain must contain numeric, string, and sequence")

    tie_order = sorted(
        _DOMAINS,
        key=lambda domain: _digest(
            {
                "domain_schedule_commitment": domain_schedule_commitment,
                "domain": domain,
                "phase": "observed-gap-domain-tiebreak-v1",
            }
        ),
    )
    tie_rank = {domain: index for index, domain in enumerate(tie_order)}
    scoreboard: dict[str, dict[str, Any]] = {}
    eligible: list[str] = []
    for domain in _DOMAINS:
        rows = list(attempts_by_domain[domain])
        if not rows:
            raise ValueError(f"domain {domain} has no support observations")
        unsupported = sum(
            1 for row in rows if not bool(row.get("supported_before_learning"))
        )
        scoreboard[domain] = {
            "observed_target_count": len(rows),
            "unsupported_count": unsupported,
            "precommitted_tie_rank": tie_rank[domain],
            "eligible_for_acquisition": unsupported >= 2,
        }
        if unsupported >= 2:
            eligible.append(domain)
    if not eligible:
        raise ObservedGapDomainCurriculumError(
            "no precommitted domain exposed two fail-closed targets"
        )

    selected = min(
        eligible,
        key=lambda domain: (
            -int(scoreboard[domain]["unsupported_count"]),
            int(scoreboard[domain]["precommitted_tie_rank"]),
        ),
    )
    return {
        "selected_domain": selected,
        "scoreboard": scoreboard,
        "precommitted_tie_order": tie_order,
        "selection_uses_observed_support": True,
        "fixed_domain_order_used": False,
    }


def _domain_challenges(domain: str, commitment: str) -> tuple[Any, ...]:
    if domain == "numeric":
        return tuple(_challenge_inputs("numeric", commitment))
    if domain == "string":
        return tuple(_string_challenges(commitment))
    if domain == "sequence":
        return tuple(_sequence_challenges(commitment))
    raise ValueError(f"unsupported domain: {domain}")


def _solve_examples(
    root: Path,
    *,
    domain: str,
    examples: Sequence[ProgramExample],
) -> dict[str, Any]:
    return synthesize_shallowest_verified_route(
        root,
        input_domain=domain,
        output_domain=domain,
        examples=examples,
        max_depth=4,
        max_candidates=192,
        max_search_nodes=4096,
        max_behavior_evaluations=2048,
    )


def _verify_retained_examples(
    root: Path,
    *,
    domain: str,
    examples: Sequence[ProgramExample],
) -> dict[str, Any]:
    solved = _solve_examples(root, domain=domain, examples=examples)
    if solved.get("candidate_ids_supplied_by_caller") is not False:
        raise ObservedGapDomainCurriculumError(
            f"{domain} retention replay relied on caller Candidate IDs"
        )
    for example in examples:
        if execute_program(solved["compiled_program"], example.input) != example.output:
            raise ObservedGapDomainCurriculumError(
                f"{domain} retention replay changed prior behavior"
            )
    return solved


def run_observed_gap_domain_curriculum(root: Path, seed: str) -> dict[str, Any]:
    """Choose the next bounded learning domain from observed durable gaps and retain prior domains.

    The campaign first establishes the verified numeric/string/sequence durable base. It then commits
    target schedules for all three domains before querying support. Exactly three precommitted targets
    per domain are observed. The domain with the most observed fail-closed targets is selected, with a
    commitment-bound tie breaker rather than a fixed numeric->string->sequence order. One unsupported
    target is learned through the existing exact-scope regression path and transactional commit while a
    second target in the selected domain remains an untouched control.

    A fresh state-only resolver must rediscover the new skill without caller Candidate IDs, replay held-
    back semantics through fresh Engine runs, independently retain the earlier numeric, string, and
    sequence skills, preserve all Candidate bytes and trial ledgers, and leave the selected control gap
    fail-closed. This remains repository-authored bounded internal development evidence, not AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    base = run_adaptive_depth_composition(root, f"{seed}:adaptive-base")
    if not base.get("passed"):
        raise ObservedGapDomainCurriculumError("adaptive base prerequisite did not pass")
    three_domain = run_sequence_seed_gap_acquisition(
        root,
        f"{seed}:three-domain-prerequisite",
        max_target_attempts=3,
    )
    if not three_domain.get("passed"):
        raise ObservedGapDomainCurriculumError("three-domain prerequisite did not pass")

    numeric_candidate_id = str(three_domain["numeric_prerequisite_candidate_id"])
    string_candidate_id = str(three_domain["string_prerequisite_candidate_id"])
    sequence_candidate_id = str(three_domain["candidate_id"])
    prior_ids = _all_candidate_ids(root)
    prior_tree = _tree_snapshot(root, prior_ids)
    prior_trials = _trial_snapshot(root, prior_ids)
    for required in (numeric_candidate_id, string_candidate_id, sequence_candidate_id):
        if required not in prior_ids:
            raise ObservedGapDomainCurriculumError(
                f"three-domain prerequisite Candidate is not durable: {required}"
            )
    if not prior_ids or not all(prior_trials.get(candidate_id) for candidate_id in prior_ids):
        raise ObservedGapDomainCurriculumError("source library lacks durable Candidate trials")

    precommit = _precommit_domain_schedules(root, seed)
    schedules = precommit["schedules"]
    attempts_by_domain: dict[str, list[dict[str, Any]]] = {}
    unsupported_by_domain: dict[str, list[dict[str, Any]]] = {}
    for domain in _DOMAINS:
        rows: list[dict[str, Any]] = []
        unsupported: list[dict[str, Any]] = []
        targets = list(schedules[domain]["target_schedule"])[:3]
        if len(targets) != 3:
            raise ObservedGapDomainCurriculumError(
                f"precommitted {domain} schedule did not expose three targets"
            )
        for target in targets:
            examples = _target_examples(target)
            attempt = _attempt_domain(root, domain, examples)
            row = {
                "target_index": int(target["target_index"]),
                "behavior_fingerprint": str(target["behavior_fingerprint"]),
                "supported_before_learning": bool(attempt["supported"]),
                "candidate_ids_supplied_by_caller": bool(
                    attempt["candidate_ids_supplied_by_caller"]
                ),
            }
            if "failure" in attempt:
                row["fail_closed_reason"] = str(attempt["failure"])
            rows.append(row)
            if not attempt["supported"]:
                unsupported.append(dict(target))
        attempts_by_domain[domain] = rows
        unsupported_by_domain[domain] = unsupported

    selection = _select_domain_from_observed_gaps(
        attempts_by_domain,
        str(precommit["domain_schedule_commitment"]),
    )
    selected_domain = str(selection["selected_domain"])
    selected_unsupported = unsupported_by_domain[selected_domain]
    if len(selected_unsupported) < 2:
        raise ObservedGapDomainCurriculumError(
            "selected domain lost its acquisition/control pair"
        )
    acquisition_target = selected_unsupported[0]
    control_target = selected_unsupported[1]
    acquisition_examples = _target_examples(acquisition_target)
    control_examples = _target_examples(control_target)
    choice_commitment = _digest(
        {
            "domain_schedule_commitment": precommit["domain_schedule_commitment"],
            "selected_domain": selected_domain,
            "acquisition_fingerprint": acquisition_target["behavior_fingerprint"],
            "control_fingerprint": control_target["behavior_fingerprint"],
            "phase": "observed-gap-domain-choice-v1",
        }
    )
    challenge_inputs = _domain_challenges(selected_domain, choice_commitment)
    if len(challenge_inputs) < 2:
        raise ObservedGapDomainCurriculumError("selected domain produced fewer than two challenges")
    hidden_program = acquisition_target["program"]
    challenge_expected = tuple(
        execute_program(hidden_program, value) for value in challenge_inputs
    )
    spec = {
        "name": (
            f"observed-gap-{selected_domain}-"
            f"{str(acquisition_target['behavior_fingerprint'])[:12]}"
        ),
        "input_domain": selected_domain,
        "output_domain": selected_domain,
        "examples": acquisition_examples,
        "probe": challenge_inputs[0],
        "expected": challenge_expected[0],
        "challenge": challenge_inputs[1],
        "challenge_expected": challenge_expected[1],
        "max_nodes": int(acquisition_target["program_nodes"]),
    }

    with tempfile.TemporaryDirectory(prefix="agi-observed-gap-domain-learner-") as temporary:
        learner_root = Path(temporary).resolve()
        _copy_persistent_state(root, learner_root)
        if _tree_snapshot(learner_root, prior_ids) != prior_tree:
            raise ObservedGapDomainCurriculumError("learner restart changed prior Candidate bytes")
        if _trial_snapshot(learner_root, prior_ids) != prior_trials:
            raise ObservedGapDomainCurriculumError("learner restart changed prior Candidate trials")
        if _attempt_domain(learner_root, selected_domain, acquisition_examples)["supported"]:
            raise ObservedGapDomainCurriculumError(
                "selected target became supported before learning"
            )

        acquired = _promote_task(
            learner_root,
            seed=f"{seed}:observed-gap-acquisition:{selected_domain}",
            spec=spec,
        )
        candidate_id = str(acquired["candidate_id"])
        scope = str(acquired["scope"])
        if candidate_id in prior_ids:
            raise ObservedGapDomainCurriculumError(
                "observed-gap acquisition reused a prior Candidate identity"
            )
        if not acquired.get("negative_evidence_retained"):
            raise ObservedGapDomainCurriculumError(
                "observed-gap acquisition lost protected negative evidence"
            )
        for value, expected in zip(challenge_inputs, challenge_expected, strict=True):
            if execute_program(acquired["program"], value) != expected:
                raise ObservedGapDomainCurriculumError(
                    "learned selected-domain program failed held-back semantics"
                )
        if _tree_snapshot(learner_root, prior_ids) != prior_tree:
            raise ObservedGapDomainCurriculumError("learning changed prior Candidate bytes")
        if _trial_snapshot(learner_root, prior_ids) != prior_trials:
            raise ObservedGapDomainCurriculumError("learning changed prior Candidate trials")
        commit = _commit_verified_candidate(
            learner_root,
            root,
            candidate_id=candidate_id,
            scope=scope,
        )

    if _tree_snapshot(root, prior_ids) != prior_tree:
        raise ObservedGapDomainCurriculumError("transactional commit changed prior Candidate bytes")
    if _trial_snapshot(root, prior_ids) != prior_trials:
        raise ObservedGapDomainCurriculumError("transactional commit changed prior Candidate trials")
    enlarged_ids = [*prior_ids, candidate_id]
    enlarged_tree = _tree_snapshot(root, enlarged_ids)
    enlarged_trials = _trial_snapshot(root, enlarged_ids)
    if not enlarged_trials.get(candidate_id):
        raise ObservedGapDomainCurriculumError("new Candidate lacks durable trial evidence")

    with tempfile.TemporaryDirectory(prefix="agi-observed-gap-domain-resolver-") as temporary:
        resolver_root = Path(temporary).resolve()
        _copy_persistent_state(root, resolver_root)
        prior_runs_copied = (resolver_root / ".continual" / "runs").exists()
        prior_episodes_copied = (resolver_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (resolver_root / ".continual" / "evidence").exists()
        if _tree_snapshot(resolver_root, enlarged_ids) != enlarged_tree:
            raise ObservedGapDomainCurriculumError("resolver restart changed Candidate bytes")
        if _trial_snapshot(resolver_root, enlarged_ids) != enlarged_trials:
            raise ObservedGapDomainCurriculumError("resolver restart changed Candidate trials")

        solved = _solve_examples(
            resolver_root,
            domain=selected_domain,
            examples=acquisition_examples,
        )
        selected_ids = [str(value) for value in solved["selected_candidate_ids"]]
        if solved.get("candidate_ids_supplied_by_caller") is not False:
            raise ObservedGapDomainCurriculumError("new-skill resolver used caller Candidate IDs")
        if candidate_id not in selected_ids:
            raise ObservedGapDomainCurriculumError("new-skill resolver omitted the new Candidate")
        selected_items = tuple(_load_verified_macro_items(resolver_root, selected_ids))
        fresh_run_ids: list[str] = []
        heldback_outputs: list[dict[str, Any]] = []
        for value, expected in zip(challenge_inputs, challenge_expected, strict=True):
            compiled_output = execute_program(solved["compiled_program"], value)
            runtime_output, run_id, refs = _execute_chain(resolver_root, selected_items, value)
            if compiled_output != expected or runtime_output != expected:
                raise ObservedGapDomainCurriculumError(
                    "fresh selected-domain replay failed held-back semantics"
                )
            if len(refs) != int(solved["selected_chain_depth"]):
                raise ObservedGapDomainCurriculumError(
                    "fresh selected-domain replay used wrong learned-stage count"
                )
            fresh_run_ids.append(run_id)
            heldback_outputs.append(
                {"input": value, "expected": expected, "output": runtime_output}
            )
        if len(set(fresh_run_ids)) != len(fresh_run_ids):
            raise ObservedGapDomainCurriculumError("held-back replay reused an Engine run")

        numeric_examples = _numeric_retention_examples(
            resolver_root,
            numeric_candidate_id,
            f"{seed}:numeric-retention",
        )
        numeric_retention = _verify_retained_examples(
            resolver_root,
            domain="numeric",
            examples=numeric_examples,
        )
        string_examples = _string_retention_examples(
            resolver_root,
            string_candidate_id,
            f"{seed}:string-retention",
        )
        string_retention = _verify_retained_examples(
            resolver_root,
            domain="string",
            examples=string_examples,
        )
        sequence_examples = tuple(
            ProgramExample(item["input"], item["expected"])
            for item in three_domain["heldback_challenge_outputs"]
        )
        sequence_retention = _verify_retained_examples(
            resolver_root,
            domain="sequence",
            examples=sequence_examples,
        )

        control_attempt = _attempt_domain(resolver_root, selected_domain, control_examples)
        if control_attempt["supported"]:
            raise ObservedGapDomainCurriculumError(
                "one selected-domain acquisition unexpectedly solved the untouched control gap"
            )
        if _tree_snapshot(resolver_root, enlarged_ids) != enlarged_tree:
            raise ObservedGapDomainCurriculumError("fresh validation changed Candidate bytes")
        if _trial_snapshot(resolver_root, enlarged_ids) != enlarged_trials:
            raise ObservedGapDomainCurriculumError("fresh validation changed Candidate trials")

    if _tree_snapshot(root, enlarged_ids) != enlarged_tree:
        raise ObservedGapDomainCurriculumError("fresh validation mutated source Candidate bytes")
    if _trial_snapshot(root, enlarged_ids) != enlarged_trials:
        raise ObservedGapDomainCurriculumError("fresh validation mutated source Candidate trials")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "observed-gap-domain-curriculum-v1",
        "seed": seed,
        "source_domains": list(_DOMAINS),
        "domain_target_schedules_precommitted_before_support_checks": bool(
            precommit["precommitted_before_support_checks"]
        ),
        "domain_schedule_commitment": str(precommit["domain_schedule_commitment"]),
        "support_attempts_by_domain": attempts_by_domain,
        "domain_selection": selection,
        "selected_domain": selected_domain,
        "fixed_domain_order_used": False,
        "selection_uses_observed_support": True,
        "acquisition_target_index": int(acquisition_target["target_index"]),
        "control_target_index": int(control_target["target_index"]),
        "acquisition_target_failed_closed_before_learning": True,
        "control_target_failed_closed_before_learning": True,
        "numeric_prerequisite_candidate_id": numeric_candidate_id,
        "string_prerequisite_candidate_id": string_candidate_id,
        "sequence_prerequisite_candidate_id": sequence_candidate_id,
        "candidate_id": candidate_id,
        "scope": scope,
        "new_candidate_negative_evidence_retained": True,
        "transactional_commit": commit,
        "solver_candidate_ids_supplied_by_caller": False,
        "solver_selected_candidate_ids": selected_ids,
        "new_candidate_rediscovered": candidate_id in selected_ids,
        "heldback_outputs": heldback_outputs,
        "fresh_engine_runs": fresh_run_ids,
        "fresh_engine_runs_unique": len(set(fresh_run_ids)) == len(fresh_run_ids),
        "numeric_prerequisite_retained": bool(numeric_retention["selected_candidate_ids"]),
        "string_prerequisite_retained": bool(string_retention["selected_candidate_ids"]),
        "sequence_prerequisite_retained": bool(sequence_retention["selected_candidate_ids"]),
        "control_gap_remained_failed_closed": True,
        "prior_candidate_state_unchanged": True,
        "prior_trial_state_unchanged": True,
        "source_candidate_state_unchanged": True,
        "source_trial_state_unchanged": True,
        "prior_runs_copied": prior_runs_copied,
        "prior_episodes_copied": prior_episodes_copied,
        "prior_evidence_copied": prior_evidence_copied,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded observed-gap curriculum evidence only. The next learning domain is chosen "
            "from support failures against three precommitted repository-authored target sources rather "
            "than a fixed domain order, but generators, grammars, synthesis, regression, persistence, "
            "runtime, scoring, and evaluator remain repository-authored. This does not establish open-"
            "domain autonomous curriculum learning, independent production evaluation, or AGI."
        ),
    }
    if not all(
        (
            report["domain_target_schedules_precommitted_before_support_checks"],
            report["fixed_domain_order_used"] is False,
            report["selection_uses_observed_support"],
            report["acquisition_target_failed_closed_before_learning"],
            report["control_target_failed_closed_before_learning"],
            report["new_candidate_negative_evidence_retained"],
            report["new_candidate_rediscovered"],
            report["fresh_engine_runs_unique"],
            report["numeric_prerequisite_retained"],
            report["string_prerequisite_retained"],
            report["sequence_prerequisite_retained"],
            report["control_gap_remained_failed_closed"],
            report["prior_candidate_state_unchanged"],
            report["prior_trial_state_unchanged"],
            not report["prior_runs_copied"],
            not report["prior_episodes_copied"],
            not report["prior_evidence_copied"],
        )
    ):
        raise ObservedGapDomainCurriculumError(
            "observed-gap domain curriculum aggregate invariant failed"
        )

    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "observed-gap-domain-curriculum"
        / f"observed-gap-{_digest(seed)[:16]}.json",
        report,
    )
    return report
