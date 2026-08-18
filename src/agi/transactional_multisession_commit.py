from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from agi.acquired_program_runtime import _atomic_json
from agi.cross_family_order_retention import (
    _trial_snapshots,
    run_cross_family_order_retention,
)
from agi.durable_state_rehydration import _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_session_continual_chain import (
    _copy_persistent_state,
    _numeric_negation_spec,
    _replay_generation,
)
from agi.post_rehydration_continual_extension import _extension_spec
from continual.candidate_regression import (
    CandidateIntegrityError,
    candidate_verified_for_scope,
)


class TransactionalMultiSessionCommitError(RuntimeError):
    pass


def _read_candidate(root: Path, candidate_id: str) -> dict[str, Any]:
    path = root / ".continual" / "candidates" / candidate_id / "candidate.json"
    if not path.is_file():
        raise TransactionalMultiSessionCommitError(
            f"missing Candidate state: {candidate_id}"
        )
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("candidate_id") != candidate_id:
        raise TransactionalMultiSessionCommitError(
            f"invalid Candidate identity: {candidate_id}"
        )
    return value


def _directory_snapshot(base: Path) -> dict[str, str]:
    if not base.is_dir():
        raise TransactionalMultiSessionCommitError(f"missing directory: {base}")
    result: dict[str, str] = {}
    for path in sorted(item for item in base.rglob("*") if item.is_file()):
        result[path.relative_to(base).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    if not result:
        raise TransactionalMultiSessionCommitError(
            f"directory contains no durable files: {base}"
        )
    return result


def _require_verified_candidate(
    root: Path,
    *,
    candidate_id: str,
    scope: str,
) -> dict[str, Any]:
    candidate = _read_candidate(root, candidate_id)
    if not candidate_verified_for_scope(root, candidate, scope):
        raise TransactionalMultiSessionCommitError(
            f"Candidate is not verified for exact scope: {candidate_id}"
        )
    return candidate


def _commit_verified_candidate(
    source_root: Path,
    destination_root: Path,
    *,
    candidate_id: str,
    scope: str,
) -> dict[str, Any]:
    """Atomically add one already verified Candidate without overwriting durable state."""

    source_root = source_root.resolve()
    destination_root = destination_root.resolve()
    _require_verified_candidate(
        source_root,
        candidate_id=candidate_id,
        scope=scope,
    )
    source_dir = source_root / ".continual" / "candidates" / candidate_id
    source_snapshot = _directory_snapshot(source_dir)
    source_trials = _trial_snapshots(source_root, [candidate_id])
    if not source_trials.get(candidate_id):
        raise TransactionalMultiSessionCommitError(
            "verified Candidate has no durable trial ledger"
        )

    candidates_root = destination_root / ".continual" / "candidates"
    candidates_root.mkdir(parents=True, exist_ok=True)
    destination_dir = candidates_root / candidate_id
    if destination_dir.exists():
        raise TransactionalMultiSessionCommitError(
            "transactional Candidate commit refuses to overwrite existing state"
        )

    staging_dir = candidates_root / (
        f".staging-{candidate_id}-{_digest(source_snapshot)[:12]}"
    )
    if staging_dir.exists():
        raise TransactionalMultiSessionCommitError(
            "transactional Candidate staging path already exists"
        )

    installed = False
    try:
        shutil.copytree(source_dir, staging_dir)
        if _directory_snapshot(staging_dir) != source_snapshot:
            raise TransactionalMultiSessionCommitError(
                "staged Candidate bytes differ from verified source"
            )
        staging_dir.replace(destination_dir)
        installed = True
        if _directory_snapshot(destination_dir) != source_snapshot:
            raise TransactionalMultiSessionCommitError(
                "committed Candidate bytes differ from verified source"
            )
        _require_verified_candidate(
            destination_root,
            candidate_id=candidate_id,
            scope=scope,
        )
        if _trial_snapshots(destination_root, [candidate_id]) != source_trials:
            raise TransactionalMultiSessionCommitError(
                "committed Candidate trial ledger differs from verified source"
            )
    except Exception:
        if installed and destination_dir.is_dir():
            shutil.rmtree(destination_dir)
        if staging_dir.is_dir():
            shutil.rmtree(staging_dir)
        raise

    return {
        "candidate_id": candidate_id,
        "scope": scope,
        "file_count": len(source_snapshot),
        "tree_digest": _digest(source_snapshot),
        "trial_snapshot": source_trials[candidate_id],
        "atomic_directory_rename": True,
        "overwrite_allowed": False,
    }


def _assert_unchanged(
    root: Path,
    candidate_ids: list[str],
    tree_before: Mapping[str, str],
    trials_before: Mapping[str, Mapping[str, str]],
    *,
    label: str,
) -> None:
    if _tree_snapshot(root, candidate_ids) != dict(tree_before):
        raise TransactionalMultiSessionCommitError(
            f"{label} changed protected Candidate bytes"
        )
    if _trial_snapshots(root, candidate_ids) != dict(trials_before):
        raise TransactionalMultiSessionCommitError(
            f"{label} changed protected Candidate trials"
        )


def run_transactional_multisession_commit(root: Path, seed: str) -> dict[str, Any]:
    """Commit session-learned skills into durable state only after exact-scope verification.

    Prior multi-session evidence showed that temporary state-only restarts can accumulate two new
    skills without forgetting, but those temporary roots are discarded. This milestone closes that
    durability gap. Each new skill is first learned and regression-verified in a state-only reconstructed
    root. A tampered copy is rejected before installation. The verified Candidate directory is then
    staged and atomically renamed into the durable source root without overwriting any existing state.
    A second state-only session must observe the first committed skill before learning the second one.
    Finally, the durable root and another fresh state-only reconstruction replay the complete retained
    capability set on new inputs without spending additional Candidate trials.

    This remains repository-authored deterministic internal development evidence. It demonstrates a
    bounded transactional persistence property, not open-domain autonomy or independent production AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    base = run_cross_family_order_retention(root, f"{seed}:source")
    if not base.get("passed"):
        raise TransactionalMultiSessionCommitError(
            "cross-family accumulated-capability prerequisite did not pass"
        )
    heterogeneous_ids = [str(value) for value in base["heterogeneous_candidate_ids"]]
    recursive_component_ids = [
        str(value) for value in base["recursive_component_candidate_ids"]
    ]
    recursive_id = str(base["recursive_candidate_id"])
    source_ids = [*heterogeneous_ids, *recursive_component_ids, recursive_id]
    if len(source_ids) != len(set(source_ids)):
        raise TransactionalMultiSessionCommitError(
            "source Candidate identities are not unique"
        )
    source_tree_before = _tree_snapshot(root, source_ids)
    source_trials_before = _trial_snapshots(root, source_ids)

    with tempfile.TemporaryDirectory(prefix="agi-transaction-session-1-") as first_temporary:
        session1 = Path(first_temporary).resolve()
        _copy_persistent_state(root, session1)
        _assert_unchanged(
            session1,
            source_ids,
            source_tree_before,
            source_trials_before,
            label="session 1 reconstruction",
        )

        lower = _promote_task(
            session1,
            seed=f"{seed}:session1-lower",
            spec=_extension_spec(),
        )
        lower_id = str(lower["candidate_id"])
        lower_scope = str(lower["scope"])
        if lower_id in source_ids:
            raise TransactionalMultiSessionCommitError(
                "session 1 reused a source Candidate identity"
            )
        _require_verified_candidate(
            session1,
            candidate_id=lower_id,
            scope=lower_scope,
        )
        _assert_unchanged(
            session1,
            source_ids,
            source_tree_before,
            source_trials_before,
            label="session 1 learning",
        )

        tampered_commit_failed_closed = False
        with tempfile.TemporaryDirectory(prefix="agi-transaction-tamper-") as tampered_temporary:
            tampered = Path(tampered_temporary).resolve()
            _copy_persistent_state(session1, tampered)
            tampered_candidate = _read_candidate(tampered, lower_id)
            tampered_candidate["unverified_transactional_tamper"] = True
            _atomic_json(
                tampered
                / ".continual"
                / "candidates"
                / lower_id
                / "candidate.json",
                tampered_candidate,
            )
            try:
                _commit_verified_candidate(
                    tampered,
                    root,
                    candidate_id=lower_id,
                    scope=lower_scope,
                )
            except (CandidateIntegrityError, TransactionalMultiSessionCommitError):
                tampered_commit_failed_closed = True
        if not tampered_commit_failed_closed:
            raise TransactionalMultiSessionCommitError(
                "tampered Candidate was accepted by transactional commit"
            )
        if (root / ".continual" / "candidates" / lower_id).exists():
            raise TransactionalMultiSessionCommitError(
                "failed tampered commit left durable Candidate residue"
            )

        lower_commit = _commit_verified_candidate(
            session1,
            root,
            candidate_id=lower_id,
            scope=lower_scope,
        )

    _assert_unchanged(
        root,
        source_ids,
        source_tree_before,
        source_trials_before,
        label="first transactional commit",
    )
    lower_tree_after_commit = _tree_snapshot(root, [lower_id])
    lower_trials_after_commit = _trial_snapshots(root, [lower_id])

    duplicate_commit_failed_closed = False
    try:
        _commit_verified_candidate(
            root,
            root,
            candidate_id=lower_id,
            scope=lower_scope,
        )
    except TransactionalMultiSessionCommitError:
        duplicate_commit_failed_closed = True
    if not duplicate_commit_failed_closed:
        raise TransactionalMultiSessionCommitError(
            "transactional commit overwrote an existing Candidate"
        )
    if _tree_snapshot(root, [lower_id]) != lower_tree_after_commit:
        raise TransactionalMultiSessionCommitError(
            "duplicate-commit rejection changed the existing Candidate"
        )

    first_committed_ids = [*source_ids, lower_id]
    first_committed_tree = _tree_snapshot(root, first_committed_ids)
    first_committed_trials = _trial_snapshots(root, first_committed_ids)

    with tempfile.TemporaryDirectory(prefix="agi-transaction-session-2-") as second_temporary:
        session2 = Path(second_temporary).resolve()
        _copy_persistent_state(root, session2)
        _assert_unchanged(
            session2,
            first_committed_ids,
            first_committed_tree,
            first_committed_trials,
            label="session 2 reconstruction",
        )
        _require_verified_candidate(
            session2,
            candidate_id=lower_id,
            scope=lower_scope,
        )

        negation = _promote_task(
            session2,
            seed=f"{seed}:session2-negation",
            spec=_numeric_negation_spec(),
        )
        negation_id = str(negation["candidate_id"])
        negation_scope = str(negation["scope"])
        if negation_id in first_committed_ids:
            raise TransactionalMultiSessionCommitError(
                "session 2 reused an already committed Candidate identity"
            )
        _require_verified_candidate(
            session2,
            candidate_id=negation_id,
            scope=negation_scope,
        )
        _assert_unchanged(
            session2,
            first_committed_ids,
            first_committed_tree,
            first_committed_trials,
            label="session 2 learning",
        )
        negation_commit = _commit_verified_candidate(
            session2,
            root,
            candidate_id=negation_id,
            scope=negation_scope,
        )

    _assert_unchanged(
        root,
        first_committed_ids,
        first_committed_tree,
        first_committed_trials,
        label="second transactional commit",
    )
    all_ids = [*first_committed_ids, negation_id]
    durable_tree_before_replay = _tree_snapshot(root, all_ids)
    durable_trials_before_replay = _trial_snapshots(root, all_ids)
    _require_verified_candidate(
        root,
        candidate_id=lower_id,
        scope=lower_scope,
    )
    _require_verified_candidate(
        root,
        candidate_id=negation_id,
        scope=negation_scope,
    )

    durable_replay_runs = _replay_generation(
        root,
        heterogeneous_ids=heterogeneous_ids,
        recursive_id=recursive_id,
        lower_id=lower_id,
        negation_id=negation_id,
        generation=2,
    )
    _assert_unchanged(
        root,
        all_ids,
        durable_tree_before_replay,
        durable_trials_before_replay,
        label="durable-root replay",
    )

    with tempfile.TemporaryDirectory(prefix="agi-transaction-final-restart-") as final_temporary:
        final_root = Path(final_temporary).resolve()
        _copy_persistent_state(root, final_root)
        prior_runs_copied = (final_root / ".continual" / "runs").exists()
        prior_episodes_copied = (final_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (final_root / ".continual" / "evidence").exists()
        _assert_unchanged(
            final_root,
            all_ids,
            durable_tree_before_replay,
            durable_trials_before_replay,
            label="final state-only reconstruction",
        )
        final_replay_runs = _replay_generation(
            final_root,
            heterogeneous_ids=heterogeneous_ids,
            recursive_id=recursive_id,
            lower_id=lower_id,
            negation_id=negation_id,
            generation=3,
        )
        _assert_unchanged(
            final_root,
            all_ids,
            durable_tree_before_replay,
            durable_trials_before_replay,
            label="final fresh replay",
        )

    _assert_unchanged(
        root,
        all_ids,
        durable_tree_before_replay,
        durable_trials_before_replay,
        label="completed transactional campaign",
    )
    fresh_runs = [*durable_replay_runs, *final_replay_runs]
    if len(set(fresh_runs)) != len(fresh_runs):
        raise TransactionalMultiSessionCommitError(
            "transactional campaign reused a supposedly fresh Engine run"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "transactional-multisession-candidate-commit-v1",
        "source_candidate_ids": source_ids,
        "first_committed_candidate_id": lower_id,
        "first_committed_scope": lower_scope,
        "second_committed_candidate_id": negation_id,
        "second_committed_scope": negation_scope,
        "transactional_commit_count": 2,
        "tampered_candidate_commit_failed_closed": tampered_commit_failed_closed,
        "duplicate_commit_failed_closed": duplicate_commit_failed_closed,
        "first_commit": lower_commit,
        "second_commit": negation_commit,
        "source_state_unchanged": _tree_snapshot(root, source_ids) == source_tree_before,
        "source_trials_unchanged": _trial_snapshots(root, source_ids) == source_trials_before,
        "first_committed_candidate_preserved_during_second_learning": (
            _tree_snapshot(root, [lower_id]) == lower_tree_after_commit
            and _trial_snapshots(root, [lower_id]) == lower_trials_after_commit
        ),
        "durable_root_contains_both_new_verified_candidates": True,
        "durable_state_unchanged_by_replay": (
            _tree_snapshot(root, all_ids) == durable_tree_before_replay
            and _trial_snapshots(root, all_ids) == durable_trials_before_replay
        ),
        "fresh_state_only_restart_replayed_full_set": True,
        "fresh_engine_run_count": len(fresh_runs),
        "fresh_engine_runs": fresh_runs,
        "fresh_engine_runs_unique": len(set(fresh_runs)) == len(fresh_runs),
        "prior_runs_copied": prior_runs_copied,
        "prior_episodes_copied": prior_episodes_copied,
        "prior_evidence_copied": prior_evidence_copied,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal deterministic transactional persistence evidence only. Two exact-scope learned "
            "skills were admitted into durable Candidate state only after regression verification; a "
            "tampered Candidate and duplicate overwrite both failed closed, protected Candidate bytes "
            "and trial ledgers remained unchanged, and the accumulated state replayed after a fresh "
            "state-only restart. Repository-authored tasks, synthesis, scoring, and runtime do not "
            "establish open-domain autonomy, independent evaluation, or production AGI."
        ),
    }
    if not all(
        (
            report["source_state_unchanged"],
            report["source_trials_unchanged"],
            report["first_committed_candidate_preserved_during_second_learning"],
            report["durable_state_unchanged_by_replay"],
            report["fresh_engine_runs_unique"],
            not report["prior_runs_copied"],
            not report["prior_episodes_copied"],
            not report["prior_evidence_copied"],
        )
    ):
        raise TransactionalMultiSessionCommitError(
            "transactional multi-session aggregate invariant failed"
        )
    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "transactional-multisession-commit"
        / f"commit-{_digest(seed)[:16]}.json",
        report,
    )
    return report
