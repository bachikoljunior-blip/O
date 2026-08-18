from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample
from agi.cross_family_order_retention import (
    _trial_snapshots,
    run_cross_family_order_retention,
)
from agi.durable_state_rehydration import _run_one, _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multiround_disambiguation_bank import _target_answer
from agi.post_rehydration_continual_extension import _extension_spec


class MultiSessionContinualChainError(RuntimeError):
    pass


def _numeric_negation_spec() -> dict[str, Any]:
    return {
        "name": "numeric-neg-session3",
        "input_domain": "numeric",
        "output_domain": "numeric",
        "examples": (
            ProgramExample(4, -4),
            ProgramExample(-6, 6),
            ProgramExample(0, 0),
        ),
        "probe": 19,
        "expected": -19,
        "max_nodes": 2,
    }


def _copy_persistent_state(source: Path, destination: Path) -> None:
    shutil.copytree(
        source / ".continual" / "candidates",
        destination / ".continual" / "candidates",
    )
    shutil.copytree(
        source / ".continual" / "system",
        destination / ".continual" / "system",
    )


def _replay_generation(
    root: Path,
    *,
    heterogeneous_ids: list[str],
    recursive_id: str,
    lower_id: str,
    negation_id: str,
    generation: int,
) -> list[str]:
    if len(heterogeneous_ids) != 4:
        raise MultiSessionContinualChainError(
            "expected four heterogeneous source capabilities"
        )
    if generation == 2:
        probes: tuple[tuple[Any, Any], ...] = (
            (-211, 211),
            ("session two", "SESSION TWO"),
            ([7, 8, -3], 12),
            ({"kind": "target", "noise": "session-2", "novel": 201}, True),
        )
        recursive_value = {"b": 202, "session": 2}
        lower_value = "SESSION Two"
        lower_expected = "session two"
        negation_value = 31
        negation_expected = -31
    elif generation == 3:
        probes = (
            (-307, 307),
            ("third restart", "THIRD RESTART"),
            ([50, -5, -4], 41),
            ({"kind": "target", "noise": "session-3", "novel": 301}, True),
        )
        recursive_value = {"b": 302, "session": 3, "fresh": True}
        lower_value = "THIRD Session"
        lower_expected = "third session"
        negation_value = -42
        negation_expected = 42
    else:
        raise ValueError("generation must be 2 or 3")

    runs: list[str] = []
    for candidate_id, (value, expected) in zip(
        heterogeneous_ids,
        probes,
        strict=True,
    ):
        runs.append(
            _run_one(
                root,
                candidate_id=candidate_id,
                value=value,
                expected=expected,
            )
        )
    runs.append(
        _run_one(
            root,
            candidate_id=recursive_id,
            value=recursive_value,
            expected=not _target_answer(recursive_value),
        )
    )
    runs.append(
        _run_one(
            root,
            candidate_id=lower_id,
            value=lower_value,
            expected=lower_expected,
        )
    )
    runs.append(
        _run_one(
            root,
            candidate_id=negation_id,
            value=negation_value,
            expected=negation_expected,
        )
    )
    return runs


def run_multi_session_continual_chain(root: Path, seed: str) -> dict[str, Any]:
    """Accumulate two new skills across successive state-only restarts without forgetting.

    The source root first accumulates the verified heterogeneous family and recursive memory-derived
    skill. Session 1 is reconstructed from Candidate/system state only and learns a new string-lower
    skill. Session 2 is reconstructed from Session 1 state only and learns a second, distinct numeric
    negation skill. It must keep every older Candidate byte tree and trial ledger unchanged while all
    old-plus-new skills replay on fresh inputs. Session 3 is reconstructed from Session 2 state only and
    must replay the complete capability set again with no new Candidate trials. Prior runs, episodes,
    and evidence directories are deliberately excluded from every restart transfer.

    This is repository-authored deterministic multi-session continual-learning evidence. It does not
    establish open-domain generality, independent evaluation, or production AGI.
    """
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    base = run_cross_family_order_retention(root, f"{seed}:source")
    if not base.get("passed"):
        raise MultiSessionContinualChainError(
            "cross-family accumulated-capability prerequisite did not pass"
        )
    heterogeneous_ids = [str(item) for item in base["heterogeneous_candidate_ids"]]
    recursive_component_ids = [
        str(item) for item in base["recursive_component_candidate_ids"]
    ]
    recursive_id = str(base["recursive_candidate_id"])
    source_ids = [
        *heterogeneous_ids,
        *recursive_component_ids,
        recursive_id,
    ]
    if len(source_ids) != len(set(source_ids)):
        raise MultiSessionContinualChainError(
            "source Candidate identities are not unique"
        )
    source_tree_before = _tree_snapshot(root, source_ids)
    source_trials_before = _trial_snapshots(root, source_ids)

    with tempfile.TemporaryDirectory(prefix="agi-multisession-1-") as session1_temporary:
        session1 = Path(session1_temporary).resolve()
        _copy_persistent_state(root, session1)
        if _tree_snapshot(session1, source_ids) != source_tree_before:
            raise MultiSessionContinualChainError(
                "session 1 reconstruction changed source Candidate bytes"
            )
        if _trial_snapshots(session1, source_ids) != source_trials_before:
            raise MultiSessionContinualChainError(
                "session 1 reconstruction changed source Candidate trials"
            )

        lower = _promote_task(
            session1,
            seed=f"{seed}:session1-lower",
            spec=_extension_spec(),
        )
        lower_id = str(lower["candidate_id"])
        if lower_id in source_ids:
            raise MultiSessionContinualChainError(
                "session 1 reused a source Candidate identity"
            )
        session1_prior_tree_after_lower = _tree_snapshot(session1, source_ids)
        session1_prior_trials_after_lower = _trial_snapshots(session1, source_ids)
        source_unchanged_by_first_extension = (
            session1_prior_tree_after_lower == source_tree_before
        )
        source_trials_unchanged_by_first_extension = (
            session1_prior_trials_after_lower == source_trials_before
        )
        if not source_unchanged_by_first_extension:
            raise MultiSessionContinualChainError(
                "session 1 learning changed an older Candidate byte tree"
            )
        if not source_trials_unchanged_by_first_extension:
            raise MultiSessionContinualChainError(
                "session 1 learning rewrote an older Candidate trial ledger"
            )
        lower_trials = _trial_snapshots(session1, [lower_id])
        if not lower_trials.get(lower_id):
            raise MultiSessionContinualChainError(
                "session 1 extension lacks persisted regression trials"
            )
        session1_ids = [*source_ids, lower_id]
        session1_tree = _tree_snapshot(session1, session1_ids)
        session1_trials = _trial_snapshots(session1, session1_ids)

        with tempfile.TemporaryDirectory(prefix="agi-multisession-2-") as session2_temporary:
            session2 = Path(session2_temporary).resolve()
            _copy_persistent_state(session1, session2)
            session2_copy_byte_identical = (
                _tree_snapshot(session2, session1_ids) == session1_tree
            )
            session2_trials_byte_identical = (
                _trial_snapshots(session2, session1_ids) == session1_trials
            )
            if not session2_copy_byte_identical:
                raise MultiSessionContinualChainError(
                    "session 2 reconstruction changed session 1 Candidate bytes"
                )
            if not session2_trials_byte_identical:
                raise MultiSessionContinualChainError(
                    "session 2 reconstruction changed session 1 Candidate trials"
                )

            negation = _promote_task(
                session2,
                seed=f"{seed}:session2-negation",
                spec=_numeric_negation_spec(),
            )
            negation_id = str(negation["candidate_id"])
            if negation_id in session1_ids:
                raise MultiSessionContinualChainError(
                    "session 2 reused an earlier Candidate identity"
                )
            session2_prior_tree_after_negation = _tree_snapshot(session2, session1_ids)
            session2_prior_trials_after_negation = _trial_snapshots(session2, session1_ids)
            session1_state_unchanged_by_second_extension = (
                session2_prior_tree_after_negation == session1_tree
            )
            session1_trials_unchanged_by_second_extension = (
                session2_prior_trials_after_negation == session1_trials
            )
            if not session1_state_unchanged_by_second_extension:
                raise MultiSessionContinualChainError(
                    "session 2 learning changed an earlier Candidate byte tree"
                )
            if not session1_trials_unchanged_by_second_extension:
                raise MultiSessionContinualChainError(
                    "session 2 learning rewrote an earlier Candidate trial ledger"
                )
            negation_trials = _trial_snapshots(session2, [negation_id])
            if not negation_trials.get(negation_id):
                raise MultiSessionContinualChainError(
                    "session 2 extension lacks persisted regression trials"
                )

            session2_replay_runs = _replay_generation(
                session2,
                heterogeneous_ids=heterogeneous_ids,
                recursive_id=recursive_id,
                lower_id=lower_id,
                negation_id=negation_id,
                generation=2,
            )
            session2_ids = [*session1_ids, negation_id]
            session2_tree_after_replay = _tree_snapshot(session2, session2_ids)
            session2_trials_after_replay = _trial_snapshots(session2, session2_ids)
            if _tree_snapshot(session2, session1_ids) != session1_tree:
                raise MultiSessionContinualChainError(
                    "session 2 replay changed older Candidate state"
                )
            if _trial_snapshots(session2, session1_ids) != session1_trials:
                raise MultiSessionContinualChainError(
                    "session 2 replay changed older Candidate trials"
                )
            if _trial_snapshots(session2, [negation_id]) != negation_trials:
                raise MultiSessionContinualChainError(
                    "session 2 replay changed new negation trials"
                )

            with tempfile.TemporaryDirectory(prefix="agi-multisession-3-") as session3_temporary:
                session3 = Path(session3_temporary).resolve()
                _copy_persistent_state(session2, session3)
                session3_copy_byte_identical = (
                    _tree_snapshot(session3, session2_ids) == session2_tree_after_replay
                )
                session3_trials_byte_identical = (
                    _trial_snapshots(session3, session2_ids) == session2_trials_after_replay
                )
                if not session3_copy_byte_identical:
                    raise MultiSessionContinualChainError(
                        "session 3 reconstruction changed extended Candidate bytes"
                    )
                if not session3_trials_byte_identical:
                    raise MultiSessionContinualChainError(
                        "session 3 reconstruction changed extended Candidate trials"
                    )

                session3_replay_runs = _replay_generation(
                    session3,
                    heterogeneous_ids=heterogeneous_ids,
                    recursive_id=recursive_id,
                    lower_id=lower_id,
                    negation_id=negation_id,
                    generation=3,
                )
                session3_state_unchanged_after_replay = (
                    _tree_snapshot(session3, session2_ids) == session2_tree_after_replay
                )
                session3_trials_unchanged_after_replay = (
                    _trial_snapshots(session3, session2_ids) == session2_trials_after_replay
                )
                if not session3_state_unchanged_after_replay:
                    raise MultiSessionContinualChainError(
                        "session 3 replay changed extended Candidate state"
                    )
                if not session3_trials_unchanged_after_replay:
                    raise MultiSessionContinualChainError(
                        "session 3 replay changed extended Candidate trials"
                    )

    source_tree_after = _tree_snapshot(root, source_ids)
    source_trials_after = _trial_snapshots(root, source_ids)
    source_state_unchanged = source_tree_after == source_tree_before
    source_trials_unchanged = source_trials_after == source_trials_before
    if not source_state_unchanged or not source_trials_unchanged:
        raise MultiSessionContinualChainError(
            "multi-session chain mutated source durable state"
        )

    fresh_runs = [*session2_replay_runs, *session3_replay_runs]
    if len(set(fresh_runs)) != len(fresh_runs):
        raise MultiSessionContinualChainError(
            "multi-session chain reused a supposedly fresh Engine run"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "multi-session-continual-chain-v1",
        "source_candidate_ids": source_ids,
        "heterogeneous_candidate_ids": heterogeneous_ids,
        "recursive_component_candidate_ids": recursive_component_ids,
        "recursive_candidate_id": recursive_id,
        "session1_extension_candidate_id": lower_id,
        "session1_extension_scope": lower["scope"],
        "session1_negative_evidence_retained": bool(lower["negative_evidence_retained"]),
        "session2_extension_candidate_id": negation_id,
        "session2_extension_scope": negation["scope"],
        "session2_negative_evidence_retained": bool(negation["negative_evidence_retained"]),
        "source_unchanged_by_first_extension": source_unchanged_by_first_extension,
        "source_trials_unchanged_by_first_extension": (
            source_trials_unchanged_by_first_extension
        ),
        "session2_copy_byte_identical": session2_copy_byte_identical,
        "session2_trials_byte_identical": session2_trials_byte_identical,
        "session1_state_unchanged_by_second_extension": (
            session1_state_unchanged_by_second_extension
        ),
        "session1_trials_unchanged_by_second_extension": (
            session1_trials_unchanged_by_second_extension
        ),
        "session3_copy_byte_identical": session3_copy_byte_identical,
        "session3_trials_byte_identical": session3_trials_byte_identical,
        "session3_state_unchanged_after_replay": session3_state_unchanged_after_replay,
        "session3_trials_unchanged_after_replay": session3_trials_unchanged_after_replay,
        "source_candidate_state_unchanged": source_state_unchanged,
        "source_trial_ledgers_unchanged": source_trials_unchanged,
        "state_only_restart_count": 3,
        "new_skill_count": 2,
        "fresh_engine_run_count": len(fresh_runs),
        "fresh_engine_runs": fresh_runs,
        "prior_runs_copied": False,
        "prior_episodes_copied": False,
        "prior_evidence_copied": False,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal deterministic repeated multi-session continual-learning evidence only. Two new "
            "bounded exact-scope skills are accumulated across successive Candidate/system-only restarts "
            "without changing earlier Candidate state or trials, and the full retained set survives a "
            "third restart. Repository-authored synthesis, tasks, measurements, and evaluators do not "
            "establish open-domain generality or independent production AGI."
        ),
    }
    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "multi-session-continual-chain"
        / f"chain-{_digest(seed)[:16]}.json",
        report,
    )
    return report
