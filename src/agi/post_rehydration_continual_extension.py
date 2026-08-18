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


class PostRehydrationContinualExtensionError(RuntimeError):
    pass


def _extension_spec() -> dict[str, Any]:
    return {
        "name": "string-lower-post-rehydrate",
        "input_domain": "string",
        "output_domain": "string",
        "examples": (
            ProgramExample("ABC", "abc"),
            ProgramExample("MiXeD", "mixed"),
            ProgramExample("Q", "q"),
        ),
        "probe": "Post REHYDRATE",
        "expected": "post rehydrate",
        "max_nodes": 2,
    }


def _replay_accumulated(
    root: Path,
    *,
    heterogeneous_ids: list[str],
    recursive_id: str,
    extension_id: str,
) -> list[str]:
    if len(heterogeneous_ids) != 4:
        raise PostRehydrationContinualExtensionError(
            "expected four heterogeneous source capabilities"
        )
    probes = (
        (-137, 137),
        ("after restart", "AFTER RESTART"),
        ([100, -20, 7], 87),
        ({"kind": "target", "noise": "rehydrated", "novel": 91}, True),
    )
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

    recursive_value = {"b": 92, "rehydrated": True}
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
            candidate_id=extension_id,
            value="SECOND Fresh",
            expected="second fresh",
        )
    )
    return runs


def run_post_rehydration_continual_extension(root: Path, seed: str) -> dict[str, Any]:
    """Continue learning after durable rehydration, then survive another fresh restart.

    A source root first accumulates the verified heterogeneous capability family plus the later
    recursive memory-derived skill. Candidate/system state alone is copied into a distinct root. That
    rehydrated root must learn and regression-promote a genuinely new exact-scope string-lower skill
    while every pre-existing Candidate byte tree and trial ledger remains unchanged. Fresh Engines then
    replay the old skills on novel inputs plus the new skill on an unseen input. The extended persistent
    state is copied again into a second distinct root with no prior run/episode/evidence directories;
    the same old-plus-new capability set must replay again without spending additional Candidate trials.

    This is repository-authored deterministic multi-session continual-learning evidence only. It does
    not establish open-domain generality, evaluator independence, or production AGI.
    """
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    base = run_cross_family_order_retention(root, f"{seed}:source")
    if not base.get("passed"):
        raise PostRehydrationContinualExtensionError(
            "cross-family accumulated-capability prerequisite did not pass"
        )
    heterogeneous_ids = [str(item) for item in base["heterogeneous_candidate_ids"]]
    recursive_component_ids = [
        str(item) for item in base["recursive_component_candidate_ids"]
    ]
    recursive_id = str(base["recursive_candidate_id"])
    prior_ids = [
        *heterogeneous_ids,
        *recursive_component_ids,
        recursive_id,
    ]
    if len(prior_ids) != len(set(prior_ids)):
        raise PostRehydrationContinualExtensionError(
            "pre-existing Candidate identities are not unique"
        )

    source_tree_before = _tree_snapshot(root, prior_ids)
    source_trials_before = _trial_snapshots(root, prior_ids)

    with tempfile.TemporaryDirectory(prefix="agi-post-rehydrate-extension-") as temporary:
        rehydrated_root = Path(temporary).resolve()
        shutil.copytree(
            root / ".continual" / "candidates",
            rehydrated_root / ".continual" / "candidates",
        )
        shutil.copytree(
            root / ".continual" / "system",
            rehydrated_root / ".continual" / "system",
        )
        prior_tree_before_extension = _tree_snapshot(rehydrated_root, prior_ids)
        prior_trials_before_extension = _trial_snapshots(rehydrated_root, prior_ids)
        if prior_tree_before_extension != source_tree_before:
            raise PostRehydrationContinualExtensionError(
                "first rehydration changed pre-existing Candidate bytes"
            )
        if prior_trials_before_extension != source_trials_before:
            raise PostRehydrationContinualExtensionError(
                "first rehydration changed pre-existing Candidate trial ledgers"
            )

        extension = _promote_task(
            rehydrated_root,
            seed=f"{seed}:extension",
            spec=_extension_spec(),
        )
        extension_id = str(extension["candidate_id"])
        if extension_id in prior_ids:
            raise PostRehydrationContinualExtensionError(
                "post-rehydration learning reused a prior Candidate identity"
            )
        extension_trials_before_replay = _trial_snapshots(
            rehydrated_root,
            [extension_id],
        )
        if not extension_trials_before_replay.get(extension_id):
            raise PostRehydrationContinualExtensionError(
                "new post-rehydration Candidate lacks durable regression trials"
            )

        prior_tree_after_extension = _tree_snapshot(rehydrated_root, prior_ids)
        prior_trials_after_extension = _trial_snapshots(rehydrated_root, prior_ids)
        prior_state_unchanged_after_extension = (
            prior_tree_after_extension == prior_tree_before_extension
        )
        prior_trials_unchanged_after_extension = (
            prior_trials_after_extension == prior_trials_before_extension
        )
        if not prior_state_unchanged_after_extension:
            raise PostRehydrationContinualExtensionError(
                "new learning changed a pre-existing Candidate byte tree"
            )
        if not prior_trials_unchanged_after_extension:
            raise PostRehydrationContinualExtensionError(
                "new learning rewrote a pre-existing Candidate trial ledger"
            )

        first_replay_runs = _replay_accumulated(
            rehydrated_root,
            heterogeneous_ids=heterogeneous_ids,
            recursive_id=recursive_id,
            extension_id=extension_id,
        )
        if _tree_snapshot(rehydrated_root, prior_ids) != prior_tree_before_extension:
            raise PostRehydrationContinualExtensionError(
                "first fresh replay changed prior Candidate state"
            )
        if _trial_snapshots(rehydrated_root, prior_ids) != prior_trials_before_extension:
            raise PostRehydrationContinualExtensionError(
                "first fresh replay changed prior Candidate trials"
            )
        if _trial_snapshots(rehydrated_root, [extension_id]) != extension_trials_before_replay:
            raise PostRehydrationContinualExtensionError(
                "first fresh replay changed new Candidate trials"
            )

        extended_ids = [*prior_ids, extension_id]
        extended_tree_before_second_restart = _tree_snapshot(
            rehydrated_root,
            extended_ids,
        )
        extended_trials_before_second_restart = _trial_snapshots(
            rehydrated_root,
            extended_ids,
        )

        with tempfile.TemporaryDirectory(prefix="agi-second-rehydrate-") as second_temporary:
            second_root = Path(second_temporary).resolve()
            shutil.copytree(
                rehydrated_root / ".continual" / "candidates",
                second_root / ".continual" / "candidates",
            )
            shutil.copytree(
                rehydrated_root / ".continual" / "system",
                second_root / ".continual" / "system",
            )
            second_tree_before = _tree_snapshot(second_root, extended_ids)
            second_trials_before = _trial_snapshots(second_root, extended_ids)
            second_copy_byte_identical = (
                second_tree_before == extended_tree_before_second_restart
            )
            second_trials_byte_identical = (
                second_trials_before == extended_trials_before_second_restart
            )
            if not second_copy_byte_identical:
                raise PostRehydrationContinualExtensionError(
                    "second rehydration changed extended Candidate bytes"
                )
            if not second_trials_byte_identical:
                raise PostRehydrationContinualExtensionError(
                    "second rehydration changed extended Candidate trials"
                )

            second_replay_runs = _replay_accumulated(
                second_root,
                heterogeneous_ids=heterogeneous_ids,
                recursive_id=recursive_id,
                extension_id=extension_id,
            )
            second_state_unchanged_after_replay = (
                _tree_snapshot(second_root, extended_ids) == second_tree_before
            )
            second_trials_unchanged_after_replay = (
                _trial_snapshots(second_root, extended_ids) == second_trials_before
            )
            if not second_state_unchanged_after_replay:
                raise PostRehydrationContinualExtensionError(
                    "second restart replay changed extended Candidate state"
                )
            if not second_trials_unchanged_after_replay:
                raise PostRehydrationContinualExtensionError(
                    "second restart replay changed extended Candidate trials"
                )

        post_extension_tree = _tree_snapshot(rehydrated_root, prior_ids)
        post_extension_trials = _trial_snapshots(rehydrated_root, prior_ids)
        if post_extension_tree != prior_tree_before_extension:
            raise PostRehydrationContinualExtensionError(
                "post-rehydration extension changed prior Candidate state"
            )
        if post_extension_trials != prior_trials_before_extension:
            raise PostRehydrationContinualExtensionError(
                "post-rehydration extension changed prior Candidate trials"
            )

    source_tree_after = _tree_snapshot(root, prior_ids)
    source_trials_after = _trial_snapshots(root, prior_ids)
    source_state_unchanged = source_tree_after == source_tree_before
    source_trials_unchanged = source_trials_after == source_trials_before
    if not source_state_unchanged or not source_trials_unchanged:
        raise PostRehydrationContinualExtensionError(
            "multi-session extension mutated source durable state"
        )

    replay_runs = [*first_replay_runs, *second_replay_runs]
    if len(set(replay_runs)) != len(replay_runs):
        raise PostRehydrationContinualExtensionError(
            "multi-session extension reused a supposedly fresh Engine run"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "post-rehydration-continual-extension-v1",
        "prior_candidate_ids": prior_ids,
        "heterogeneous_candidate_ids": heterogeneous_ids,
        "recursive_component_candidate_ids": recursive_component_ids,
        "recursive_candidate_id": recursive_id,
        "extension_candidate_id": extension_id,
        "extension_scope": extension["scope"],
        "extension_target_task_id": extension["target_task_id"],
        "extension_negative_evidence_retained": bool(
            extension["negative_evidence_retained"]
        ),
        "prior_state_unchanged_after_extension": prior_state_unchanged_after_extension,
        "prior_trials_unchanged_after_extension": prior_trials_unchanged_after_extension,
        "first_rehydrated_root_is_distinct": True,
        "second_rehydrated_root_is_distinct": True,
        "prior_runs_copied": False,
        "prior_episodes_copied": False,
        "prior_evidence_copied": False,
        "second_copy_byte_identical": second_copy_byte_identical,
        "second_trials_byte_identical": second_trials_byte_identical,
        "second_state_unchanged_after_replay": second_state_unchanged_after_replay,
        "second_trials_unchanged_after_replay": second_trials_unchanged_after_replay,
        "source_candidate_state_unchanged": source_state_unchanged,
        "source_trial_ledgers_unchanged": source_trials_unchanged,
        "fresh_engine_run_count": len(replay_runs),
        "fresh_engine_runs": replay_runs,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal deterministic multi-session continual-learning evidence only. A distinct rehydrated "
            "root can acquire a new exact-scope bounded skill without changing prior Candidate state, "
            "and old plus new skills survive a second state-only restart. Repository-authored tasks, "
            "synthesis grammar, regression measurements, and evaluators do not establish open-domain "
            "generality or independent production AGI."
        ),
    }
    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "post-rehydration-continual-extension"
        / f"extension-{_digest(seed)[:16]}.json",
        report,
    )
    return report
