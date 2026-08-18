from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.durable_state_rehydration import _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.seeded_generated_goal_acquisition import _generated_goal_specs
from agi.verified_macro_library_discovery import synthesize_from_verified_macro_library
from agi.verified_macro_synthesis import _load_verified_macro_items


class GeneratedParameterCompositionError(RuntimeError):
    pass


def _generated_spec(seed: str, prefix: str) -> dict[str, Any]:
    try:
        return next(spec for spec in _generated_goal_specs(seed) if str(spec["name"]).startswith(prefix))
    except StopIteration as exc:  # pragma: no cover - fixed generator contract
        raise GeneratedParameterCompositionError(f"missing generated spec: {prefix}") from exc


def _distinct_scale_spec(seed: str, scale: int) -> tuple[str, dict[str, Any]]:
    for index in range(16):
        candidate_seed = f"{seed}:distractor:{index}"
        spec = _generated_spec(candidate_seed, "generated-scale-")
        if int(spec["generated_parameter"]) != scale:
            return candidate_seed, spec
    raise GeneratedParameterCompositionError("bounded seed search did not find a distinct scale distractor")


def run_generated_parameter_composition_transfer(root: Path, seed: str) -> dict[str, Any]:
    """Compose independently generated learned parameters after a fresh state-only restart.

    Three exact-scope learned numeric programs are independently regression-promoted from seed-derived
    behavior instances: a scale, an offset, and a behaviorally wrong scale distractor. A distinct fresh
    state-only root receives only a new affine behavior specification, not Candidate identities. Durable
    library discovery must find the verified programs itself, reject all one-stage type-correct routes,
    uniquely select scale then offset, and transfer that two-stage plan to post-selection challenges.
    Candidate bytes and trial ledgers must remain unchanged in both the fresh resolver and durable source.

    This is bounded repository-authored internal transfer/composition evidence. It does not establish
    open-domain task generation, evaluator independence, production isolation, or AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    scale_seed = f"{seed}:scale-source"
    offset_seed = f"{seed}:offset-source"
    scale_spec = _generated_spec(scale_seed, "generated-scale-")
    offset_spec = _generated_spec(offset_seed, "generated-offset-")
    scale = int(scale_spec["generated_parameter"])
    offset = int(offset_spec["generated_parameter"])
    distractor_seed, distractor_spec = _distinct_scale_spec(seed, scale)
    distractor_scale = int(distractor_spec["generated_parameter"])

    scale_acquired = _promote_task(root, seed=scale_seed, spec=scale_spec)
    offset_acquired = _promote_task(root, seed=offset_seed, spec=offset_spec)
    distractor_acquired = _promote_task(root, seed=distractor_seed, spec=distractor_spec)
    scale_id = str(scale_acquired["candidate_id"])
    offset_id = str(offset_acquired["candidate_id"])
    distractor_id = str(distractor_acquired["candidate_id"])
    generated_ids = [scale_id, offset_id, distractor_id]
    if len(set(generated_ids)) != 3:
        raise GeneratedParameterCompositionError("generated component Candidate identities are not distinct")
    if not all(
        bool(item.get("negative_evidence_retained"))
        for item in (scale_acquired, offset_acquired, distractor_acquired)
    ):
        raise GeneratedParameterCompositionError("generated component lost protected negative evidence")

    all_ids = _all_candidate_ids(root)
    source_tree = _tree_snapshot(root, all_ids)
    source_trials = _trial_snapshot(root, all_ids)
    if not all(source_trials.get(candidate_id) for candidate_id in generated_ids):
        raise GeneratedParameterCompositionError("generated component lacks durable regression trials")

    planning_inputs = (4, -5, 8)
    component_support = {1, -2, 3, 7, -9, 0, 2, -3, 11, -8}
    if set(planning_inputs) & component_support:
        raise GeneratedParameterCompositionError("composition planning overlaps component support")
    planning_examples = tuple(
        ProgramExample(value, value * scale + offset) for value in planning_inputs
    )

    with tempfile.TemporaryDirectory(prefix="agi-generated-parameter-composition-") as temporary:
        fresh_root = Path(temporary).resolve()
        _copy_persistent_state(root, fresh_root)
        prior_runs_copied = (fresh_root / ".continual" / "runs").exists()
        prior_episodes_copied = (fresh_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (fresh_root / ".continual" / "evidence").exists()
        fresh_tree_before = _tree_snapshot(fresh_root, all_ids)
        fresh_trials_before = _trial_snapshot(fresh_root, all_ids)
        if fresh_tree_before != source_tree or fresh_trials_before != source_trials:
            raise GeneratedParameterCompositionError("fresh restart changed durable learning state")

        synthesized = synthesize_from_verified_macro_library(
            fresh_root,
            input_domain="numeric",
            output_domain="numeric",
            examples=planning_examples,
            max_depth=2,
            max_candidates=64,
        )
        selected_ids = [str(value) for value in synthesized["selected_candidate_ids"]]
        if synthesized["candidate_ids_supplied_by_caller"] is not False:
            raise GeneratedParameterCompositionError("composition relied on caller Candidate IDs")
        if not set(generated_ids).issubset(set(synthesized["library_candidate_ids"])):
            raise GeneratedParameterCompositionError("fresh library omitted a generated component")
        if int(synthesized["shortest_type_chain_depth"]) != 1:
            raise GeneratedParameterCompositionError("composition lacks a one-stage type-correct route")
        if int(synthesized["selected_chain_depth"]) != 2:
            raise GeneratedParameterCompositionError("generated affine behavior did not require two stages")
        if selected_ids != [scale_id, offset_id]:
            raise GeneratedParameterCompositionError("resolver did not select generated scale then offset")

        commitment = _digest(
            {
                "seed": seed,
                "synthesis_digest": synthesized["digest"],
                "selected_ids": selected_ids,
                "scale": scale,
                "offset": offset,
                "phase": "generated-parameter-post-selection-challenge-v1",
            }
        )
        magnitude = 20 + int(commitment[:4], 16) % 80
        challenge_inputs = (magnitude, -(magnitude + 1))
        if set(challenge_inputs) & (set(planning_inputs) | component_support):
            raise GeneratedParameterCompositionError("post-selection challenge overlaps prior support")

        selected_items = _load_verified_macro_items(fresh_root, selected_ids)
        fresh_run_ids: list[str] = []
        outputs: list[dict[str, Any]] = []
        for value in challenge_inputs:
            expected = value * scale + offset
            compiled_output = execute_program(synthesized["compiled_program"], value)
            source_output, run_id, refs = _execute_chain(fresh_root, selected_items, value)
            if compiled_output != expected or source_output != expected:
                raise GeneratedParameterCompositionError("generated composition failed challenge")
            if len(refs) != 2:
                raise GeneratedParameterCompositionError("generated challenge did not execute two stages")
            fresh_run_ids.append(run_id)
            outputs.append({"input": value, "expected": expected, "output": source_output})

        if len(set(fresh_run_ids)) != len(fresh_run_ids):
            raise GeneratedParameterCompositionError("post-selection challenges reused an Engine run")
        if _tree_snapshot(fresh_root, all_ids) != fresh_tree_before:
            raise GeneratedParameterCompositionError("fresh composition changed Candidate bytes")
        if _trial_snapshot(fresh_root, all_ids) != fresh_trials_before:
            raise GeneratedParameterCompositionError("fresh composition changed Candidate trials")

    if _tree_snapshot(root, all_ids) != source_tree:
        raise GeneratedParameterCompositionError("fresh transfer mutated source Candidate bytes")
    if _trial_snapshot(root, all_ids) != source_trials:
        raise GeneratedParameterCompositionError("fresh transfer mutated source Candidate trials")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "generated-parameter-composition-transfer-v1",
        "scale": scale,
        "offset": offset,
        "distractor_scale": distractor_scale,
        "scale_candidate_id": scale_id,
        "offset_candidate_id": offset_id,
        "distractor_candidate_id": distractor_id,
        "caller_supplied_candidate_ids": False,
        "library_candidate_count": int(synthesized["library_candidate_count"]),
        "shortest_type_chain_depth": int(synthesized["shortest_type_chain_depth"]),
        "selected_chain_depth": int(synthesized["selected_chain_depth"]),
        "selected_candidate_ids": selected_ids,
        "one_stage_type_correct_routes_rejected_by_behavior": True,
        "challenge_generated_after_selection": True,
        "post_selection_challenge_commitment": commitment,
        "challenge_case_count": len(challenge_inputs),
        "fresh_engine_runs": fresh_run_ids,
        "fresh_engine_runs_unique": len(set(fresh_run_ids)) == len(fresh_run_ids),
        "all_outputs_matched": all(item["output"] == item["expected"] for item in outputs),
        "fresh_candidate_state_unchanged": True,
        "fresh_trial_state_unchanged": True,
        "source_candidate_state_unchanged": True,
        "source_trial_state_unchanged": True,
        "prior_runs_copied": prior_runs_copied,
        "prior_episodes_copied": prior_episodes_copied,
        "prior_evidence_copied": prior_evidence_copied,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded generated-parameter transfer evidence only. Independently regression-promoted "
            "seed-derived scale and offset skills were rediscovered after a fresh state-only restart, "
            "behaviorally composed without caller Candidate IDs, and transferred to post-selection affine "
            "challenges while a one-stage generated distractor was rejected. Repository-authored generation, "
            "synthesis, regression, runtime, and scoring do not establish independent production AGI evidence."
        ),
    }
    if not all(
        (
            report["fresh_engine_runs_unique"],
            report["all_outputs_matched"],
            report["one_stage_type_correct_routes_rejected_by_behavior"],
            not report["prior_runs_copied"],
            not report["prior_episodes_copied"],
            not report["prior_evidence_copied"],
        )
    ):
        raise GeneratedParameterCompositionError("generated parameter composition aggregate invariant failed")
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "generated-parameter-composition-transfer"
        / f"composition-{_digest(seed)[:16]}.json",
        report,
    )
    return report
