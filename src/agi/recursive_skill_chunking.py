from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program, validate_program_descriptor
from agi.behavior_guided_tool_chain_discovery import _execute_chain, _typed_chains
from agi.heterogeneous_retention_campaign import _digest, _measurement, _promote_task
from agi.learned_tool_chain_compilation import (
    _compile_chain_program,
    _select_unique_minimal_chain,
    run_learned_tool_chain_compilation,
)
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
    _validate_runtime_output,
)
from continual.acquired_program_tools import (
    AcquiredProgramToolSpec,
    acquired_program_candidate_payload,
)
from continual.candidate_regression import record_candidate_regression
from continual.learned_tools import LearnedToolError


def _verified_candidate_item(root: Path, candidate_id: str) -> dict[str, Any]:
    path = root / ".continual" / "candidates" / candidate_id / "candidate.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("recursive skill Candidate must persist as an object")
    spec = AcquiredProgramToolSpec.from_candidate(raw)
    return {
        "candidate_id": spec.candidate_id,
        "tool_id": spec.tool_id,
        "scope": spec.scope,
        "input_domain": str(spec.program["input_domain"]),
        "output_domain": str(spec.program["output_domain"]),
        "program": spec.program,
        "negative_evidence_retained": bool(raw.get("contradictory_evidence")),
    }


def _recursive_not_spec() -> dict[str, Any]:
    return {
        "name": "recursive-not",
        "input_domain": "boolean",
        "output_domain": "boolean",
        "examples": (
            ProgramExample(True, False),
            ProgramExample(False, True),
            ProgramExample(True, False),
        ),
        "probe": False,
        "expected": True,
        "max_nodes": 2,
    }


def run_recursive_skill_chunking(root: Path, seed: str) -> dict[str, Any]:
    """Reuse a verified compiled skill as a macro and chunk the new plan again.

    A first-generation compiled numeric-to-boolean skill is independently regression promoted by the
    existing chain-compilation campaign. A separately learned boolean negation skill is then promoted.
    The new planner receives only post-promotion behavior examples for an inverted absolute-threshold
    task. Type search contains a shorter one-stage macro route, but that route is behaviorally wrong;
    behavior must select macro-plus-negation. That mixed plan is compiled into a second-generation
    acquired-program Candidate, rejected under a forced protected regression, validly promoted, and
    compared with the two-stage source plan on challenges generated after promotion across fresh
    Engines. Candidate trial ledgers must remain unchanged during replay.

    This is bounded internal recursive skill-chunking evidence. It does not establish open-world skill
    induction, independent evaluation, or AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    generation_one = run_learned_tool_chain_compilation(
        root,
        f"{seed}:generation-one",
    )
    if not generation_one.get("passed") or not generation_one.get("compiled_candidate_promoted"):
        raise RuntimeError("first-generation compiled skill was not independently verified")
    macro = _verified_candidate_item(root, str(generation_one["compiled_candidate_id"]))
    if macro["input_domain"] != "numeric" or macro["output_domain"] != "boolean":
        raise RuntimeError("first-generation macro has unexpected type")

    negation = _promote_task(
        root,
        seed=f"{seed}:recursive-components",
        spec=_recursive_not_spec(),
    )
    if negation["input_domain"] != "boolean" or negation["output_domain"] != "boolean":
        raise RuntimeError("recursive component has unexpected type")

    component_pool = (macro, negation)
    component_ids = [str(item["candidate_id"]) for item in component_pool]
    component_trials_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in component_ids
    }
    if not all(component_trials_before.values()):
        raise RuntimeError("recursive source component lacks regression trial evidence")

    known_generation_one_support = {-4, -3, 0, 2, 5, 7, -8, -1, 9}
    planning_examples = (
        ProgramExample(-13, False),
        ProgramExample(-2, True),
        ProgramExample(16, False),
    )
    planning_inputs = {example.input for example in planning_examples}
    if planning_inputs & known_generation_one_support:
        raise RuntimeError("recursive planning support overlaps first-generation support")

    chains = _typed_chains(
        component_pool,
        input_domain="numeric",
        output_domain="boolean",
        max_depth=2,
    )
    if not chains:
        raise RuntimeError("recursive skill planner found no typed route")
    shortest_type_depth = min(len(chain) for chain in chains)
    selected = _select_unique_minimal_chain(root, chains, planning_examples)
    selected_ids = [str(item["candidate_id"]) for item in selected]
    if shortest_type_depth != 1:
        raise RuntimeError("recursive task lacks a shorter type-correct macro distractor")
    if len(selected) != 2 or selected_ids != component_ids:
        raise RuntimeError("recursive behavior did not select macro-plus-negation")

    second_program = _compile_chain_program(selected, planning_examples)
    second_meta = validate_program_descriptor(second_program)
    first_meta = validate_program_descriptor(macro["program"])
    if int(second_meta["nodes"]) <= int(first_meta["nodes"]):
        raise RuntimeError("second-generation chunk did not structurally reuse the macro behavior")
    for example in planning_examples:
        source_output, _run_id, _refs = _execute_chain(root, selected, example.input)
        compiled_output = execute_program(second_program, example.input)
        if source_output != example.output or compiled_output != source_output:
            raise RuntimeError("second-generation compiler changed planning semantics")

    token = _digest(
        {
            "seed": seed,
            "generation_one_candidate": macro["candidate_id"],
            "selected_ids": selected_ids,
            "second_support": second_program["support_sha256"],
        }
    )[:16]
    candidate_id = f"recursive-compiled-plan-{token}"
    tool_id = f"recursive-compiled-tool-{token}"
    scope = "agi/acquired-program/compiled-plan/recursive-inverted-absolute-threshold/v1"
    candidate_dir = root / ".continual" / "candidates" / candidate_id
    _atomic_json(
        candidate_dir / "candidate.json",
        acquired_program_candidate_payload(
            candidate_id=candidate_id,
            tool_id=tool_id,
            scope=scope,
            program=second_program,
            description=(
                "Second-generation pure acquired program compiled from a verified compiled macro plus "
                "a separately promoted learned component."
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
            value=-13,
        )
    except LearnedToolError:
        baseline_failed_closed = True
    if not baseline_failed_closed:
        raise RuntimeError("second-generation compiled skill became callable before regression")

    target_task = f"withheld-{candidate_id}"
    target_domain = "recursive-compiled-plan:numeric-to-boolean"
    baseline: list[dict[str, Any]] = []
    trial: list[dict[str, Any]] = []
    for repeat_index in range(3):
        artifact = {
            "candidate_id": candidate_id,
            "repeat_index": repeat_index,
            "generation_one_candidate_id": macro["candidate_id"],
            "second_program_sha256": _digest(second_program),
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
            "generation_one_candidate_id": macro["candidate_id"],
            "invariant": "first-generation macro and recursive component remain verified",
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
        raise RuntimeError("second-generation Candidate ignored a protected regression")
    promoted = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline_path,
        candidate_path=valid_path,
        target_task_ids=[target_task],
    )
    if not promoted["decision"]["adopt_candidate"]:
        raise RuntimeError("second-generation Candidate failed valid repeated target improvement")

    second_trials_before = _candidate_trial_snapshot(root, candidate_id)
    if not second_trials_before:
        raise RuntimeError("second-generation Candidate lacks durable regression evidence")

    commitment = _digest(
        {
            "seed": seed,
            "candidate_id": candidate_id,
            "generation_one_candidate_id": macro["candidate_id"],
            "phase": "post-promotion-recursive-skill-challenge-v1",
        }
    )
    large_negative = -(25 + int(commitment[:2], 16) % 50)
    large_positive = 25 + int(commitment[2:4], 16) % 50
    challenge_inputs = (large_negative, 3, large_positive)
    if len(set(challenge_inputs)) != len(challenge_inputs):
        raise RuntimeError("recursive challenge generator produced duplicate inputs")
    if set(challenge_inputs) & (planning_inputs | known_generation_one_support):
        raise RuntimeError("recursive challenge overlaps planning or earlier support")

    source_run_ids: list[str] = []
    compiled_run_ids: list[str] = []
    outputs: list[dict[str, Any]] = []
    for repeat_index in range(3):
        for case_index, value in enumerate(challenge_inputs):
            expected = abs(value) < 5
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
                raise RuntimeError("recursive compiled skill diverged from source macro plan")
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
    second_trials_after = _candidate_trial_snapshot(root, candidate_id)
    component_trials_unchanged = component_trials_after == component_trials_before
    second_trials_unchanged = second_trials_after == second_trials_before
    if not component_trials_unchanged or not second_trials_unchanged:
        raise RuntimeError("recursive replay changed Candidate trial state")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "recursive-verified-skill-chunking",
        "generation_one_candidate_id": macro["candidate_id"],
        "generation_one_tool_id": macro["tool_id"],
        "generation_one_scope": macro["scope"],
        "generation_one_program_nodes": int(first_meta["nodes"]),
        "recursive_component_candidate_id": negation["candidate_id"],
        "component_candidate_ids": component_ids,
        "shortest_type_chain_depth": shortest_type_depth,
        "selected_chain_depth": len(selected),
        "selected_chain_candidate_ids": selected_ids,
        "compiled_candidate_id": candidate_id,
        "compiled_tool_id": tool_id,
        "compiled_scope": scope,
        "second_generation_program_nodes": int(second_meta["nodes"]),
        "second_generation_program_depth": int(second_meta["depth"]),
        "planning_examples_overlap_generation_one_support": False,
        "first_generation_macro_reused_as_stage": selected_ids[0] == macro["candidate_id"],
        "baseline_before_second_regression_failed_closed": baseline_failed_closed,
        "adverse_second_regression_rejected": not rejected["decision"]["adopt_candidate"],
        "second_generation_candidate_promoted": bool(promoted["decision"]["adopt_candidate"]),
        "challenge_generated_after_second_promotion": True,
        "challenge_commitment": commitment,
        "challenge_inputs_overlap_planning_or_earlier_support": False,
        "challenge_case_count": len(challenge_inputs),
        "source_fresh_run_count": len(source_run_ids),
        "compiled_fresh_run_count": len(compiled_run_ids),
        "all_outputs_equal": all(
            item["expected"] == item["source_output"] == item["compiled_output"]
            for item in outputs
        ),
        "source_component_trial_state_unchanged": component_trials_unchanged,
        "second_generation_trial_state_unchanged": second_trials_unchanged,
        "source_negative_evidence_retained": all(
            bool(item.get("negative_evidence_retained")) for item in component_pool
        ),
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded recursive skill-chunking evidence only. A previously regression-promoted "
            "compiled acquired program was reused as a macro in a new behavior-selected task, combined "
            "with a separately promoted boolean skill, compiled into a second-generation Candidate, "
            "independently regression-gated, and replayed across fresh mechanical Engines with unchanged "
            "trial ledgers. The tasks, planner, compiler, measurements, challenges, and runtime remain "
            "repository-authored and credential-free, so this is not AGI or independent production evidence."
        ),
    }
    if not report["all_outputs_equal"]:
        raise RuntimeError("recursive aggregate equivalence check failed")
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "recursive-skill-chunking"
        / f"recursive-skill-{_digest(seed)[:16]}.json",
        report,
    )
    return report
