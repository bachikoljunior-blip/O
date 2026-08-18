from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.cross_family_order_retention import _trial_snapshots
from agi.durable_state_rehydration import _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.transactional_multisession_commit import run_transactional_multisession_commit
from agi.verified_macro_library_discovery import synthesize_from_verified_macro_library
from agi.verified_macro_synthesis import _load_verified_macro_items


class AutonomousDurableCompositionError(RuntimeError):
    pass


def run_autonomous_durable_composition(root: Path, seed: str) -> dict[str, Any]:
    """Compose durable verified skills after restart without caller-supplied Candidate IDs.

    The source first transactionally commits two session-learned skills and then reconstructs only
    Candidate/system state into a distinct root. The fresh root receives a new numeric behavior task:
    return negative absolute value. Its durable verified library contains shorter one-stage numeric
    routes (absolute value and negation), neither of which satisfies the behavior. Library synthesis
    must discover candidates itself, reject those shorter type-correct routes behaviorally, uniquely
    select absolute-value followed by the newly committed negation skill, and compile that two-stage
    route. Post-selection challenges exercise both signs through the compiled descriptor and through
    fresh mechanical Engine chains while all Candidate bytes and trial ledgers remain unchanged.

    This is bounded repository-authored internal composition/autonomy evidence. It does not establish
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
        raise AutonomousDurableCompositionError(
            "transactional durable-state prerequisite did not pass"
        )

    source_ids = [str(value) for value in transactional["source_candidate_ids"]]
    negation_id = str(transactional["second_committed_candidate_id"])
    lower_id = str(transactional["first_committed_candidate_id"])
    abs_ids = [candidate_id for candidate_id in source_ids if candidate_id.startswith("hetero-numeric-abs-")]
    if len(abs_ids) != 1:
        raise AutonomousDurableCompositionError(
            "expected exactly one durable numeric-absolute source Candidate"
        )
    absolute_id = abs_ids[0]
    protected_ids = [*source_ids, lower_id, negation_id]
    if len(protected_ids) != len(set(protected_ids)):
        raise AutonomousDurableCompositionError(
            "durable composition source contains duplicate Candidate identities"
        )

    source_tree_before = _tree_snapshot(root, protected_ids)
    source_trials_before = _trial_snapshots(root, protected_ids)

    with tempfile.TemporaryDirectory(prefix="agi-autonomous-durable-composition-") as temporary:
        fresh_root = Path(temporary).resolve()
        _copy_persistent_state(root, fresh_root)
        prior_runs_copied = (fresh_root / ".continual" / "runs").exists()
        prior_episodes_copied = (fresh_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (fresh_root / ".continual" / "evidence").exists()

        fresh_tree_before = _tree_snapshot(fresh_root, protected_ids)
        fresh_trials_before = _trial_snapshots(fresh_root, protected_ids)
        if fresh_tree_before != source_tree_before:
            raise AutonomousDurableCompositionError(
                "fresh restart changed durable Candidate bytes"
            )
        if fresh_trials_before != source_trials_before:
            raise AutonomousDurableCompositionError(
                "fresh restart changed durable Candidate trials"
            )

        planning_examples = (
            ProgramExample(-17, -17),
            ProgramExample(8, -8),
            ProgramExample(-2, -2),
        )
        synthesized = synthesize_from_verified_macro_library(
            fresh_root,
            input_domain="numeric",
            output_domain="numeric",
            examples=planning_examples,
            max_depth=2,
            max_candidates=64,
        )
        if synthesized.get("candidate_ids_supplied_by_caller") is not False:
            raise AutonomousDurableCompositionError(
                "composition unexpectedly relied on caller Candidate IDs"
            )
        selected_ids = [str(value) for value in synthesized["selected_candidate_ids"]]
        if synthesized["shortest_type_chain_depth"] != 1:
            raise AutonomousDurableCompositionError(
                "composition task lacks a shorter type-correct one-stage distractor"
            )
        if synthesized["selected_chain_depth"] != 2:
            raise AutonomousDurableCompositionError(
                "behavior-guided composition did not require two retained skills"
            )
        if selected_ids != [absolute_id, negation_id]:
            raise AutonomousDurableCompositionError(
                "behavior-guided durable composition selected the wrong route"
            )
        if absolute_id not in synthesized["library_candidate_ids"] or negation_id not in synthesized["library_candidate_ids"]:
            raise AutonomousDurableCompositionError(
                "durable library omitted a required verified component"
            )

        selected_chain = _load_verified_macro_items(fresh_root, selected_ids)
        commitment = _digest(
            {
                "seed": seed,
                "selection_digest": synthesized["digest"],
                "selected_ids": selected_ids,
                "phase": "post-selection-negative-absolute-challenge-v1",
            }
        )
        challenge_values = (
            -(31 + int(commitment[:4], 16) % 300),
            31 + int(commitment[4:8], 16) % 300,
        )
        if challenge_values[0] >= 0 or challenge_values[1] <= 0:
            raise AutonomousDurableCompositionError(
                "challenge generator did not cover both input signs"
            )
        if set(challenge_values) & {-17, 8, -2, -3, 0, 5, -11, 4, -6, 19}:
            raise AutonomousDurableCompositionError(
                "post-selection challenge overlapped planning or component support"
            )

        compiled_program = synthesized["compiled_program"]
        fresh_engine_runs: list[str] = []
        stage_invocation_count = 0
        outputs: list[dict[str, Any]] = []
        for value in challenge_values:
            expected = -abs(value)
            compiled = execute_program(compiled_program, value)
            if compiled != expected:
                raise AutonomousDurableCompositionError(
                    "compiled durable composition failed post-selection challenge"
                )
            output, run_id, refs = _execute_chain(fresh_root, selected_chain, value)
            if output != expected:
                raise AutonomousDurableCompositionError(
                    "fresh Engine durable composition failed post-selection challenge"
                )
            fresh_engine_runs.append(run_id)
            stage_invocation_count += len(refs)
            outputs.append(
                {
                    "input_sha256": _digest(value),
                    "output": output,
                    "expected": expected,
                    "run_id": run_id,
                    "stage_count": len(refs),
                }
            )

        if len(set(fresh_engine_runs)) != len(fresh_engine_runs):
            raise AutonomousDurableCompositionError(
                "composition reused a supposedly fresh Engine run"
            )
        if stage_invocation_count != 4:
            raise AutonomousDurableCompositionError(
                "two two-stage challenges did not execute exactly four learned stages"
            )

        fresh_candidate_state_unchanged = (
            _tree_snapshot(fresh_root, protected_ids) == fresh_tree_before
        )
        fresh_trial_state_unchanged = (
            _trial_snapshots(fresh_root, protected_ids) == fresh_trials_before
        )
        if not fresh_candidate_state_unchanged:
            raise AutonomousDurableCompositionError(
                "durable composition changed fresh Candidate state"
            )
        if not fresh_trial_state_unchanged:
            raise AutonomousDurableCompositionError(
                "durable composition changed fresh Candidate trials"
            )

    source_candidate_state_unchanged = (
        _tree_snapshot(root, protected_ids) == source_tree_before
    )
    source_trial_state_unchanged = (
        _trial_snapshots(root, protected_ids) == source_trials_before
    )
    if not source_candidate_state_unchanged or not source_trial_state_unchanged:
        raise AutonomousDurableCompositionError(
            "fresh composition mutated durable source Candidate state"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "autonomous-durable-library-composition-v1",
        "transactional_source_digest": transactional["digest"],
        "protected_candidate_count": len(protected_ids),
        "library_candidate_count": int(synthesized["library_candidate_count"]),
        "caller_supplied_candidate_ids": False,
        "shortest_type_chain_depth": int(synthesized["shortest_type_chain_depth"]),
        "selected_chain_depth": int(synthesized["selected_chain_depth"]),
        "selected_candidate_ids": selected_ids,
        "selected_absolute_candidate_id": absolute_id,
        "selected_newly_committed_negation_id": negation_id,
        "shorter_type_correct_routes_rejected_by_behavior": True,
        "challenge_generated_after_selection": True,
        "post_selection_challenge_commitment": commitment,
        "challenge_case_count": len(challenge_values),
        "fresh_engine_runs": fresh_engine_runs,
        "fresh_engine_runs_unique": len(set(fresh_engine_runs)) == len(fresh_engine_runs),
        "stage_invocation_count": stage_invocation_count,
        "all_outputs_matched": all(item["output"] == item["expected"] for item in outputs),
        "fresh_candidate_state_unchanged": fresh_candidate_state_unchanged,
        "fresh_trial_state_unchanged": fresh_trial_state_unchanged,
        "source_candidate_state_unchanged": source_candidate_state_unchanged,
        "source_trial_state_unchanged": source_trial_state_unchanged,
        "prior_runs_copied": prior_runs_copied,
        "prior_episodes_copied": prior_episodes_copied,
        "prior_evidence_copied": prior_evidence_copied,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded autonomous composition and transfer evidence only. A fresh state-only "
            "restart discovered the verified durable library without caller Candidate IDs, rejected "
            "shorter type-correct one-stage routes by behavior, selected durable absolute-value then "
            "the newly committed negation skill, and transferred the two-stage plan to post-selection "
            "challenges without changing Candidate state or trials. Repository-authored task behavior, "
            "search, scoring, compiler, and runtime do not establish open-domain autonomy or independent AGI."
        ),
    }
    if not all(
        (
            report["shorter_type_correct_routes_rejected_by_behavior"],
            report["challenge_generated_after_selection"],
            report["fresh_engine_runs_unique"],
            report["all_outputs_matched"],
            report["fresh_candidate_state_unchanged"],
            report["fresh_trial_state_unchanged"],
            report["source_candidate_state_unchanged"],
            report["source_trial_state_unchanged"],
            not report["prior_runs_copied"],
            not report["prior_episodes_copied"],
            not report["prior_evidence_copied"],
        )
    ):
        raise AutonomousDurableCompositionError(
            "autonomous durable composition aggregate invariant failed"
        )
    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "autonomous-durable-composition"
        / f"composition-{_digest(seed)[:16]}.json",
        report,
    )
    return report
