from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program
from agi.durable_state_rehydration import _run_one, _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.transactional_multisession_commit import (
    _commit_verified_candidate,
    run_transactional_multisession_commit,
)
from agi.verified_macro_library_discovery import (
    VerifiedMacroLibraryError,
    synthesize_from_verified_macro_library,
)


class AutonomousHeterogeneousGoalError(RuntimeError):
    pass


def _goal_specs() -> tuple[dict[str, Any], ...]:
    return (
        {
            "name": "string-reverse",
            "input_domain": "string",
            "output_domain": "string",
            "examples": (
                ProgramExample("ab", "ba"),
                ProgramExample("Agent", "tnegA"),
                ProgramExample("xyz", "zyx"),
            ),
            "probe": "Novel",
            "expected": "levoN",
            "challenge": "Fresh 42",
            "challenge_expected": "24 hserF",
            "max_nodes": 2,
        },
        {
            "name": "sequence-length",
            "input_domain": "sequence",
            "output_domain": "numeric",
            "examples": (
                ProgramExample([1, 2], 2),
                ProgramExample([], 0),
                ProgramExample([5, -1, 7], 3),
            ),
            "probe": [9, 8, 7, 6],
            "expected": 4,
            "challenge": [10, 20, 30, 40, 50],
            "challenge_expected": 5,
            "max_nodes": 2,
        },
        {
            "name": "object-has-token",
            "input_domain": "object",
            "output_domain": "boolean",
            "examples": (
                ProgramExample({"token": 1}, True),
                ProgramExample({"other": 1}, False),
                ProgramExample({"token": 0, "other": 2}, True),
            ),
            "probe": {"token": "x", "novel": True},
            "expected": True,
            "challenge": {"novel": 99, "token": False},
            "challenge_expected": True,
            "max_nodes": 2,
        },
    )


def _library_attempt(root: Path, spec: dict[str, Any]) -> dict[str, Any]:
    try:
        result = synthesize_from_verified_macro_library(
            root,
            input_domain=str(spec["input_domain"]),
            output_domain=str(spec["output_domain"]),
            examples=spec["examples"],
            max_depth=3,
            max_candidates=64,
        )
    except (VerifiedMacroLibraryError, RuntimeError) as exc:
        return {
            "goal": str(spec["name"]),
            "supported": False,
            "failure_type": type(exc).__name__,
        }
    return {
        "goal": str(spec["name"]),
        "supported": True,
        "selected_candidate_ids": [
            str(value) for value in result["selected_candidate_ids"]
        ],
        "selected_chain_depth": int(result["selected_chain_depth"]),
        "library_candidate_count": int(result["library_candidate_count"]),
        "candidate_ids_supplied_by_caller": bool(
            result["candidate_ids_supplied_by_caller"]
        ),
        "digest": str(result["digest"]),
    }


def _classify_goals(
    root: Path,
    specs: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [_library_attempt(root, spec) for spec in specs]


def run_autonomous_heterogeneous_goal_acquisition(root: Path, seed: str) -> dict[str, Any]:
    """Select and close one unsupported behavior from a bounded heterogeneous goal set.

    A durable multi-session capability base is reconstructed into a fresh state-only decision root.
    Three behavior goals span string, sequence, and object domains. The decision procedure receives no
    Candidate IDs and first asks the exact-scope verified macro library whether each behavior is already
    supported. It must identify multiple fail-closed gaps, then use a seed-committed deterministic choice
    over only those unsupported goals to select one target rather than relying on a fixed phase-specific
    Candidate identity.

    The selected goal is learned in a separate fresh state-only learner through bounded synthesis and the
    existing protected exact-scope regression gate, then transactionally admitted to the durable root.
    Another fresh restart must rediscover the enlarged library without caller Candidate IDs, solve the
    selected behavior through exactly the newly acquired Candidate, retain at least one heterogeneous
    unselected gap as unsupported, and transfer the selected skill to a post-retrieval challenge without
    changing any old or new Candidate bytes/trial ledgers.

    The goal set, choice rule, synthesis grammar, examples, regression, and evaluator remain repository-
    authored. This is bounded internal autonomous-goal-selection evidence, not independent AGI evidence.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    base = run_transactional_multisession_commit(
        root,
        f"{seed}:heterogeneous-goal-base",
    )
    if not base.get("passed"):
        raise AutonomousHeterogeneousGoalError(
            "transactional durable capability base did not pass"
        )

    prior_ids = _all_candidate_ids(root)
    prior_tree = _tree_snapshot(root, prior_ids)
    prior_trials = _trial_snapshot(root, prior_ids)
    specs = _goal_specs()

    with tempfile.TemporaryDirectory(prefix="agi-heterogeneous-goal-decision-") as temporary:
        decision_root = Path(temporary).resolve()
        _copy_persistent_state(root, decision_root)
        prior_runs_copied = (decision_root / ".continual" / "runs").exists()
        prior_episodes_copied = (decision_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (decision_root / ".continual" / "evidence").exists()
        if _tree_snapshot(decision_root, prior_ids) != prior_tree:
            raise AutonomousHeterogeneousGoalError(
                "goal-decision restart changed prior Candidate bytes"
            )
        if _trial_snapshot(decision_root, prior_ids) != prior_trials:
            raise AutonomousHeterogeneousGoalError(
                "goal-decision restart changed prior Candidate trial ledgers"
            )

        initial_classification = _classify_goals(decision_root, specs)
        if any(
            item.get("candidate_ids_supplied_by_caller") is True
            for item in initial_classification
        ):
            raise AutonomousHeterogeneousGoalError(
                "goal classification unexpectedly relied on caller Candidate IDs"
            )
        unsupported_names = sorted(
            str(item["goal"])
            for item in initial_classification
            if not item["supported"]
        )
        if len(unsupported_names) < 2:
            raise AutonomousHeterogeneousGoalError(
                "heterogeneous goal set did not expose multiple unsupported behaviors"
            )

    selection_commitment = _digest(
        {
            "seed": seed,
            "phase": "heterogeneous-unsupported-goal-choice-v1",
            "unsupported_goals": unsupported_names,
            "initial_classification": initial_classification,
        }
    )
    selected_name = unsupported_names[
        int(selection_commitment[:8], 16) % len(unsupported_names)
    ]
    selected_spec = next(spec for spec in specs if spec["name"] == selected_name)

    with tempfile.TemporaryDirectory(prefix="agi-heterogeneous-goal-learner-") as temporary:
        learner_root = Path(temporary).resolve()
        _copy_persistent_state(root, learner_root)
        if _tree_snapshot(learner_root, prior_ids) != prior_tree:
            raise AutonomousHeterogeneousGoalError(
                "goal learner restart changed prior Candidate bytes"
            )
        if _trial_snapshot(learner_root, prior_ids) != prior_trials:
            raise AutonomousHeterogeneousGoalError(
                "goal learner restart changed prior Candidate trials"
            )

        prelearning = _library_attempt(learner_root, selected_spec)
        if prelearning["supported"]:
            raise AutonomousHeterogeneousGoalError(
                "selected unsupported goal became supported before learning"
            )

        acquired = _promote_task(
            learner_root,
            seed=f"{seed}:selected-goal:{selected_name}",
            spec=selected_spec,
        )
        candidate_id = str(acquired["candidate_id"])
        scope = str(acquired["scope"])
        if candidate_id in prior_ids:
            raise AutonomousHeterogeneousGoalError(
                "selected goal reused a prior Candidate identity"
            )
        if not acquired.get("negative_evidence_retained"):
            raise AutonomousHeterogeneousGoalError(
                "selected goal Candidate did not retain protected negative evidence"
            )
        acquired_trials = _trial_snapshot(learner_root, [candidate_id])
        if not acquired_trials.get(candidate_id):
            raise AutonomousHeterogeneousGoalError(
                "selected goal Candidate lacks durable regression trials"
            )
        if _tree_snapshot(learner_root, prior_ids) != prior_tree:
            raise AutonomousHeterogeneousGoalError(
                "selected goal learning changed prior Candidate bytes"
            )
        if _trial_snapshot(learner_root, prior_ids) != prior_trials:
            raise AutonomousHeterogeneousGoalError(
                "selected goal learning changed prior Candidate trials"
            )

        commit = _commit_verified_candidate(
            learner_root,
            root,
            candidate_id=candidate_id,
            scope=scope,
        )

    if _tree_snapshot(root, prior_ids) != prior_tree:
        raise AutonomousHeterogeneousGoalError(
            "selected goal transactional commit changed prior Candidate bytes"
        )
    if _trial_snapshot(root, prior_ids) != prior_trials:
        raise AutonomousHeterogeneousGoalError(
            "selected goal transactional commit changed prior Candidate trials"
        )

    enlarged_ids = [*prior_ids, candidate_id]
    enlarged_tree = _tree_snapshot(root, enlarged_ids)
    enlarged_trials = _trial_snapshot(root, enlarged_ids)

    with tempfile.TemporaryDirectory(prefix="agi-heterogeneous-goal-resolver-") as temporary:
        resolver_root = Path(temporary).resolve()
        _copy_persistent_state(root, resolver_root)
        if _tree_snapshot(resolver_root, enlarged_ids) != enlarged_tree:
            raise AutonomousHeterogeneousGoalError(
                "post-acquisition restart changed Candidate bytes"
            )
        if _trial_snapshot(resolver_root, enlarged_ids) != enlarged_trials:
            raise AutonomousHeterogeneousGoalError(
                "post-acquisition restart changed Candidate trials"
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
            raise AutonomousHeterogeneousGoalError(
                "post-acquisition resolver relied on caller Candidate IDs"
            )
        selected_ids = [
            str(value) for value in selected_result["selected_candidate_ids"]
        ]
        if selected_ids != [candidate_id] or int(selected_result["selected_chain_depth"]) != 1:
            raise AutonomousHeterogeneousGoalError(
                "fresh resolver did not select exactly the newly acquired goal skill"
            )

        post_classification = _classify_goals(resolver_root, specs)
        by_goal = {str(item["goal"]): item for item in post_classification}
        if not by_goal[selected_name]["supported"]:
            raise AutonomousHeterogeneousGoalError(
                "selected goal remained unsupported after verified acquisition"
            )
        remaining_unsupported = sorted(
            name
            for name in unsupported_names
            if name != selected_name and not by_goal[name]["supported"]
        )
        if not remaining_unsupported:
            raise AutonomousHeterogeneousGoalError(
                "one bounded acquisition unexpectedly erased all heterogeneous gaps"
            )

        compiled_program = selected_result["compiled_program"]
        challenge = selected_spec["challenge"]
        challenge_expected = selected_spec["challenge_expected"]
        if execute_program(compiled_program, challenge) != challenge_expected:
            raise AutonomousHeterogeneousGoalError(
                "fresh resolver compiled skill failed post-selection challenge"
            )
        fresh_run = _run_one(
            resolver_root,
            candidate_id=candidate_id,
            value=challenge,
            expected=challenge_expected,
        )
        resolver_state_unchanged = (
            _tree_snapshot(resolver_root, enlarged_ids) == enlarged_tree
        )
        resolver_trials_unchanged = (
            _trial_snapshot(resolver_root, enlarged_ids) == enlarged_trials
        )
        if not resolver_state_unchanged or not resolver_trials_unchanged:
            raise AutonomousHeterogeneousGoalError(
                "post-acquisition goal resolution changed Candidate state or trials"
            )

    source_state_unchanged = _tree_snapshot(root, enlarged_ids) == enlarged_tree
    source_trials_unchanged = _trial_snapshot(root, enlarged_ids) == enlarged_trials
    if not source_state_unchanged or not source_trials_unchanged:
        raise AutonomousHeterogeneousGoalError(
            "fresh heterogeneous goal resolution mutated durable source state"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "autonomous-heterogeneous-goal-acquisition-v1",
        "transactional_base_digest": str(base["digest"]),
        "goal_names": [str(spec["name"]) for spec in specs],
        "goal_domains": [
            f"{spec['input_domain']}->{spec['output_domain']}" for spec in specs
        ],
        "initial_classification": initial_classification,
        "initial_unsupported_goals": unsupported_names,
        "initial_unsupported_count": len(unsupported_names),
        "selection_commitment": selection_commitment,
        "selected_goal": selected_name,
        "selected_from_unsupported_only": selected_name in unsupported_names,
        "selected_goal_candidate_id": candidate_id,
        "selected_goal_scope": scope,
        "selected_goal_negative_evidence_retained": bool(
            acquired["negative_evidence_retained"]
        ),
        "transactional_commit": commit,
        "caller_supplied_candidate_ids_for_goal_classification": False,
        "caller_supplied_candidate_ids_for_post_acquisition_resolution": False,
        "post_acquisition_selected_candidate_ids": selected_ids,
        "post_acquisition_selected_chain_depth": int(
            selected_result["selected_chain_depth"]
        ),
        "remaining_unsupported_goals": remaining_unsupported,
        "remaining_unsupported_count": len(remaining_unsupported),
        "one_acquisition_did_not_claim_all_goals": bool(remaining_unsupported),
        "post_selection_challenge_sha256": _digest(challenge),
        "fresh_engine_run": fresh_run,
        "resolver_state_unchanged": resolver_state_unchanged,
        "resolver_trials_unchanged": resolver_trials_unchanged,
        "source_state_unchanged": source_state_unchanged,
        "source_trials_unchanged": source_trials_unchanged,
        "prior_runs_copied": prior_runs_copied,
        "prior_episodes_copied": prior_episodes_copied,
        "prior_evidence_copied": prior_evidence_copied,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded autonomous goal-selection/acquisition evidence only. The system classified "
            "a repository-authored heterogeneous behavior set using only its verified durable macro "
            "library, selected one fail-closed gap from that set by a committed deterministic rule, "
            "learned and transactionally retained the missing skill, rediscovered it after restart, "
            "and retained other unsupported goals instead of overclaiming coverage. The goal set, choice "
            "rule, examples, grammar, regression, search, and evaluator are not independent production "
            "evidence and do not establish AGI."
        ),
    }
    if not all(
        (
            report["selected_from_unsupported_only"],
            report["selected_goal_negative_evidence_retained"],
            report["one_acquisition_did_not_claim_all_goals"],
            report["resolver_state_unchanged"],
            report["resolver_trials_unchanged"],
            report["source_state_unchanged"],
            report["source_trials_unchanged"],
            not report["prior_runs_copied"],
            not report["prior_episodes_copied"],
            not report["prior_evidence_copied"],
        )
    ):
        raise AutonomousHeterogeneousGoalError(
            "autonomous heterogeneous goal aggregate invariant failed"
        )
    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "autonomous-heterogeneous-goal-acquisition"
        / f"goal-acquisition-{_digest(seed)[:16]}.json",
        report,
    )
    return report
