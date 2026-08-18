from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program
from agi.autonomous_heterogeneous_goal_acquisition import (
    _classify_goals,
    _library_attempt,
)
from agi.autonomous_heterogeneous_goal_exhaustion import (
    run_autonomous_heterogeneous_goal_exhaustion,
)
from agi.durable_state_rehydration import _run_one, _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.transactional_multisession_commit import _commit_verified_candidate
from agi.verified_macro_library_discovery import synthesize_from_verified_macro_library


class SeededGeneratedGoalAcquisitionError(RuntimeError):
    pass


def _generated_goal_specs(seed: str) -> tuple[dict[str, Any], ...]:
    """Instantiate a bounded heterogeneous goal set from a seed commitment.

    The task *families* remain repository-authored, but constants, object keys, examples, probes, and
    challenges are derived from the supplied seed rather than named by a caller. This makes the exact
    behavior instances unavailable until the seed is fixed while keeping synthesis and evaluation fully
    deterministic and resource-bounded.
    """

    digest = _digest({"seed": seed, "phase": "generated-goal-specs-v1"})
    scale = 2 + int(digest[0:2], 16) % 5
    offset = 2 + int(digest[2:4], 16) % 7
    object_key = f"k_{digest[4:12]}"
    pair_key = f"v_{digest[12:20]}"
    sequence_bias = 1 + int(digest[20:22], 16) % 4

    return (
        {
            "name": f"generated-scale-{scale}",
            "input_domain": "numeric",
            "output_domain": "numeric",
            "examples": (
                ProgramExample(1, scale),
                ProgramExample(-2, -2 * scale),
                ProgramExample(3, 3 * scale),
            ),
            "probe": 7,
            "expected": 7 * scale,
            "challenge": -9,
            "challenge_expected": -9 * scale,
            "max_nodes": 3,
            "generated_parameter": scale,
        },
        {
            "name": f"generated-offset-{offset}",
            "input_domain": "numeric",
            "output_domain": "numeric",
            "examples": (
                ProgramExample(0, offset),
                ProgramExample(2, 2 + offset),
                ProgramExample(-3, -3 + offset),
            ),
            "probe": 11,
            "expected": 11 + offset,
            "challenge": -8,
            "challenge_expected": -8 + offset,
            "max_nodes": 3,
            "generated_parameter": offset,
        },
        {
            "name": f"generated-has-key-{object_key}",
            "input_domain": "object",
            "output_domain": "boolean",
            "examples": (
                ProgramExample({object_key: 1, "noise": 0}, True),
                ProgramExample({"noise": 1}, False),
                ProgramExample({object_key: False, "other": "x"}, True),
            ),
            "probe": {object_key: "fresh", "novel": 1},
            "expected": True,
            "challenge": {"noise": 99, object_key: None},
            "challenge_expected": True,
            "max_nodes": 2,
            "generated_parameter": object_key,
        },
        {
            "name": f"generated-object-pair-{pair_key}",
            "input_domain": "numeric",
            "output_domain": "object",
            "examples": (
                ProgramExample(2, {pair_key: 2}),
                ProgramExample(-5, {pair_key: -5}),
                ProgramExample(0, {pair_key: 0}),
            ),
            "probe": 13,
            "expected": {pair_key: 13},
            "challenge": -21,
            "challenge_expected": {pair_key: -21},
            "max_nodes": 2,
            "generated_parameter": pair_key,
        },
        {
            "name": f"generated-sequence-sum-bias-{sequence_bias}",
            "input_domain": "sequence",
            "output_domain": "numeric",
            "examples": (
                ProgramExample([1, 2], 3 + sequence_bias),
                ProgramExample([], sequence_bias),
                ProgramExample([5, -2, 4], 7 + sequence_bias),
            ),
            "probe": [10, -3, 2],
            "expected": 9 + sequence_bias,
            "challenge": [-4, 6, 8],
            "challenge_expected": 10 + sequence_bias,
            "max_nodes": 4,
            "generated_parameter": sequence_bias,
        },
    )


def _assert_state(
    root: Path,
    candidate_ids: Sequence[str],
    tree: dict[str, str],
    trials: dict[str, dict[str, str]],
    *,
    label: str,
) -> None:
    ids = [str(value) for value in candidate_ids]
    if _tree_snapshot(root, ids) != tree:
        raise SeededGeneratedGoalAcquisitionError(
            f"{label} changed protected Candidate bytes"
        )
    if _trial_snapshot(root, ids) != trials:
        raise SeededGeneratedGoalAcquisitionError(
            f"{label} changed protected Candidate trial ledgers"
        )


def run_seeded_generated_goal_acquisition(root: Path, seed: str) -> dict[str, Any]:
    """Generate fresh bounded behavior instances and autonomously close one observed capability gap.

    A prerequisite closed-loop curriculum first exhausts its fixed heterogeneous goal set. The system
    then derives a new heterogeneous set of exact behavior instances from a seed commitment: numeric
    scale/offset, generated object-key presence/construction, and sequence aggregation with a generated
    bias. A fresh state-only decision root receives no Candidate IDs and classifies those generated goals
    against only the durable verified macro library. It must expose multiple fail-closed gaps, select one
    solely from that observed set, learn it in a separate fresh root through bounded synthesis plus
    protected exact-scope regression, transactionally retain it, and solve a held-back challenge after a
    further restart. At least one other generated gap must remain unsupported so one acquisition cannot
    be mislabeled as general coverage.

    Goal families, generation algorithm, grammar, regression, search, and evaluator remain repository-
    authored. This is internal bounded generated-task adaptation evidence, not independent AGI evidence.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    prerequisite = run_autonomous_heterogeneous_goal_exhaustion(
        root,
        f"{seed}:fixed-goal-prerequisite",
    )
    if not prerequisite.get("passed") or not prerequisite.get("bounded_goal_set_exhausted"):
        raise SeededGeneratedGoalAcquisitionError(
            "fixed-goal autonomous prerequisite did not pass"
        )

    prior_ids = _all_candidate_ids(root)
    prior_tree = _tree_snapshot(root, prior_ids)
    prior_trials = _trial_snapshot(root, prior_ids)
    specs = _generated_goal_specs(seed)
    generated_spec_digest = _digest(
        [
            {
                "name": spec["name"],
                "input_domain": spec["input_domain"],
                "output_domain": spec["output_domain"],
                "examples": [
                    {"input": item.input, "output": item.output}
                    for item in spec["examples"]
                ],
                "probe": spec["probe"],
                "expected": spec["expected"],
                "challenge": spec["challenge"],
                "challenge_expected": spec["challenge_expected"],
                "generated_parameter": spec["generated_parameter"],
            }
            for spec in specs
        ]
    )

    with tempfile.TemporaryDirectory(prefix="agi-generated-goal-decision-") as temporary:
        decision_root = Path(temporary).resolve()
        _copy_persistent_state(root, decision_root)
        _assert_state(
            decision_root,
            prior_ids,
            prior_tree,
            prior_trials,
            label="generated-goal decision restart",
        )
        initial_classification = _classify_goals(decision_root, specs)
        if any(
            item.get("candidate_ids_supplied_by_caller") is True
            for item in initial_classification
        ):
            raise SeededGeneratedGoalAcquisitionError(
                "generated-goal classification relied on caller Candidate IDs"
            )
        unsupported = sorted(
            str(item["goal"])
            for item in initial_classification
            if not item["supported"]
        )
        if len(unsupported) < 2:
            raise SeededGeneratedGoalAcquisitionError(
                "generated goal set did not expose multiple unsupported behaviors"
            )

    selection_commitment = _digest(
        {
            "seed": seed,
            "generated_spec_digest": generated_spec_digest,
            "unsupported_goals": unsupported,
            "initial_classification": initial_classification,
            "phase": "seeded-generated-goal-choice-v1",
        }
    )
    selected_name = unsupported[
        int(selection_commitment[:8], 16) % len(unsupported)
    ]
    selected_spec = next(spec for spec in specs if spec["name"] == selected_name)

    with tempfile.TemporaryDirectory(prefix="agi-generated-goal-learner-") as temporary:
        learner_root = Path(temporary).resolve()
        _copy_persistent_state(root, learner_root)
        _assert_state(
            learner_root,
            prior_ids,
            prior_tree,
            prior_trials,
            label="generated-goal learner restart",
        )
        if _library_attempt(learner_root, selected_spec)["supported"]:
            raise SeededGeneratedGoalAcquisitionError(
                "selected generated goal became supported before learning"
            )
        acquired = _promote_task(
            learner_root,
            seed=f"{seed}:generated:{selected_name}",
            spec=selected_spec,
        )
        candidate_id = str(acquired["candidate_id"])
        scope = str(acquired["scope"])
        if candidate_id in prior_ids:
            raise SeededGeneratedGoalAcquisitionError(
                "generated-goal learning reused a prior Candidate identity"
            )
        if not acquired.get("negative_evidence_retained"):
            raise SeededGeneratedGoalAcquisitionError(
                "generated-goal Candidate did not retain protected negative evidence"
            )
        candidate_trials = _trial_snapshot(learner_root, [candidate_id])
        if not candidate_trials.get(candidate_id):
            raise SeededGeneratedGoalAcquisitionError(
                "generated-goal Candidate lacks durable regression trials"
            )
        _assert_state(
            learner_root,
            prior_ids,
            prior_tree,
            prior_trials,
            label="generated-goal learning",
        )
        commit = _commit_verified_candidate(
            learner_root,
            root,
            candidate_id=candidate_id,
            scope=scope,
        )

    _assert_state(
        root,
        prior_ids,
        prior_tree,
        prior_trials,
        label="generated-goal transactional commit",
    )
    enlarged_ids = [*prior_ids, candidate_id]
    enlarged_tree = _tree_snapshot(root, enlarged_ids)
    enlarged_trials = _trial_snapshot(root, enlarged_ids)

    with tempfile.TemporaryDirectory(prefix="agi-generated-goal-resolver-") as temporary:
        resolver_root = Path(temporary).resolve()
        _copy_persistent_state(root, resolver_root)
        _assert_state(
            resolver_root,
            enlarged_ids,
            enlarged_tree,
            enlarged_trials,
            label="generated-goal resolver restart",
        )
        selected_result = synthesize_from_verified_macro_library(
            resolver_root,
            input_domain=str(selected_spec["input_domain"]),
            output_domain=str(selected_spec["output_domain"]),
            examples=selected_spec["examples"],
            max_depth=3,
            max_candidates=64,
        )
        if selected_result["candidate_ids_supplied_by_caller"]:
            raise SeededGeneratedGoalAcquisitionError(
                "generated-goal replay relied on caller Candidate IDs"
            )
        selected_ids = [
            str(value) for value in selected_result["selected_candidate_ids"]
        ]
        if candidate_id not in selected_ids:
            raise SeededGeneratedGoalAcquisitionError(
                "fresh resolver did not rediscover the generated-goal Candidate"
            )
        if execute_program(
            selected_result["compiled_program"], selected_spec["challenge"]
        ) != selected_spec["challenge_expected"]:
            raise SeededGeneratedGoalAcquisitionError(
                "generated-goal compiled skill failed held-back challenge"
            )
        fresh_run = _run_one(
            resolver_root,
            candidate_id=candidate_id,
            value=selected_spec["challenge"],
            expected=selected_spec["challenge_expected"],
        )
        post_classification = _classify_goals(resolver_root, specs)
        by_goal = {str(item["goal"]): item for item in post_classification}
        if not by_goal[selected_name]["supported"]:
            raise SeededGeneratedGoalAcquisitionError(
                "selected generated goal remained unsupported after learning"
            )
        remaining_unsupported = sorted(
            name
            for name in unsupported
            if name != selected_name and not by_goal[name]["supported"]
        )
        if not remaining_unsupported:
            raise SeededGeneratedGoalAcquisitionError(
                "one generated-goal acquisition unexpectedly erased every generated gap"
            )
        _assert_state(
            resolver_root,
            enlarged_ids,
            enlarged_tree,
            enlarged_trials,
            label="generated-goal replay",
        )

    _assert_state(
        root,
        enlarged_ids,
        enlarged_tree,
        enlarged_trials,
        label="source after generated-goal replay",
    )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "seeded-generated-goal-acquisition-v1",
        "fixed_goal_prerequisite_digest": str(prerequisite["digest"]),
        "generated_goal_count": len(specs),
        "generated_goal_names": [str(spec["name"]) for spec in specs],
        "generated_goal_domains": [
            f"{spec['input_domain']}->{spec['output_domain']}" for spec in specs
        ],
        "generated_parameters": [spec["generated_parameter"] for spec in specs],
        "generated_spec_digest": generated_spec_digest,
        "initial_classification": initial_classification,
        "initial_unsupported_goals": unsupported,
        "initial_unsupported_count": len(unsupported),
        "selection_commitment": selection_commitment,
        "selected_goal": selected_name,
        "selected_from_unsupported_only": selected_name in unsupported,
        "selected_candidate_id": candidate_id,
        "selected_scope": scope,
        "selected_negative_evidence_retained": bool(
            acquired["negative_evidence_retained"]
        ),
        "transactional_commit": commit,
        "caller_supplied_candidate_ids": False,
        "post_acquisition_selected_candidate_ids": selected_ids,
        "post_classification": post_classification,
        "remaining_unsupported_goals": remaining_unsupported,
        "remaining_unsupported_count": len(remaining_unsupported),
        "fresh_engine_run": fresh_run,
        "prior_state_unchanged": _tree_snapshot(root, prior_ids) == prior_tree,
        "prior_trials_unchanged": _trial_snapshot(root, prior_ids) == prior_trials,
        "all_state_unchanged_after_fresh_replay": _tree_snapshot(root, enlarged_ids)
        == enlarged_tree,
        "all_trials_unchanged_after_fresh_replay": _trial_snapshot(root, enlarged_ids)
        == enlarged_trials,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded seed-generated task adaptation evidence only. Exact task constants, keys, "
            "examples, probes, and challenges were generated after a seed commitment, and the system "
            "selected one fail-closed generated gap from durable-library classification without caller "
            "Candidate IDs. Task families, generator, grammar, regression, search, scoring, and evaluator "
            "remain repository-authored; this is not independent production evidence and does not "
            "establish AGI."
        ),
    }
    if not all(
        (
            report["selected_from_unsupported_only"],
            report["selected_negative_evidence_retained"],
            report["remaining_unsupported_count"] >= 1,
            report["prior_state_unchanged"],
            report["prior_trials_unchanged"],
            report["all_state_unchanged_after_fresh_replay"],
            report["all_trials_unchanged_after_fresh_replay"],
        )
    ):
        raise SeededGeneratedGoalAcquisitionError(
            "seeded generated-goal aggregate invariant failed"
        )

    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "seeded-generated-goal-acquisition"
        / f"generated-goal-{_digest(seed)[:16]}.json",
        report,
    )
    return report
