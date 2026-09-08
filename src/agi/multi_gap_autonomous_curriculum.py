from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.durable_state_rehydration import _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.transactional_multisession_commit import (
    _commit_verified_candidate,
    run_transactional_multisession_commit,
)
from agi.verified_macro_library_discovery import (
    VerifiedMacroLibraryError,
    discover_verified_acquired_program_ids,
    synthesize_from_verified_macro_library,
)
from agi.verified_macro_synthesis import _load_verified_macro_items


class MultiGapAutonomousCurriculumError(RuntimeError):
    pass


def _all_candidate_ids(root: Path) -> list[str]:
    base = root / ".continual" / "candidates"
    if not base.exists():
        return []
    return sorted(
        path.name
        for path in base.iterdir()
        if path.is_dir() and (path / "candidate.json").is_file()
    )


def _trial_snapshot(root: Path, candidate_ids: Sequence[str]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for candidate_id in candidate_ids:
        trial_dir = root / ".continual" / "candidates" / candidate_id / "trials"
        values: dict[str, str] = {}
        if trial_dir.is_dir():
            for path in sorted(item for item in trial_dir.rglob("*") if item.is_file()):
                values[path.relative_to(trial_dir).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
        result[candidate_id] = values
    return result


def _square_spec() -> dict[str, Any]:
    return {
        "name": "curriculum-square",
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


def _numeric_to_string_spec() -> dict[str, Any]:
    return {
        "name": "curriculum-numeric-to-string",
        "input_domain": "numeric",
        "output_domain": "string",
        "examples": (
            ProgramExample(3, "3"),
            ProgramExample(-8, "-8"),
            ProgramExample(12, "12"),
        ),
        "probe": -15,
        "expected": "-15",
        "max_nodes": 2,
    }


def _expect_gap(
    root: Path,
    *,
    input_domain: str,
    output_domain: str,
    examples: Sequence[ProgramExample],
    max_depth: int,
) -> bool:
    try:
        synthesize_from_verified_macro_library(
            root,
            input_domain=input_domain,
            output_domain=output_domain,
            examples=examples,
            max_depth=max_depth,
            max_candidates=64,
        )
    except (VerifiedMacroLibraryError, RuntimeError):
        return True
    return False


def _learn_and_commit_gap(
    durable_root: Path,
    *,
    seed: str,
    spec: Mapping[str, Any],
    gap_examples: Sequence[ProgramExample],
    max_depth: int,
) -> dict[str, Any]:
    durable_root = durable_root.resolve()
    prior_ids = _all_candidate_ids(durable_root)
    prior_tree = _tree_snapshot(durable_root, prior_ids)
    prior_trials = _trial_snapshot(durable_root, prior_ids)

    with tempfile.TemporaryDirectory(prefix="agi-multigap-learning-session-") as temporary:
        session_root = Path(temporary).resolve()
        _copy_persistent_state(durable_root, session_root)
        if _tree_snapshot(session_root, prior_ids) != prior_tree:
            raise MultiGapAutonomousCurriculumError(
                "learning-session restart changed prior Candidate bytes"
            )
        if _trial_snapshot(session_root, prior_ids) != prior_trials:
            raise MultiGapAutonomousCurriculumError(
                "learning-session restart changed prior Candidate trial ledgers"
            )

        gap_failed_closed = _expect_gap(
            session_root,
            input_domain=str(spec["input_domain"]),
            output_domain=str(spec["output_domain"]),
            examples=gap_examples,
            max_depth=max_depth,
        )
        if not gap_failed_closed:
            raise MultiGapAutonomousCurriculumError(
                f"durable library unexpectedly solved pre-learning gap {spec['name']}"
            )

        promoted = _promote_task(
            session_root,
            seed=seed,
            spec=dict(spec),
        )
        candidate_id = str(promoted["candidate_id"])
        scope = str(promoted["scope"])
        if candidate_id in prior_ids:
            raise MultiGapAutonomousCurriculumError(
                "gap acquisition reused an existing Candidate identity"
            )
        if not promoted.get("negative_evidence_retained"):
            raise MultiGapAutonomousCurriculumError(
                "gap acquisition did not retain protected-regression negative evidence"
            )
        if _tree_snapshot(session_root, prior_ids) != prior_tree:
            raise MultiGapAutonomousCurriculumError(
                "learning a gap changed prior Candidate bytes"
            )
        if _trial_snapshot(session_root, prior_ids) != prior_trials:
            raise MultiGapAutonomousCurriculumError(
                "learning a gap changed prior Candidate trial ledgers"
            )

        commit = _commit_verified_candidate(
            session_root,
            durable_root,
            candidate_id=candidate_id,
            scope=scope,
        )

    if _tree_snapshot(durable_root, prior_ids) != prior_tree:
        raise MultiGapAutonomousCurriculumError(
            "transactional gap commit changed prior Candidate bytes"
        )
    if _trial_snapshot(durable_root, prior_ids) != prior_trials:
        raise MultiGapAutonomousCurriculumError(
            "transactional gap commit changed prior Candidate trial ledgers"
        )
    return {
        "name": str(spec["name"]),
        "candidate_id": candidate_id,
        "scope": scope,
        "gap_failed_closed_before_learning": gap_failed_closed,
        "negative_evidence_retained": bool(promoted["negative_evidence_retained"]),
        "commit": commit,
        "prior_candidate_count": len(prior_ids),
    }


def run_multi_gap_autonomous_curriculum(root: Path, seed: str) -> dict[str, Any]:
    """Close two sequential durable-library gaps and compose both retained gains after restart.

    The curriculum starts from the transactionally retained multi-session capability base. In a fresh
    state-only learning session, numeric square behavior must first fail closed against the existing
    verified library, then a bounded square program is synthesized, exact-scope regression-verified,
    and transactionally retained. A second independent state-only session must observe that enlarged
    state, fail closed on a distinct numeric-to-string gap, learn and transactionally retain a bounded
    conversion skill, and preserve the first acquisition plus every older Candidate and trial ledger.

    A third fresh state-only root receives a new negative-square-to-string behavior with no Candidate
    IDs. One-stage and two-stage typed routes exist but are behaviorally insufficient. Library synthesis
    must discover and uniquely compose the first new skill (square), an older retained skill (negation),
    and the second new skill (numeric-to-string). Post-selection challenges cover both input signs via
    compiled and fresh mechanical Engine execution without changing any durable learning evidence.

    This is bounded repository-authored curriculum/self-improvement evidence, not independent AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    base = run_transactional_multisession_commit(
        root,
        f"{seed}:transactional-base",
    )
    if not base.get("passed"):
        raise MultiGapAutonomousCurriculumError(
            "transactional capability base did not pass"
        )
    base_ids = _all_candidate_ids(root)
    base_tree_before = _tree_snapshot(root, base_ids)
    base_trials_before = _trial_snapshot(root, base_ids)

    square_gap_examples = (
        ProgramExample(14, 196),
        ProgramExample(-10, 100),
        ProgramExample(9, 81),
    )
    square = _learn_and_commit_gap(
        root,
        seed=f"{seed}:session1-square",
        spec=_square_spec(),
        gap_examples=square_gap_examples,
        max_depth=2,
    )
    square_id = str(square["candidate_id"])
    after_square_ids = _all_candidate_ids(root)
    if square_id not in after_square_ids:
        raise MultiGapAutonomousCurriculumError(
            "first gap acquisition was not durably retained"
        )
    square_tree_before_second = _tree_snapshot(root, after_square_ids)
    square_trials_before_second = _trial_snapshot(root, after_square_ids)

    to_string_gap_examples = (
        ProgramExample(17, "17"),
        ProgramExample(-12, "-12"),
        ProgramExample(4, "4"),
    )
    to_string = _learn_and_commit_gap(
        root,
        seed=f"{seed}:session2-to-string",
        spec=_numeric_to_string_spec(),
        gap_examples=to_string_gap_examples,
        max_depth=3,
    )
    to_string_id = str(to_string["candidate_id"])
    if to_string_id == square_id:
        raise MultiGapAutonomousCurriculumError(
            "two curriculum gaps reused one Candidate identity"
        )
    if _tree_snapshot(root, after_square_ids) != square_tree_before_second:
        raise MultiGapAutonomousCurriculumError(
            "second gap learning changed first-session durable Candidate bytes"
        )
    if _trial_snapshot(root, after_square_ids) != square_trials_before_second:
        raise MultiGapAutonomousCurriculumError(
            "second gap learning changed first-session durable trials"
        )

    final_ids = _all_candidate_ids(root)
    final_tree_before = _tree_snapshot(root, final_ids)
    final_trials_before = _trial_snapshot(root, final_ids)
    library_ids = discover_verified_acquired_program_ids(root, max_candidates=64)
    negation_ids = [
        candidate_id
        for candidate_id in library_ids
        if candidate_id.startswith("hetero-numeric-neg-session3-")
    ]
    if len(negation_ids) != 1:
        raise MultiGapAutonomousCurriculumError(
            "expected exactly one retained numeric-negation Candidate"
        )
    negation_id = negation_ids[0]
    for required in (square_id, negation_id, to_string_id):
        if required not in library_ids:
            raise MultiGapAutonomousCurriculumError(
                f"verified durable library omitted required curriculum skill: {required}"
            )

    with tempfile.TemporaryDirectory(prefix="agi-multigap-final-restart-") as temporary:
        fresh_root = Path(temporary).resolve()
        _copy_persistent_state(root, fresh_root)
        prior_runs_copied = (fresh_root / ".continual" / "runs").exists()
        prior_episodes_copied = (fresh_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (fresh_root / ".continual" / "evidence").exists()
        if _tree_snapshot(fresh_root, final_ids) != final_tree_before:
            raise MultiGapAutonomousCurriculumError(
                "final restart changed accumulated Candidate bytes"
            )
        if _trial_snapshot(fresh_root, final_ids) != final_trials_before:
            raise MultiGapAutonomousCurriculumError(
                "final restart changed accumulated Candidate trials"
            )

        planning_examples = (
            ProgramExample(7, "-49"),
            ProgramExample(-5, "-25"),
            ProgramExample(11, "-121"),
        )
        synthesized = synthesize_from_verified_macro_library(
            fresh_root,
            input_domain="numeric",
            output_domain="string",
            examples=planning_examples,
            max_depth=3,
            max_candidates=64,
        )
        if synthesized.get("candidate_ids_supplied_by_caller") is not False:
            raise MultiGapAutonomousCurriculumError(
                "final curriculum composition relied on caller Candidate IDs"
            )
        selected_ids = [str(value) for value in synthesized["selected_candidate_ids"]]
        expected_selected = [square_id, negation_id, to_string_id]
        if synthesized["shortest_type_chain_depth"] != 1:
            raise MultiGapAutonomousCurriculumError(
                "final curriculum task lacks shorter type-correct routes"
            )
        if synthesized["selected_chain_depth"] != 3:
            raise MultiGapAutonomousCurriculumError(
                "final curriculum task did not require three retained skills"
            )
        if selected_ids != expected_selected:
            raise MultiGapAutonomousCurriculumError(
                "final curriculum did not compose both new gains around retained negation"
            )

        selected_chain = _load_verified_macro_items(fresh_root, selected_ids)
        commitment = _digest(
            {
                "seed": seed,
                "base_digest": base["digest"],
                "square_id": square_id,
                "to_string_id": to_string_id,
                "selection_digest": synthesized["digest"],
                "phase": "post-selection-multigap-curriculum-challenge-v1",
            }
        )
        challenge_values = (
            23 + int(commitment[:4], 16) % 80,
            -(23 + int(commitment[4:8], 16) % 80),
        )
        support_values = {
            14,
            -10,
            9,
            2,
            -4,
            6,
            -9,
            17,
            -12,
            4,
            3,
            -8,
            12,
            -15,
            7,
            -5,
            11,
            0,
            -6,
            19,
        }
        if set(challenge_values) & support_values:
            raise MultiGapAutonomousCurriculumError(
                "final challenges overlap acquisition or planning support"
            )

        compiled_program = synthesized["compiled_program"]
        fresh_runs: list[str] = []
        stage_count = 0
        for value in challenge_values:
            expected = str(-(value * value))
            if execute_program(compiled_program, value) != expected:
                raise MultiGapAutonomousCurriculumError(
                    "compiled three-stage curriculum plan failed challenge"
                )
            output, run_id, refs = _execute_chain(fresh_root, selected_chain, value)
            if output != expected:
                raise MultiGapAutonomousCurriculumError(
                    "fresh Engine three-stage curriculum plan failed challenge"
                )
            fresh_runs.append(run_id)
            stage_count += len(refs)
        if len(set(fresh_runs)) != len(fresh_runs):
            raise MultiGapAutonomousCurriculumError(
                "final curriculum reused a supposedly fresh Engine run"
            )
        if stage_count != 6:
            raise MultiGapAutonomousCurriculumError(
                "two three-stage challenges did not execute six learned stages"
            )

        fresh_state_unchanged = _tree_snapshot(fresh_root, final_ids) == final_tree_before
        fresh_trials_unchanged = _trial_snapshot(fresh_root, final_ids) == final_trials_before
        if not fresh_state_unchanged or not fresh_trials_unchanged:
            raise MultiGapAutonomousCurriculumError(
                "final autonomous composition changed accumulated learning state"
            )

    source_state_unchanged_after_final = _tree_snapshot(root, final_ids) == final_tree_before
    source_trials_unchanged_after_final = _trial_snapshot(root, final_ids) == final_trials_before
    base_state_still_present = all(candidate_id in final_ids for candidate_id in base_ids)
    if not source_state_unchanged_after_final or not source_trials_unchanged_after_final:
        raise MultiGapAutonomousCurriculumError(
            "final fresh composition mutated durable source state"
        )
    if not base_state_still_present:
        raise MultiGapAutonomousCurriculumError(
            "multi-gap curriculum lost a pre-existing Candidate"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "multi-gap-autonomous-curriculum-v1",
        "transactional_base_digest": base["digest"],
        "base_candidate_count": len(base_ids),
        "first_gap": square,
        "second_gap": to_string,
        "sequential_gap_count": 2,
        "first_gap_failed_closed_before_learning": bool(square["gap_failed_closed_before_learning"]),
        "second_gap_failed_closed_before_learning": bool(to_string["gap_failed_closed_before_learning"]),
        "first_acquired_candidate_id": square_id,
        "second_acquired_candidate_id": to_string_id,
        "first_acquisition_preserved_during_second": (
            _tree_snapshot(root, after_square_ids) == square_tree_before_second
            and _trial_snapshot(root, after_square_ids) == square_trials_before_second
        ),
        "caller_supplied_candidate_ids_for_final_composition": False,
        "final_selected_candidate_ids": selected_ids,
        "final_selected_chain_depth": int(synthesized["selected_chain_depth"]),
        "final_shortest_type_chain_depth": int(synthesized["shortest_type_chain_depth"]),
        "both_new_skills_reused_in_final_composition": (
            square_id in selected_ids and to_string_id in selected_ids
        ),
        "older_retained_skill_reused_in_final_composition": negation_id in selected_ids,
        "challenge_generated_after_final_selection": True,
        "post_selection_challenge_commitment": commitment,
        "fresh_engine_runs": fresh_runs,
        "fresh_engine_runs_unique": len(set(fresh_runs)) == len(fresh_runs),
        "stage_invocation_count": stage_count,
        "fresh_state_unchanged": fresh_state_unchanged,
        "fresh_trials_unchanged": fresh_trials_unchanged,
        "source_state_unchanged_after_final": source_state_unchanged_after_final,
        "source_trials_unchanged_after_final": source_trials_unchanged_after_final,
        "base_state_still_present": base_state_still_present,
        "prior_runs_copied": prior_runs_copied,
        "prior_episodes_copied": prior_episodes_copied,
        "prior_evidence_copied": prior_evidence_copied,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded multi-gap curriculum evidence only. Two distinct unsupported behaviors "
            "failed closed in separate fresh sessions, were each learned through finite synthesis, "
            "protected exact-scope regression, and transactional durable admission, then both new skills "
            "were autonomously composed with an older retained skill after another restart. The curriculum, "
            "examples, grammar, regression measurements, search, scoring, and evaluator remain repository-authored; "
            "this does not establish open-domain autonomous self-improvement or independent AGI."
        ),
    }
    if not all(
        (
            report["first_gap_failed_closed_before_learning"],
            report["second_gap_failed_closed_before_learning"],
            report["first_acquisition_preserved_during_second"],
            report["both_new_skills_reused_in_final_composition"],
            report["older_retained_skill_reused_in_final_composition"],
            report["challenge_generated_after_final_selection"],
            report["fresh_engine_runs_unique"],
            report["fresh_state_unchanged"],
            report["fresh_trials_unchanged"],
            report["source_state_unchanged_after_final"],
            report["source_trials_unchanged_after_final"],
            report["base_state_still_present"],
            not report["prior_runs_copied"],
            not report["prior_episodes_copied"],
            not report["prior_evidence_copied"],
        )
    ):
        raise MultiGapAutonomousCurriculumError(
            "multi-gap autonomous curriculum aggregate invariant failed"
        )
    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "multi-gap-autonomous-curriculum"
        / f"curriculum-{_digest(seed)[:16]}.json",
        report,
    )
    return report
