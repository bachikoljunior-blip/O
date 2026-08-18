from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program
from agi.adaptive_depth_composition import (
    AdaptiveDepthCompositionError,
    _challenge_inputs,
    _execute_descriptor_chain,
    _planning_inputs,
    run_adaptive_depth_composition,
    synthesize_shallowest_verified_route,
)
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.durable_state_rehydration import _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.seeded_generated_goal_acquisition import _generated_goal_specs
from agi.verified_macro_synthesis import _load_verified_macro_items


class AdaptiveDepthPostAdditionRetentionError(RuntimeError):
    pass


def _later_object_predicate_spec(seed: str) -> dict[str, Any]:
    specs = _generated_goal_specs(f"{seed}:later-capability")
    try:
        return next(
            spec
            for spec in specs
            if spec["input_domain"] == "object" and spec["output_domain"] == "boolean"
        )
    except StopIteration as exc:  # pragma: no cover - fixed generated family contract
        raise AdaptiveDepthPostAdditionRetentionError(
            "generated later-capability family lacks object-to-boolean behavior"
        ) from exc


def run_adaptive_depth_postaddition_retention(root: Path, seed: str) -> dict[str, Any]:
    """Retain an adaptively selected composed route after learning an unrelated later skill.

    The prerequisite adaptive campaign selects a uniquely behavior-sufficient route without caller
    Candidate IDs and with an explicit depth/search budget. Its durable Candidate bytes and trial ledgers
    are snapshotted. A new independently regression-promoted object-to-boolean behavior is then added.
    A subsequent fresh state-only resolver must rediscover the original route at the same minimal depth,
    execute post-selection challenges correctly, and avoid selecting the later skill. An unsupported
    string-to-numeric gap must still fail closed.

    This is bounded repository-authored continual-learning evidence. It does not establish independent
    production evaluation or AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    initial_seed = f"{seed}:initial-adaptive"

    initial = run_adaptive_depth_composition(root, initial_seed)
    if not initial.get("passed"):
        raise AdaptiveDepthPostAdditionRetentionError("initial adaptive-depth campaign did not pass")
    initial_ids = _all_candidate_ids(root)
    initial_tree = _tree_snapshot(root, initial_ids)
    initial_trials = _trial_snapshot(root, initial_ids)
    if not all(initial_trials.get(candidate_id) for candidate_id in initial_ids):
        raise AdaptiveDepthPostAdditionRetentionError(
            "initial adaptive library has missing Candidate trial evidence"
        )

    hidden_ids = tuple(str(value) for value in initial["selected_candidate_ids"])
    if not hidden_ids:
        raise AdaptiveDepthPostAdditionRetentionError("initial adaptive route is empty")
    hidden_items = _load_verified_macro_items(root, hidden_ids)
    input_domain = str(hidden_items[0]["input_domain"])
    output_domain = str(hidden_items[-1]["output_domain"])
    planning_inputs = _planning_inputs(input_domain, initial_seed)
    planning_outputs = tuple(
        _execute_descriptor_chain(hidden_items, value) for value in planning_inputs
    )
    planning_examples = tuple(
        ProgramExample(value, output)
        for value, output in zip(planning_inputs, planning_outputs, strict=True)
    )

    later_spec = _later_object_predicate_spec(seed)
    later = _promote_task(
        root,
        seed=f"{seed}:later-object-predicate-promotion",
        spec=later_spec,
    )
    later_id = str(later["candidate_id"])
    if later_id in initial_ids:
        raise AdaptiveDepthPostAdditionRetentionError("later capability reused a prior Candidate ID")
    if not later.get("negative_evidence_retained"):
        raise AdaptiveDepthPostAdditionRetentionError(
            "later capability lost protected negative evidence"
        )
    if _tree_snapshot(root, initial_ids) != initial_tree:
        raise AdaptiveDepthPostAdditionRetentionError(
            "later capability addition changed prior Candidate bytes"
        )
    if _trial_snapshot(root, initial_ids) != initial_trials:
        raise AdaptiveDepthPostAdditionRetentionError(
            "later capability addition changed prior Candidate trials"
        )
    later_trials = _trial_snapshot(root, [later_id])
    if not later_trials.get(later_id):
        raise AdaptiveDepthPostAdditionRetentionError(
            "later capability lacks durable regression trials"
        )

    enlarged_ids = [*initial_ids, later_id]
    enlarged_tree = _tree_snapshot(root, enlarged_ids)
    enlarged_trials = _trial_snapshot(root, enlarged_ids)

    with tempfile.TemporaryDirectory(prefix="agi-adaptive-postaddition-") as temporary:
        fresh_root = Path(temporary).resolve()
        _copy_persistent_state(root, fresh_root)
        prior_runs_copied = (fresh_root / ".continual" / "runs").exists()
        prior_episodes_copied = (fresh_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (fresh_root / ".continual" / "evidence").exists()
        if _tree_snapshot(fresh_root, enlarged_ids) != enlarged_tree:
            raise AdaptiveDepthPostAdditionRetentionError(
                "fresh resolver changed enlarged Candidate bytes"
            )
        if _trial_snapshot(fresh_root, enlarged_ids) != enlarged_trials:
            raise AdaptiveDepthPostAdditionRetentionError(
                "fresh resolver changed enlarged Candidate trials"
            )

        replay = synthesize_shallowest_verified_route(
            fresh_root,
            input_domain=input_domain,
            output_domain=output_domain,
            examples=planning_examples,
            max_depth=int(initial["adaptive_max_depth"]),
            max_candidates=64,
            max_search_nodes=int(initial["adaptive_search_node_budget"]),
            max_behavior_evaluations=int(initial["adaptive_behavior_evaluation_budget"]),
        )
        replay_ids = tuple(str(value) for value in replay["selected_candidate_ids"])
        if replay.get("candidate_ids_supplied_by_caller") is not False:
            raise AdaptiveDepthPostAdditionRetentionError("replay relied on caller Candidate IDs")
        if replay_ids != hidden_ids:
            raise AdaptiveDepthPostAdditionRetentionError(
                "later capability changed the retained adaptive route"
            )
        if int(replay["selected_chain_depth"]) != int(initial["selected_chain_depth"]):
            raise AdaptiveDepthPostAdditionRetentionError(
                "later capability changed the retained minimal behavior depth"
            )
        if later_id in replay_ids:
            raise AdaptiveDepthPostAdditionRetentionError(
                "unrelated later capability hijacked retained adaptive behavior"
            )

        challenge_inputs = _challenge_inputs(input_domain, str(initial["challenge_commitment"]))
        expected_outputs = tuple(
            _execute_descriptor_chain(hidden_items, value) for value in challenge_inputs
        )
        replay_items = _load_verified_macro_items(fresh_root, replay_ids)
        fresh_run_ids: list[str] = []
        challenge_outputs: list[dict[str, Any]] = []
        for value, expected in zip(challenge_inputs, expected_outputs, strict=True):
            compiled_output = execute_program(replay["compiled_program"], value)
            runtime_output, run_id, refs = _execute_chain(fresh_root, replay_items, value)
            if compiled_output != expected or runtime_output != expected:
                raise AdaptiveDepthPostAdditionRetentionError(
                    "retained adaptive route failed post-addition challenge"
                )
            if len(refs) != int(replay["selected_chain_depth"]):
                raise AdaptiveDepthPostAdditionRetentionError(
                    "post-addition challenge executed the wrong learned-stage count"
                )
            fresh_run_ids.append(run_id)
            challenge_outputs.append(
                {"input": value, "expected": expected, "output": runtime_output}
            )
        if len(set(fresh_run_ids)) != len(fresh_run_ids):
            raise AdaptiveDepthPostAdditionRetentionError(
                "post-addition challenges reused a fresh Engine run"
            )

        unsupported_failed_closed = False
        try:
            synthesize_shallowest_verified_route(
                fresh_root,
                input_domain="string",
                output_domain="numeric",
                examples=(
                    ProgramExample("unseen-a", 1),
                    ProgramExample("unseen-b", 2),
                ),
                max_depth=3,
                max_candidates=64,
                max_search_nodes=64,
                max_behavior_evaluations=32,
            )
        except AdaptiveDepthCompositionError as exc:
            unsupported_failed_closed = "no unique behavior-matching verified route" in str(exc)
        if not unsupported_failed_closed:
            raise AdaptiveDepthPostAdditionRetentionError(
                "unrelated unsupported string-to-numeric gap did not fail closed"
            )

        if _tree_snapshot(fresh_root, enlarged_ids) != enlarged_tree:
            raise AdaptiveDepthPostAdditionRetentionError(
                "post-addition replay changed Candidate bytes"
            )
        if _trial_snapshot(fresh_root, enlarged_ids) != enlarged_trials:
            raise AdaptiveDepthPostAdditionRetentionError(
                "post-addition replay changed Candidate trials"
            )

    if _tree_snapshot(root, enlarged_ids) != enlarged_tree:
        raise AdaptiveDepthPostAdditionRetentionError(
            "fresh post-addition validation mutated source Candidate bytes"
        )
    if _trial_snapshot(root, enlarged_ids) != enlarged_trials:
        raise AdaptiveDepthPostAdditionRetentionError(
            "fresh post-addition validation mutated source Candidate trials"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "adaptive-depth-postaddition-retention-v1",
        "initial_adaptive_digest": str(initial["digest"]),
        "initial_selected_candidate_ids": list(hidden_ids),
        "initial_selected_chain_depth": int(initial["selected_chain_depth"]),
        "initial_shortest_type_chain_depth": int(initial["shortest_type_chain_depth"]),
        "later_candidate_id": later_id,
        "later_candidate_input_domain": str(later_spec["input_domain"]),
        "later_candidate_output_domain": str(later_spec["output_domain"]),
        "later_candidate_negative_evidence_retained": True,
        "prior_candidate_state_unchanged_after_addition": True,
        "prior_trial_state_unchanged_after_addition": True,
        "caller_supplied_candidate_ids": False,
        "replay_selected_candidate_ids": list(replay_ids),
        "replay_selected_chain_depth": int(replay["selected_chain_depth"]),
        "replay_shortest_type_chain_depth": int(replay["shortest_type_chain_depth"]),
        "replay_depths_attempted": replay["depths_attempted"],
        "replay_search_nodes_visited": int(replay["search_nodes_visited"]),
        "replay_search_node_budget": int(replay["search_node_budget"]),
        "replay_behavior_evaluations": int(replay["behavior_evaluations"]),
        "replay_behavior_evaluation_budget": int(replay["behavior_evaluation_budget"]),
        "later_candidate_not_selected": later_id not in replay_ids,
        "retained_route_identity_unchanged": replay_ids == hidden_ids,
        "retained_minimal_depth_unchanged": (
            int(replay["selected_chain_depth"]) == int(initial["selected_chain_depth"])
        ),
        "unsupported_gap_failed_closed": unsupported_failed_closed,
        "challenge_case_count": len(challenge_outputs),
        "challenge_outputs": challenge_outputs,
        "fresh_engine_runs": fresh_run_ids,
        "fresh_engine_runs_unique": len(set(fresh_run_ids)) == len(fresh_run_ids),
        "fresh_candidate_state_unchanged": True,
        "fresh_trial_state_unchanged": True,
        "source_candidate_state_unchanged": True,
        "source_trial_state_unchanged": True,
        "prior_runs_copied": prior_runs_copied,
        "prior_episodes_copied": prior_episodes_copied,
        "prior_evidence_copied": prior_evidence_copied,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded post-addition retention evidence only. An adaptively discovered composed "
            "route remained the same minimal behavior solution after a later independently verified "
            "capability was added and after a fresh state-only restart; unsupported behavior remained "
            "fail-closed and Candidate bytes/trials remained immutable. Repository-authored generation, "
            "regression, search, runtime, and scoring do not establish independent production evidence "
            "or AGI."
        ),
    }
    if not all(
        (
            report["retained_route_identity_unchanged"],
            report["retained_minimal_depth_unchanged"],
            report["later_candidate_not_selected"],
            report["unsupported_gap_failed_closed"],
            report["fresh_engine_runs_unique"],
            report["prior_candidate_state_unchanged_after_addition"],
            report["prior_trial_state_unchanged_after_addition"],
            report["replay_search_nodes_visited"] <= report["replay_search_node_budget"],
            report["replay_behavior_evaluations"] <= report["replay_behavior_evaluation_budget"],
            not report["prior_runs_copied"],
            not report["prior_episodes_copied"],
            not report["prior_evidence_copied"],
        )
    ):
        raise AdaptiveDepthPostAdditionRetentionError(
            "adaptive post-addition retention aggregate invariant failed"
        )
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "adaptive-depth-postaddition-retention"
        / f"retention-{_digest(seed)[:16]}.json",
        report,
    )
    return report
