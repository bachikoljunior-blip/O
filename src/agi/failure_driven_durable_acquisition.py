from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program
from agi.cross_family_order_retention import _trial_snapshots
from agi.durable_state_rehydration import _run_one, _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.transactional_multisession_commit import (
    _commit_verified_candidate,
    run_transactional_multisession_commit,
)
from agi.verified_macro_library_discovery import (
    VerifiedMacroLibraryError,
    synthesize_from_verified_macro_library,
)


class FailureDrivenDurableAcquisitionError(RuntimeError):
    pass


def _square_spec() -> dict[str, Any]:
    return {
        "name": "numeric-square-gap-fill",
        "input_domain": "numeric",
        "output_domain": "numeric",
        "examples": (
            ProgramExample(2, 4),
            ProgramExample(-4, 16),
            ProgramExample(6, 36),
        ),
        "probe": -9,
        "expected": 81,
        "max_nodes": 3,
    }


def run_failure_driven_durable_acquisition(root: Path, seed: str) -> dict[str, Any]:
    """Turn a verified durable-library gap into a new retained skill and recover after restart.

    A transactionally retained capability base is reconstructed into a fresh learner root. Before any
    new learning, the learner asks the verified durable library for a numeric square behavior without
    supplying Candidate IDs; the existing library must fail closed because no verified route matches.
    Only then does bounded program synthesis learn a square skill from disjoint examples, submit it to
    the protected exact-scope regression gate, and transactionally commit the verified Candidate into
    durable source state without changing any older Candidate bytes or trial ledgers. A second fresh
    state-only restart must discover the enlarged library without caller IDs, select the newly acquired
    square skill for different behavior examples, and transfer it to post-selection challenges through
    fresh mechanical Engines without spending more Candidate trials.

    This is repository-authored bounded failure-driven continual-learning evidence. It does not prove
    open-domain novelty detection, independent evaluation, or production AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    base = run_transactional_multisession_commit(
        root,
        f"{seed}:transactional-source",
    )
    if not base.get("passed"):
        raise FailureDrivenDurableAcquisitionError(
            "transactional durable-state prerequisite did not pass"
        )
    prior_ids = [
        *[str(value) for value in base["source_candidate_ids"]],
        str(base["first_committed_candidate_id"]),
        str(base["second_committed_candidate_id"]),
    ]
    if len(prior_ids) != len(set(prior_ids)):
        raise FailureDrivenDurableAcquisitionError(
            "durable source contains duplicate Candidate identities"
        )
    prior_tree_before = _tree_snapshot(root, prior_ids)
    prior_trials_before = _trial_snapshots(root, prior_ids)

    initial_gap_examples = (
        ProgramExample(12, 144),
        ProgramExample(-7, 49),
        ProgramExample(9, 81),
    )

    with tempfile.TemporaryDirectory(prefix="agi-failure-driven-learner-") as learner_temporary:
        learner_root = Path(learner_temporary).resolve()
        _copy_persistent_state(root, learner_root)
        if _tree_snapshot(learner_root, prior_ids) != prior_tree_before:
            raise FailureDrivenDurableAcquisitionError(
                "learner restart changed pre-existing Candidate bytes"
            )
        if _trial_snapshots(learner_root, prior_ids) != prior_trials_before:
            raise FailureDrivenDurableAcquisitionError(
                "learner restart changed pre-existing Candidate trials"
            )

        initial_gap_failed_closed = False
        try:
            synthesize_from_verified_macro_library(
                learner_root,
                input_domain="numeric",
                output_domain="numeric",
                examples=initial_gap_examples,
                max_depth=2,
                max_candidates=64,
            )
        except (VerifiedMacroLibraryError, RuntimeError):
            initial_gap_failed_closed = True
        if not initial_gap_failed_closed:
            raise FailureDrivenDurableAcquisitionError(
                "pre-learning durable library unexpectedly solved square behavior"
            )

        acquired = _promote_task(
            learner_root,
            seed=f"{seed}:gap-fill-square",
            spec=_square_spec(),
        )
        square_id = str(acquired["candidate_id"])
        square_scope = str(acquired["scope"])
        if square_id in prior_ids:
            raise FailureDrivenDurableAcquisitionError(
                "failure-driven acquisition reused an older Candidate identity"
            )
        if not bool(acquired["negative_evidence_retained"]):
            raise FailureDrivenDurableAcquisitionError(
                "new square Candidate did not retain its protected-regression counterexample"
            )
        square_trials = _trial_snapshots(learner_root, [square_id])
        if not square_trials.get(square_id):
            raise FailureDrivenDurableAcquisitionError(
                "new square Candidate lacks durable regression trials"
            )
        if _tree_snapshot(learner_root, prior_ids) != prior_tree_before:
            raise FailureDrivenDurableAcquisitionError(
                "failure-driven learning changed older Candidate bytes"
            )
        if _trial_snapshots(learner_root, prior_ids) != prior_trials_before:
            raise FailureDrivenDurableAcquisitionError(
                "failure-driven learning changed older Candidate trials"
            )

        commit = _commit_verified_candidate(
            learner_root,
            root,
            candidate_id=square_id,
            scope=square_scope,
        )

    if _tree_snapshot(root, prior_ids) != prior_tree_before:
        raise FailureDrivenDurableAcquisitionError(
            "transactional gap-fill commit changed older Candidate bytes"
        )
    if _trial_snapshots(root, prior_ids) != prior_trials_before:
        raise FailureDrivenDurableAcquisitionError(
            "transactional gap-fill commit changed older Candidate trials"
        )

    enlarged_ids = [*prior_ids, square_id]
    enlarged_tree_before = _tree_snapshot(root, enlarged_ids)
    enlarged_trials_before = _trial_snapshots(root, enlarged_ids)

    with tempfile.TemporaryDirectory(prefix="agi-failure-driven-resolver-") as resolver_temporary:
        resolver_root = Path(resolver_temporary).resolve()
        _copy_persistent_state(root, resolver_root)
        prior_runs_copied = (resolver_root / ".continual" / "runs").exists()
        prior_episodes_copied = (resolver_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (resolver_root / ".continual" / "evidence").exists()
        if _tree_snapshot(resolver_root, enlarged_ids) != enlarged_tree_before:
            raise FailureDrivenDurableAcquisitionError(
                "post-acquisition restart changed Candidate bytes"
            )
        if _trial_snapshots(resolver_root, enlarged_ids) != enlarged_trials_before:
            raise FailureDrivenDurableAcquisitionError(
                "post-acquisition restart changed Candidate trials"
            )

        retrieval_examples = (
            ProgramExample(11, 121),
            ProgramExample(-8, 64),
            ProgramExample(13, 169),
        )
        retrieved = synthesize_from_verified_macro_library(
            resolver_root,
            input_domain="numeric",
            output_domain="numeric",
            examples=retrieval_examples,
            max_depth=2,
            max_candidates=64,
        )
        if retrieved.get("candidate_ids_supplied_by_caller") is not False:
            raise FailureDrivenDurableAcquisitionError(
                "post-acquisition retrieval relied on caller Candidate IDs"
            )
        selected_ids = [str(value) for value in retrieved["selected_candidate_ids"]]
        if selected_ids != [square_id] or retrieved["selected_chain_depth"] != 1:
            raise FailureDrivenDurableAcquisitionError(
                "fresh resolver did not select the newly acquired square skill"
            )

        commitment = _digest(
            {
                "seed": seed,
                "initial_gap_examples": [
                    {"input": example.input, "output": example.output}
                    for example in initial_gap_examples
                ],
                "retrieval_digest": retrieved["digest"],
                "square_id": square_id,
                "phase": "post-retrieval-square-challenge-v1",
            }
        )
        challenge_values = (
            20 + int(commitment[:4], 16) % 60,
            -(20 + int(commitment[4:8], 16) % 60),
        )
        support_values = {2, -4, 6, -9, 12, -7, 9, 11, -8, 13}
        if set(challenge_values) & support_values:
            raise FailureDrivenDurableAcquisitionError(
                "post-retrieval challenge overlaps acquisition or retrieval support"
            )

        fresh_runs: list[str] = []
        compiled_program = retrieved["compiled_program"]
        for value in challenge_values:
            expected = value * value
            if execute_program(compiled_program, value) != expected:
                raise FailureDrivenDurableAcquisitionError(
                    "retrieved compiled square skill failed post-selection challenge"
                )
            fresh_runs.append(
                _run_one(
                    resolver_root,
                    candidate_id=square_id,
                    value=value,
                    expected=expected,
                )
            )
        if len(set(fresh_runs)) != len(fresh_runs):
            raise FailureDrivenDurableAcquisitionError(
                "post-acquisition retrieval reused a fresh Engine run"
            )
        resolver_state_unchanged = (
            _tree_snapshot(resolver_root, enlarged_ids) == enlarged_tree_before
        )
        resolver_trials_unchanged = (
            _trial_snapshots(resolver_root, enlarged_ids) == enlarged_trials_before
        )
        if not resolver_state_unchanged or not resolver_trials_unchanged:
            raise FailureDrivenDurableAcquisitionError(
                "post-acquisition retrieval changed Candidate state or trials"
            )

    source_state_unchanged_after_retrieval = (
        _tree_snapshot(root, enlarged_ids) == enlarged_tree_before
    )
    source_trials_unchanged_after_retrieval = (
        _trial_snapshots(root, enlarged_ids) == enlarged_trials_before
    )
    if not source_state_unchanged_after_retrieval or not source_trials_unchanged_after_retrieval:
        raise FailureDrivenDurableAcquisitionError(
            "fresh retrieval mutated durable source state"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "failure-driven-durable-acquisition-v1",
        "transactional_source_digest": base["digest"],
        "initial_gap_failed_closed": initial_gap_failed_closed,
        "new_candidate_id": square_id,
        "new_candidate_scope": square_scope,
        "new_candidate_negative_evidence_retained": bool(acquired["negative_evidence_retained"]),
        "transactional_commit": commit,
        "older_candidate_state_unchanged_by_learning_and_commit": (
            _tree_snapshot(root, prior_ids) == prior_tree_before
        ),
        "older_candidate_trials_unchanged_by_learning_and_commit": (
            _trial_snapshots(root, prior_ids) == prior_trials_before
        ),
        "caller_supplied_candidate_ids_for_retrieval": False,
        "retrieved_candidate_ids": selected_ids,
        "retrieved_new_candidate": selected_ids == [square_id],
        "retrieved_chain_depth": int(retrieved["selected_chain_depth"]),
        "challenge_generated_after_retrieval": True,
        "post_retrieval_challenge_commitment": commitment,
        "fresh_engine_runs": fresh_runs,
        "fresh_engine_runs_unique": len(set(fresh_runs)) == len(fresh_runs),
        "resolver_state_unchanged": resolver_state_unchanged,
        "resolver_trials_unchanged": resolver_trials_unchanged,
        "source_state_unchanged_after_retrieval": source_state_unchanged_after_retrieval,
        "source_trials_unchanged_after_retrieval": source_trials_unchanged_after_retrieval,
        "prior_runs_copied": prior_runs_copied,
        "prior_episodes_copied": prior_episodes_copied,
        "prior_evidence_copied": prior_evidence_copied,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded failure-driven continual-learning evidence only. An unsupported behavior "
            "first failed closed, then a finite safe program was learned from examples, regression-verified, "
            "transactionally committed without changing older Candidate evidence, autonomously rediscovered "
            "after a fresh state-only restart, and transferred to post-retrieval challenges. The novelty "
            "task, synthesis grammar, examples, regression measurements, and evaluator remain repository-authored, "
            "so this is not open-domain self-improvement or independent AGI evidence."
        ),
    }
    if not all(
        (
            report["initial_gap_failed_closed"],
            report["new_candidate_negative_evidence_retained"],
            report["older_candidate_state_unchanged_by_learning_and_commit"],
            report["older_candidate_trials_unchanged_by_learning_and_commit"],
            report["retrieved_new_candidate"],
            report["challenge_generated_after_retrieval"],
            report["fresh_engine_runs_unique"],
            report["resolver_state_unchanged"],
            report["resolver_trials_unchanged"],
            report["source_state_unchanged_after_retrieval"],
            report["source_trials_unchanged_after_retrieval"],
            not report["prior_runs_copied"],
            not report["prior_episodes_copied"],
            not report["prior_evidence_copied"],
        )
    ):
        raise FailureDrivenDurableAcquisitionError(
            "failure-driven acquisition aggregate invariant failed"
        )
    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "failure-driven-durable-acquisition"
        / f"acquisition-{_digest(seed)[:16]}.json",
        report,
    )
    return report
