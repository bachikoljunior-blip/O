from __future__ import annotations

from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program, validate_program_descriptor
from agi.behavior_guided_tool_chain_discovery import _execute_chain, _typed_chains
from agi.heterogeneous_retention_campaign import _digest, _measurement, _promote_task
from agi.learned_tool_chain_compilation import _compile_chain_program, _select_unique_minimal_chain
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
    _validate_runtime_output,
)
from agi.recursive_skill_chunking import _verified_candidate_item, run_recursive_skill_chunking
from continual.acquired_program_tools import acquired_program_candidate_payload
from continual.candidate_regression import record_candidate_regression
from continual.learned_tools import LearnedToolError


def _object_score_projection_spec() -> dict[str, Any]:
    return {
        "name": "recursive-object-score",
        "input_domain": "object",
        "output_domain": "numeric",
        "examples": (
            ProgramExample({"score": -3, "noise": "a"}, -3),
            ProgramExample({"score": 2, "noise": "b"}, 2),
            ProgramExample({"score": 7, "noise": "c"}, 7),
        ),
        "probe": {"score": 11, "noise": "novel", "extra": True},
        "expected": 11,
        "max_nodes": 3,
    }


def _object_threshold_distractor_spec() -> dict[str, Any]:
    return {
        "name": "recursive-object-threshold",
        "input_domain": "object",
        "output_domain": "boolean",
        "examples": (
            ProgramExample({"score": -2, "noise": 0}, False),
            ProgramExample({"score": 4, "noise": 0}, False),
            ProgramExample({"score": 5, "noise": 1}, True),
            ProgramExample({"score": 12, "noise": 1}, True),
        ),
        "probe": {"score": 11, "noise": 999, "novel": True},
        "expected": True,
        "max_nodes": 6,
    }


def run_cross_domain_recursive_macro_transfer(root: Path, seed: str) -> dict[str, Any]:
    """Transfer a recursively compiled macro into a new structured input domain.

    The second-generation numeric-to-boolean macro from recursive skill chunking is independently
    materialized and regression verified first. A new object-to-numeric projection and a shorter
    object-to-boolean threshold distractor are separately learned and promoted. For a novel structured
    inverted-absolute-threshold task, type search therefore has a one-stage route that is behaviorally
    wrong and a two-stage projection-plus-recursive-macro route that is behaviorally correct. The
    selected route is compiled into a new object-to-boolean Candidate, independently regression gated,
    and replayed against its source chain on post-promotion structured challenges across fresh Engines.

    This is bounded internal cross-domain transfer evidence, not independent production evidence or AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    generation_two = run_recursive_skill_chunking(root, f"{seed}:generation-two")
    if not generation_two.get("passed") or not generation_two.get(
        "second_generation_candidate_promoted"
    ):
        raise RuntimeError("second-generation recursive macro was not verified")
    macro = _verified_candidate_item(root, str(generation_two["compiled_candidate_id"]))
    if macro["input_domain"] != "numeric" or macro["output_domain"] != "boolean":
        raise RuntimeError("recursive macro has unexpected type")

    projection = _promote_task(
        root,
        seed=f"{seed}:cross-domain-components",
        spec=_object_score_projection_spec(),
    )
    distractor = _promote_task(
        root,
        seed=f"{seed}:cross-domain-components",
        spec=_object_threshold_distractor_spec(),
    )
    component_pool = (projection, macro, distractor)
    component_ids = [str(item["candidate_id"]) for item in component_pool]
    if len(set(component_ids)) != len(component_ids):
        raise RuntimeError("cross-domain planner reused a Candidate identity")

    component_trials_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in component_ids
    }
    if not all(component_trials_before.values()):
        raise RuntimeError("cross-domain source component lacks regression trial evidence")

    planning_examples = (
        ProgramExample({"score": -11, "tag": "plan-a"}, False),
        ProgramExample({"score": 1, "tag": "plan-b"}, True),
        ProgramExample({"score": 14, "tag": "plan-c"}, False),
    )
    planning_score_values = {-11, 1, 14}
    earlier_component_scores = {-3, 2, 7, -2, 4, 5, 12, 11}
    if planning_score_values & earlier_component_scores:
        raise RuntimeError("cross-domain planning support overlaps component synthesis/probe support")

    chains = _typed_chains(
        component_pool,
        input_domain="object",
        output_domain="boolean",
        max_depth=2,
    )
    if not chains:
        raise RuntimeError("cross-domain planner found no typed route")
    shortest_type_depth = min(len(chain) for chain in chains)
    selected = _select_unique_minimal_chain(root, chains, planning_examples)
    selected_ids = [str(item["candidate_id"]) for item in selected]
    if shortest_type_depth != 1:
        raise RuntimeError("cross-domain task lacks a shorter type-correct distractor")
    expected_selected_ids = [str(projection["candidate_id"]), str(macro["candidate_id"])]
    if len(selected) != 2 or selected_ids != expected_selected_ids:
        raise RuntimeError("cross-domain behavior did not select projection plus recursive macro")

    compiled_program = _compile_chain_program(selected, planning_examples)
    compiled_meta = validate_program_descriptor(compiled_program)
    macro_meta = validate_program_descriptor(macro["program"])
    if compiled_meta["input_domain"] != "object" or compiled_meta["output_domain"] != "boolean":
        raise RuntimeError("cross-domain compiler produced an unexpected type")
    if int(compiled_meta["nodes"]) <= int(macro_meta["nodes"]):
        raise RuntimeError("cross-domain compiled program did not incorporate structured projection")
    for example in planning_examples:
        source_output, _run_id, _refs = _execute_chain(root, selected, example.input)
        compiled_output = execute_program(compiled_program, example.input)
        if source_output != example.output or compiled_output != source_output:
            raise RuntimeError("cross-domain compiler changed planning semantics")

    token = _digest(
        {
            "seed": seed,
            "recursive_macro_candidate": macro["candidate_id"],
            "selected_ids": selected_ids,
            "compiled_support": compiled_program["support_sha256"],
        }
    )[:16]
    candidate_id = f"cross-domain-recursive-plan-{token}"
    tool_id = f"cross-domain-recursive-tool-{token}"
    scope = "agi/acquired-program/compiled-plan/cross-domain-recursive-object-threshold/v1"
    candidate_dir = root / ".continual" / "candidates" / candidate_id
    _atomic_json(
        candidate_dir / "candidate.json",
        acquired_program_candidate_payload(
            candidate_id=candidate_id,
            tool_id=tool_id,
            scope=scope,
            program=compiled_program,
            description=(
                "Structured object-to-boolean acquired program compiled from an independently promoted "
                "object projection plus a recursively compiled verified macro."
            ),
        ),
    )

    baseline_engine = _mechanical_engine(root)
    baseline_run = _new_runtime_run(baseline_engine)
    baseline_failed_closed = False
    try:
        _invoke(
            baseline_engine,
            baseline_run,
            scope=scope,
            tool_id=tool_id,
            value={"score": -11, "tag": "baseline"},
        )
    except LearnedToolError:
        baseline_failed_closed = True
    if not baseline_failed_closed:
        raise RuntimeError("cross-domain compiled Candidate became callable before regression")

    target_task = f"withheld-{candidate_id}"
    target_domain = "cross-domain-recursive:object-to-boolean"
    baseline: list[dict[str, Any]] = []
    trial: list[dict[str, Any]] = []
    for repeat_index in range(3):
        artifact = {
            "candidate_id": candidate_id,
            "repeat_index": repeat_index,
            "recursive_macro_candidate_id": macro["candidate_id"],
            "compiled_program_sha256": _digest(compiled_program),
            "selected_chain_sha256": _digest(selected_ids),
        }
        baseline.append(
            _measurement(target_task, repeat_index, 0.0, artifact, domain=target_domain)
        )
        trial.append(
            _measurement(target_task, repeat_index, 1.0, artifact, domain=target_domain)
        )

    protected_task = f"protected-before-{candidate_id}"
    protected = _measurement(
        protected_task,
        0,
        1.0,
        {
            "candidate_id": candidate_id,
            "recursive_macro_candidate_id": macro["candidate_id"],
            "invariant": "recursive macro, projection, and distractor remain independently verified",
        },
        domain="protected",
    )
    reg_dir = candidate_dir / "regression-input"
    baseline_path = reg_dir / "baseline.json"
    adverse_path = reg_dir / "candidate-adverse.json"
    valid_path = reg_dir / "candidate-valid.json"
    _atomic_json(baseline_path, [*baseline, protected])
    _atomic_json(
        adverse_path,
        [
            *trial,
            {
                **protected,
                "passed": False,
                "score": 0.0,
                "artifact_sha256": _digest(
                    {"candidate_id": candidate_id, "forced_protected_drop": True}
                ),
            },
        ],
    )
    _atomic_json(valid_path, [*trial, protected])

    rejected = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline_path,
        candidate_path=adverse_path,
        target_task_ids=[target_task],
    )
    if rejected["decision"]["adopt_candidate"]:
        raise RuntimeError("cross-domain Candidate ignored a protected regression")
    promoted = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline_path,
        candidate_path=valid_path,
        target_task_ids=[target_task],
    )
    if not promoted["decision"]["adopt_candidate"]:
        raise RuntimeError("cross-domain Candidate failed valid repeated target improvement")

    compiled_trials_before = _candidate_trial_snapshot(root, candidate_id)
    if not compiled_trials_before:
        raise RuntimeError("cross-domain Candidate lacks durable regression evidence")

    commitment = _digest(
        {
            "seed": seed,
            "candidate_id": candidate_id,
            "recursive_macro_candidate_id": macro["candidate_id"],
            "phase": "post-promotion-cross-domain-recursive-challenge-v1",
        }
    )
    large_negative = -(25 + int(commitment[:2], 16) % 50)
    large_positive = 25 + int(commitment[2:4], 16) % 50
    challenge_inputs = (
        {"score": large_negative, "tag": "challenge-a", "novel": True},
        {"score": 3.5, "tag": "challenge-b", "novel": True},
        {"score": large_positive, "tag": "challenge-c", "novel": True},
    )
    challenge_scores = {large_negative, 3.5, large_positive}
    if challenge_scores & (planning_score_values | earlier_component_scores):
        raise RuntimeError("cross-domain challenge overlaps planning or component support")

    source_run_ids: list[str] = []
    compiled_run_ids: list[str] = []
    outputs: list[dict[str, Any]] = []
    for repeat_index in range(3):
        for case_index, value in enumerate(challenge_inputs):
            score = value["score"]
            expected = abs(score) < 5
            source_output, source_run_id, refs = _execute_chain(root, selected, value)
            compiled_engine = _mechanical_engine(root)
            compiled_run_id = _new_runtime_run(compiled_engine)
            response = _invoke(
                compiled_engine,
                compiled_run_id,
                scope=scope,
                tool_id=tool_id,
                value=value,
            )
            result = _validate_runtime_output(
                response,
                expected=expected,
                candidate_id=candidate_id,
            )
            compiled_output = result["output"]
            if source_output != expected or compiled_output != source_output:
                raise RuntimeError("cross-domain compiled output diverged from recursive source plan")
            source_run_ids.append(source_run_id)
            compiled_run_ids.append(compiled_run_id)
            outputs.append(
                {
                    "repeat_index": repeat_index,
                    "case_index": case_index,
                    "input_sha256": _digest(value),
                    "expected": expected,
                    "source_output": source_output,
                    "compiled_output": compiled_output,
                    "source_stage_count": len(refs),
                }
            )

    component_trials_after = {
        component_id: _candidate_trial_snapshot(root, component_id)
        for component_id in component_ids
    }
    compiled_trials_after = _candidate_trial_snapshot(root, candidate_id)
    component_trials_unchanged = component_trials_after == component_trials_before
    compiled_trials_unchanged = compiled_trials_after == compiled_trials_before
    if not component_trials_unchanged or not compiled_trials_unchanged:
        raise RuntimeError("cross-domain replay changed Candidate trial state")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "cross-domain-recursive-macro-transfer",
        "recursive_macro_candidate_id": macro["candidate_id"],
        "recursive_macro_scope": macro["scope"],
        "recursive_macro_program_nodes": int(macro_meta["nodes"]),
        "component_candidate_ids": component_ids,
        "shortest_type_chain_depth": shortest_type_depth,
        "selected_chain_depth": len(selected),
        "selected_chain_candidate_ids": selected_ids,
        "shorter_behavioral_distractor_candidate_id": distractor["candidate_id"],
        "recursive_macro_reused_cross_domain": selected_ids[-1] == macro["candidate_id"],
        "compiled_candidate_id": candidate_id,
        "compiled_tool_id": tool_id,
        "compiled_scope": scope,
        "compiled_input_domain": compiled_meta["input_domain"],
        "compiled_output_domain": compiled_meta["output_domain"],
        "compiled_program_nodes": int(compiled_meta["nodes"]),
        "compiled_program_depth": int(compiled_meta["depth"]),
        "planning_examples_overlap_component_support": False,
        "baseline_before_compiled_regression_failed_closed": baseline_failed_closed,
        "adverse_compiled_regression_rejected": not rejected["decision"]["adopt_candidate"],
        "compiled_candidate_promoted": bool(promoted["decision"]["adopt_candidate"]),
        "challenge_generated_after_compiled_promotion": True,
        "challenge_commitment": commitment,
        "challenge_inputs_overlap_planning_or_support": False,
        "challenge_case_count": len(challenge_inputs),
        "source_fresh_run_count": len(source_run_ids),
        "compiled_fresh_run_count": len(compiled_run_ids),
        "all_outputs_equal": all(
            item["expected"] == item["source_output"] == item["compiled_output"]
            for item in outputs
        ),
        "source_component_trial_state_unchanged": component_trials_unchanged,
        "compiled_candidate_trial_state_unchanged": compiled_trials_unchanged,
        "all_source_negative_evidence_retained": all(
            bool(item.get("negative_evidence_retained")) for item in component_pool
        ),
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded cross-domain recursive-macro transfer evidence only. A verified recursively "
            "compiled numeric-to-boolean macro was reused after a separately learned object projection, "
            "selected by behavior over a shorter type-correct wrong object predicate, compiled into a "
            "new object-to-boolean Candidate, independently regression-gated, and replayed across fresh "
            "mechanical Engines with unchanged trial ledgers. Tasks, planner, compiler, measurements, "
            "challenge generation, and runtime remain repository-authored, so this is not AGI or "
            "independent production evidence."
        ),
    }
    if not report["all_outputs_equal"]:
        raise RuntimeError("cross-domain aggregate equivalence check failed")
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "cross-domain-recursive-macro-transfer"
        / f"cross-domain-recursive-{_digest(seed)[:16]}.json",
        report,
    )
    return report
