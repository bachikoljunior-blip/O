from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program
from agi.cross_family_order_retention import _trial_snapshots
from agi.durable_state_rehydration import _run_one, _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.transactional_multisession_commit import run_transactional_multisession_commit
from agi.verified_macro_library_discovery import (
    VerifiedMacroLibraryError,
    discover_verified_acquired_program_ids,
    synthesize_from_verified_macro_library,
)


class AutonomousDurableLibraryRetrievalError(RuntimeError):
    pass


def _select_exact_one_step(
    root: Path,
    *,
    input_domain: str,
    output_domain: str,
    examples: tuple[ProgramExample, ...],
    expected_candidate_id: str,
) -> dict[str, Any]:
    synthesized = synthesize_from_verified_macro_library(
        root,
        input_domain=input_domain,
        output_domain=output_domain,
        examples=examples,
        max_depth=2,
        max_candidates=64,
    )
    if synthesized.get("candidate_ids_supplied_by_caller") is not False:
        raise AutonomousDurableLibraryRetrievalError(
            "durable-library retrieval unexpectedly relied on caller Candidate IDs"
        )
    if synthesized.get("selected_chain_depth") != 1:
        raise AutonomousDurableLibraryRetrievalError(
            "durable-library retrieval did not choose a one-step retained skill"
        )
    selected = [str(value) for value in synthesized.get("selected_candidate_ids", [])]
    if selected != [expected_candidate_id]:
        raise AutonomousDurableLibraryRetrievalError(
            "behavior-guided durable-library retrieval selected the wrong Candidate"
        )
    return synthesized


def run_autonomous_durable_library_retrieval(root: Path, seed: str) -> dict[str, Any]:
    """Retrieve newly durable skills from state after a fresh restart without supplied IDs.

    A verified transactional multi-session campaign first admits two learned skills into durable
    Candidate state. A distinct root is then reconstructed from Candidate/system state only. The
    planner receives behavior examples and desired input/output domains, but no Candidate identities.
    It must discover the exact-scope verified durable library and uniquely select the newly committed
    string-lower and numeric-negation skills despite same-type distractors. An unsupported numeric
    behavior must fail closed instead of choosing an arbitrary route. Post-selection challenges are
    generated only after both selections and are checked through both the compiled descriptor and fresh
    mechanical Engine dispatch. Candidate bytes and trial ledgers must remain unchanged throughout.

    This is bounded repository-authored internal autonomy/transfer evidence. It does not establish
    open-domain task understanding, evaluator independence, or production AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    transactional = run_transactional_multisession_commit(
        root,
        f"{seed}:transactional-source",
    )
    if not transactional.get("passed"):
        raise AutonomousDurableLibraryRetrievalError(
            "transactional durable-state prerequisite did not pass"
        )
    lower_id = str(transactional["first_committed_candidate_id"])
    negation_id = str(transactional["second_committed_candidate_id"])
    protected_ids = [
        *[str(value) for value in transactional["source_candidate_ids"]],
        lower_id,
        negation_id,
    ]
    if len(protected_ids) != len(set(protected_ids)):
        raise AutonomousDurableLibraryRetrievalError(
            "transactional source contains duplicate Candidate identities"
        )

    source_tree_before = _tree_snapshot(root, protected_ids)
    source_trials_before = _trial_snapshots(root, protected_ids)

    with tempfile.TemporaryDirectory(prefix="agi-autonomous-library-restart-") as temporary:
        fresh_root = Path(temporary).resolve()
        _copy_persistent_state(root, fresh_root)
        prior_runs_copied = (fresh_root / ".continual" / "runs").exists()
        prior_episodes_copied = (fresh_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (fresh_root / ".continual" / "evidence").exists()

        fresh_tree_before = _tree_snapshot(fresh_root, protected_ids)
        fresh_trials_before = _trial_snapshots(fresh_root, protected_ids)
        if fresh_tree_before != source_tree_before:
            raise AutonomousDurableLibraryRetrievalError(
                "fresh restart changed durable Candidate bytes"
            )
        if fresh_trials_before != source_trials_before:
            raise AutonomousDurableLibraryRetrievalError(
                "fresh restart changed durable Candidate trials"
            )

        library_ids = discover_verified_acquired_program_ids(
            fresh_root,
            max_candidates=64,
        )
        if lower_id not in library_ids or negation_id not in library_ids:
            raise AutonomousDurableLibraryRetrievalError(
                "fresh durable-library discovery omitted a transactionally committed skill"
            )

        lower_examples = (
            ProgramExample("Fresh CASE", "fresh case"),
            ProgramExample("ANOTHER One", "another one"),
            ProgramExample("xYzZ", "xyzz"),
        )
        negation_examples = (
            ProgramExample(7, -7),
            ProgramExample(-9, 9),
            ProgramExample(13, -13),
        )
        lower_plan = _select_exact_one_step(
            fresh_root,
            input_domain="string",
            output_domain="string",
            examples=lower_examples,
            expected_candidate_id=lower_id,
        )
        negation_plan = _select_exact_one_step(
            fresh_root,
            input_domain="numeric",
            output_domain="numeric",
            examples=negation_examples,
            expected_candidate_id=negation_id,
        )

        unsupported_failed_closed = False
        try:
            synthesize_from_verified_macro_library(
                fresh_root,
                input_domain="numeric",
                output_domain="numeric",
                examples=(
                    ProgramExample(23, 529),
                    ProgramExample(24, 576),
                    ProgramExample(-25, 625),
                ),
                max_depth=2,
                max_candidates=64,
            )
        except (VerifiedMacroLibraryError, RuntimeError):
            unsupported_failed_closed = True
        if not unsupported_failed_closed:
            raise AutonomousDurableLibraryRetrievalError(
                "unsupported durable-library behavior did not fail closed"
            )

        commitment = _digest(
            {
                "seed": seed,
                "lower_selection": lower_plan["digest"],
                "negation_selection": negation_plan["digest"],
                "library_ids": library_ids,
                "phase": "post-selection-durable-library-challenge-v1",
            }
        )
        lower_input = f"Fresh-{commitment[:10].upper()}-CaSe"
        lower_expected = lower_input.lower()
        negation_input = 100 + int(commitment[10:14], 16) % 800
        negation_expected = -negation_input

        if execute_program(lower_plan["compiled_program"], lower_input) != lower_expected:
            raise AutonomousDurableLibraryRetrievalError(
                "compiled lower retrieval failed post-selection challenge"
            )
        if (
            execute_program(negation_plan["compiled_program"], negation_input)
            != negation_expected
        ):
            raise AutonomousDurableLibraryRetrievalError(
                "compiled negation retrieval failed post-selection challenge"
            )

        lower_run = _run_one(
            fresh_root,
            candidate_id=lower_id,
            value=lower_input,
            expected=lower_expected,
        )
        negation_run = _run_one(
            fresh_root,
            candidate_id=negation_id,
            value=negation_input,
            expected=negation_expected,
        )
        if lower_run == negation_run:
            raise AutonomousDurableLibraryRetrievalError(
                "post-selection challenges reused one Engine run"
            )

        fresh_tree_after = _tree_snapshot(fresh_root, protected_ids)
        fresh_trials_after = _trial_snapshots(fresh_root, protected_ids)
        fresh_candidate_state_unchanged = fresh_tree_after == fresh_tree_before
        fresh_trial_state_unchanged = fresh_trials_after == fresh_trials_before
        if not fresh_candidate_state_unchanged:
            raise AutonomousDurableLibraryRetrievalError(
                "autonomous retrieval changed fresh Candidate state"
            )
        if not fresh_trial_state_unchanged:
            raise AutonomousDurableLibraryRetrievalError(
                "autonomous retrieval changed fresh Candidate trials"
            )

    source_candidate_state_unchanged = (
        _tree_snapshot(root, protected_ids) == source_tree_before
    )
    source_trial_state_unchanged = (
        _trial_snapshots(root, protected_ids) == source_trials_before
    )
    if not source_candidate_state_unchanged or not source_trial_state_unchanged:
        raise AutonomousDurableLibraryRetrievalError(
            "fresh autonomous retrieval mutated durable source Candidate state"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "autonomous-durable-library-retrieval-v1",
        "transactional_source_digest": transactional["digest"],
        "protected_candidate_count": len(protected_ids),
        "library_candidate_count": len(library_ids),
        "library_contains_committed_lower": lower_id in library_ids,
        "library_contains_committed_negation": negation_id in library_ids,
        "caller_supplied_candidate_ids": False,
        "lower_selected_candidate_id": lower_id,
        "negation_selected_candidate_id": negation_id,
        "lower_selected_chain_depth": lower_plan["selected_chain_depth"],
        "negation_selected_chain_depth": negation_plan["selected_chain_depth"],
        "unsupported_behavior_failed_closed": unsupported_failed_closed,
        "post_selection_challenge_commitment": commitment,
        "fresh_engine_runs": [lower_run, negation_run],
        "fresh_engine_runs_unique": lower_run != negation_run,
        "fresh_candidate_state_unchanged": fresh_candidate_state_unchanged,
        "fresh_trial_state_unchanged": fresh_trial_state_unchanged,
        "source_candidate_state_unchanged": source_candidate_state_unchanged,
        "source_trial_state_unchanged": source_trial_state_unchanged,
        "prior_runs_copied": prior_runs_copied,
        "prior_episodes_copied": prior_episodes_copied,
        "prior_evidence_copied": prior_evidence_copied,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded autonomous-retrieval and transfer evidence only. After a fresh state-only "
            "restart, exact-scope verified acquired programs were discovered without caller Candidate "
            "IDs; behavior examples uniquely selected two newly durable skills against same-type "
            "distractors, unsupported behavior failed closed, and post-selection challenges replayed "
            "without changing Candidate state or trials. Repository-authored behavior specifications, "
            "search, scoring, and runtime do not establish open-domain autonomy or independent AGI."
        ),
    }
    if not all(
        (
            report["library_contains_committed_lower"],
            report["library_contains_committed_negation"],
            report["unsupported_behavior_failed_closed"],
            report["fresh_engine_runs_unique"],
            report["fresh_candidate_state_unchanged"],
            report["fresh_trial_state_unchanged"],
            report["source_candidate_state_unchanged"],
            report["source_trial_state_unchanged"],
            not report["prior_runs_copied"],
            not report["prior_episodes_copied"],
            not report["prior_evidence_copied"],
        )
    ):
        raise AutonomousDurableLibraryRetrievalError(
            "autonomous durable-library aggregate invariant failed"
        )
    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "autonomous-durable-library-retrieval"
        / f"retrieval-{_digest(seed)[:16]}.json",
        report,
    )
    return report
