from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.cross_family_order_retention import _trial_snapshots
from agi.durable_state_rehydration import _tree_snapshot
from agi.failure_driven_durable_acquisition import run_failure_driven_durable_acquisition
from agi.heterogeneous_retention_campaign import _digest
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.verified_macro_library_discovery import (
    discover_verified_acquired_program_ids,
    synthesize_from_verified_macro_library,
)
from agi.verified_macro_synthesis import _load_verified_macro_items


class AcquiredSkillCompositionError(RuntimeError):
    pass


def run_acquired_skill_composition(root: Path, seed: str) -> dict[str, Any]:
    """Reuse a failure-driven acquired skill inside a new autonomous composition after restart.

    A prior bounded gap-filling campaign must first learn, regression-verify, transactionally retain,
    and rediscover a numeric square skill. Only durable Candidate/system state is then copied into a
    distinct root. Without caller-supplied Candidate IDs, the fresh root receives a new negative-square
    behavior. The verified library contains shorter one-stage numeric routes, including the newly
    acquired square and retained negation skills, but neither solves the new behavior alone. Behavior-
    guided synthesis must uniquely compose square followed by negation, reject all shorter type-correct
    routes, and transfer the plan to post-selection challenges through fresh Engine chains while every
    durable Candidate byte and trial ledger remains unchanged.

    This is repository-authored bounded self-improvement transfer evidence only; the acquisition task,
    examples, synthesis grammar, regression measurements, search, and evaluator are not independent.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    acquired = run_failure_driven_durable_acquisition(
        root,
        f"{seed}:failure-driven-source",
    )
    if not acquired.get("passed"):
        raise AcquiredSkillCompositionError(
            "failure-driven durable acquisition prerequisite did not pass"
        )
    square_id = str(acquired["new_candidate_id"])

    library_ids = discover_verified_acquired_program_ids(root, max_candidates=64)
    negation_ids = [
        candidate_id
        for candidate_id in library_ids
        if candidate_id.startswith("hetero-numeric-neg-session3-")
    ]
    if len(negation_ids) != 1:
        raise AcquiredSkillCompositionError(
            "expected exactly one transactionally retained numeric-negation Candidate"
        )
    negation_id = negation_ids[0]
    if square_id not in library_ids:
        raise AcquiredSkillCompositionError(
            "failure-driven square Candidate is absent from durable verified library"
        )

    protected_ids = list(library_ids)
    protected_tree_before = _tree_snapshot(root, protected_ids)
    protected_trials_before = _trial_snapshots(root, protected_ids)

    with tempfile.TemporaryDirectory(prefix="agi-acquired-skill-composition-") as temporary:
        fresh_root = Path(temporary).resolve()
        _copy_persistent_state(root, fresh_root)
        prior_runs_copied = (fresh_root / ".continual" / "runs").exists()
        prior_episodes_copied = (fresh_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (fresh_root / ".continual" / "evidence").exists()
        fresh_tree_before = _tree_snapshot(fresh_root, protected_ids)
        fresh_trials_before = _trial_snapshots(fresh_root, protected_ids)
        if fresh_tree_before != protected_tree_before:
            raise AcquiredSkillCompositionError(
                "fresh restart changed durable acquired-program Candidate bytes"
            )
        if fresh_trials_before != protected_trials_before:
            raise AcquiredSkillCompositionError(
                "fresh restart changed durable acquired-program trial ledgers"
            )

        planning_examples = (
            ProgramExample(-12, -144),
            ProgramExample(7, -49),
            ProgramExample(10, -100),
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
            raise AcquiredSkillCompositionError(
                "negative-square composition unexpectedly received Candidate IDs"
            )
        selected_ids = [str(value) for value in synthesized["selected_candidate_ids"]]
        if synthesized["shortest_type_chain_depth"] != 1:
            raise AcquiredSkillCompositionError(
                "negative-square task lacks shorter type-correct one-stage routes"
            )
        if synthesized["selected_chain_depth"] != 2:
            raise AcquiredSkillCompositionError(
                "negative-square task did not require two durable skills"
            )
        if selected_ids != [square_id, negation_id]:
            raise AcquiredSkillCompositionError(
                "autonomous composition did not reuse acquired square then retained negation"
            )

        selected_chain = _load_verified_macro_items(fresh_root, selected_ids)
        commitment = _digest(
            {
                "seed": seed,
                "acquisition_digest": acquired["digest"],
                "selection_digest": synthesized["digest"],
                "phase": "post-selection-negative-square-challenge-v1",
            }
        )
        challenge_values = (
            21 + int(commitment[:4], 16) % 70,
            -(21 + int(commitment[4:8], 16) % 70),
        )
        support_values = {
            -12,
            7,
            10,
            2,
            -4,
            6,
            -9,
            12,
            -7,
            9,
            11,
            -8,
            13,
            4,
            -6,
            0,
            19,
        }
        if set(challenge_values) & support_values:
            raise AcquiredSkillCompositionError(
                "post-selection challenge overlaps acquisition or planning support"
            )

        compiled_program = synthesized["compiled_program"]
        fresh_runs: list[str] = []
        stage_count = 0
        for value in challenge_values:
            expected = -(value * value)
            if execute_program(compiled_program, value) != expected:
                raise AcquiredSkillCompositionError(
                    "compiled negative-square program failed post-selection challenge"
                )
            output, run_id, refs = _execute_chain(fresh_root, selected_chain, value)
            if output != expected:
                raise AcquiredSkillCompositionError(
                    "fresh Engine acquired-skill composition failed challenge"
                )
            fresh_runs.append(run_id)
            stage_count += len(refs)
        if len(set(fresh_runs)) != len(fresh_runs):
            raise AcquiredSkillCompositionError(
                "acquired-skill composition reused a supposedly fresh Engine run"
            )
        if stage_count != 4:
            raise AcquiredSkillCompositionError(
                "two two-stage challenges did not execute four learned stages"
            )

        fresh_state_unchanged = (
            _tree_snapshot(fresh_root, protected_ids) == fresh_tree_before
        )
        fresh_trials_unchanged = (
            _trial_snapshots(fresh_root, protected_ids) == fresh_trials_before
        )
        if not fresh_state_unchanged or not fresh_trials_unchanged:
            raise AcquiredSkillCompositionError(
                "acquired-skill composition changed fresh durable learning state"
            )

    source_state_unchanged = _tree_snapshot(root, protected_ids) == protected_tree_before
    source_trials_unchanged = (
        _trial_snapshots(root, protected_ids) == protected_trials_before
    )
    if not source_state_unchanged or not source_trials_unchanged:
        raise AcquiredSkillCompositionError(
            "fresh acquired-skill composition mutated durable source state"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "failure-driven-acquired-skill-composition-v1",
        "failure_driven_acquisition_digest": acquired["digest"],
        "library_candidate_count": len(library_ids),
        "caller_supplied_candidate_ids": False,
        "newly_acquired_square_candidate_id": square_id,
        "retained_negation_candidate_id": negation_id,
        "shortest_type_chain_depth": int(synthesized["shortest_type_chain_depth"]),
        "selected_chain_depth": int(synthesized["selected_chain_depth"]),
        "selected_candidate_ids": selected_ids,
        "newly_acquired_skill_reused_in_composition": selected_ids[0] == square_id,
        "shorter_type_correct_routes_rejected_by_behavior": True,
        "challenge_generated_after_selection": True,
        "post_selection_challenge_commitment": commitment,
        "challenge_case_count": len(challenge_values),
        "fresh_engine_runs": fresh_runs,
        "fresh_engine_runs_unique": len(set(fresh_runs)) == len(fresh_runs),
        "stage_invocation_count": stage_count,
        "fresh_state_unchanged": fresh_state_unchanged,
        "fresh_trials_unchanged": fresh_trials_unchanged,
        "source_state_unchanged": source_state_unchanged,
        "source_trials_unchanged": source_trials_unchanged,
        "prior_runs_copied": prior_runs_copied,
        "prior_episodes_copied": prior_episodes_copied,
        "prior_evidence_copied": prior_evidence_copied,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded self-improvement transfer evidence only. A skill acquired only after a "
            "prior fail-closed gap was regression-verified and durably retained, then reused after a "
            "fresh restart as the first stage of a newly behavior-selected composition with an older "
            "retained skill. The tasks, examples, synthesis grammar, search, scoring, and evaluator are "
            "repository-authored and therefore do not establish open-domain self-improvement or AGI."
        ),
    }
    if not all(
        (
            report["newly_acquired_skill_reused_in_composition"],
            report["shorter_type_correct_routes_rejected_by_behavior"],
            report["challenge_generated_after_selection"],
            report["fresh_engine_runs_unique"],
            report["fresh_state_unchanged"],
            report["fresh_trials_unchanged"],
            report["source_state_unchanged"],
            report["source_trials_unchanged"],
            not report["prior_runs_copied"],
            not report["prior_episodes_copied"],
            not report["prior_evidence_copied"],
        )
    ):
        raise AcquiredSkillCompositionError(
            "acquired-skill composition aggregate invariant failed"
        )
    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "acquired-skill-composition"
        / f"composition-{_digest(seed)[:16]}.json",
        report,
    )
    return report
