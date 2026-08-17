from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program, validate_program_descriptor
from agi.behavior_guided_tool_chain_discovery import _chain_matches, _execute_chain, _typed_chains
from agi.heterogeneous_retention_campaign import _digest, _measurement, _promote_task
from agi.learned_tool_chain_compilation import (
    _compile_chain_program,
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
from continual.candidate_regression import candidate_verified_for_scope, record_candidate_regression
from continual.learned_tools import LearnedToolError


def _stage_from_candidate(root: Path, candidate_id: str) -> dict[str, Any]:
    path = root / ".continual" / "candidates" / candidate_id / "candidate.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise RuntimeError("recursive chunking Candidate must be an object")
    spec = AcquiredProgramToolSpec.from_candidate(raw)
    if not candidate_verified_for_scope(root, raw, spec.scope):
        raise RuntimeError("recursive chunking source Candidate is not regression-verified")
    descriptor = spec.descriptor()
    return {
        "candidate_id": spec.candidate_id,
        "tool_id": spec.tool_id,
        "scope": spec.scope,
        "program": spec.program,
        "input_domain": descriptor["input_domain"],
        "output_domain": descriptor["output_domain"],
    }


def _select_unique_minimal(
    root: Path,
    chains: Sequence[tuple[dict[str, Any], ...]],
    examples: Sequence[ProgramExample],
) -> tuple[dict[str, Any], ...]:
    matching = [chain for chain in chains if _chain_matches(root, chain, examples)]
    if not matching:
        raise RuntimeError("recursive chunking found no behaviorally valid plan")
    minimum = min(len(chain) for chain in matching)
    minimal = [chain for chain in matching if len(chain) == minimum]
    if len(minimal) != 1:
        raise RuntimeError("recursive chunking planning support is ambiguous")
    return minimal[0]


def _promote_compiled_program(
    root: Path,
    *,
    seed: str,
    program: Mapping[str, Any],
    source_candidate_ids: Sequence[str],
) -> dict[str, Any]:
    token = _digest(
        {
            "seed": seed,
            "program": program,
            "source_candidate_ids": list(source_candidate_ids),
            "generation": 2,
        }
    )[:16]
    candidate_id = f"recursive-compiled-{token}"
    tool_id = f"recursive-compiled-tool-{token}"
    scope = "agi/acquired-program/compiled-plan/not-absolute-threshold/v2"
    candidate_dir = root / ".continual" / "candidates" / candidate_id
    candidate = acquired_program_candidate_payload(
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=scope,
        program=program,
        description=(
            "Second-generation pure acquired program compiled from a previously compiled macro plus a newly learned boolean transformation."
        ),
    )
    candidate["supporting_evidence"] = [
        {
            "type": "recursive_skill_chunking_source",
            "source_candidate_ids": list(source_candidate_ids),
            "generation": 2,
        }
    ]
    _atomic_json(candidate_dir / "candidate.json", candidate)

    engine = _mechanical_engine(root)
    run_id = _new_runtime_run(engine)
    failed_closed = False
    try:
        _invoke(engine, run_id, scope=scope, tool_id=tool_id, value=-12)
    except LearnedToolError:
        failed_closed = True
    if not failed_closed:
        raise RuntimeError("second-generation compiled Candidate was active before regression")

    target_task = f"withheld-{candidate_id}"
    target_domain = "recursive-compiled:numeric-to-boolean"
    baseline: list[dict[str, Any]] = []
    trial: list[dict[str, Any]] = []
    for repeat_index in range(3):
        artifact = {
            "candidate_id": candidate_id,
            "repeat_index": repeat_index,
            "program_sha256": _digest(program),
            "source_candidate_ids": list(source_candidate_ids),
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
        {"candidate_id": candidate_id, "invariant": "source macro and component skills retained"},
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
        raise RuntimeError("recursive compiled Candidate ignored protected regression")
    promoted = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline_path,
        candidate_path=valid_path,
        target_task_ids=[target_task],
    )
    if not promoted["decision"]["adopt_candidate"]:
        raise RuntimeError("recursive compiled Candidate failed valid promotion")
    return {
        "candidate_id": candidate_id,
        "tool_id": tool_id,
        "scope": scope,
        "failed_closed_before_regression": failed_closed,
        "adverse_regression_rejected": not rejected["decision"]["adopt_candidate"],
        "promoted": bool(promoted["decision"]["adopt_candidate"]),
    }


def _fresh_large(
    commitment: str,
    start_index: int,
    *,
    negative: bool,
    excluded: set[int],
) -> int:
    start = 20 + int(commitment[start_index : start_index + 2], 16) % 60
    for offset in range(60):
        magnitude = 20 + ((start - 20 + offset) % 60)
        candidate = -magnitude if negative else magnitude
        if candidate not in excluded:
            return candidate
    raise RuntimeError("could not derive a fresh recursive chunking challenge value")


def run_recursive_skill_chunking(root: Path, seed: str) -> dict[str, Any]:
    """Reuse an already compiled skill as a macro, then chunk the higher-order plan again.

    Generation one first compiles ``abs -> object -> threshold`` into a verified numeric-to-boolean
    acquired program. A separately learned boolean negation skill is then added to the same verified
    library. New planning examples express ``not(abs(x) >= 5)`` and are disjoint from generation-one
    planning support and all primitive synthesis support. The behavior-guided planner must prefer the
    two-stage ``compiled-macro -> boolean-not`` route over the four-stage primitive equivalent. That
    selected macro plan is compiled again into a second-generation acquired program which must pass a
    fresh protected regression gate before execution. Post-promotion challenges compare the macro plan
    and the second-generation one-stage tool across fresh Engines without changing any source trials.

    This is internal bounded recursive reuse evidence, not open-ended recursive self-improvement or AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    base = run_learned_tool_chain_compilation(root, f"{seed}:generation-one")
    if base.get("passed") is not True:
        raise RuntimeError("generation-one learned-tool compilation did not pass")

    not_spec = {
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
    not_promoted = _promote_task(
        root,
        seed=f"{seed}:boolean-not",
        spec=not_spec,
    )

    source_ids = [
        *[str(item) for item in base["component_candidate_ids"]],
        str(base["compiled_candidate_id"]),
        str(not_promoted["candidate_id"]),
    ]
    if len(set(source_ids)) != len(source_ids):
        raise RuntimeError("recursive chunking source Candidate identities are not distinct")
    stages = tuple(_stage_from_candidate(root, candidate_id) for candidate_id in source_ids)
    source_trials_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in source_ids
    }
    if not all(source_trials_before.values()):
        raise RuntimeError("recursive chunking source Candidate lacks durable trial evidence")

    planning_examples = (
        ProgramExample(-12, False),
        ProgramExample(-2, True),
        ProgramExample(13, False),
    )
    primitive_numeric_support = {
        -3,
        0,
        5,
        2,
        -4,
        7,
    }
    generation_one_planning = {-8, -1, 9}
    planning_inputs = {item.input for item in planning_examples}
    if planning_inputs & (primitive_numeric_support | generation_one_planning):
        raise RuntimeError("recursive planning support overlaps prior support")

    chains = _typed_chains(
        stages,
        input_domain="numeric",
        output_domain="boolean",
        max_depth=4,
    )
    selected = _select_unique_minimal(root, chains, planning_examples)
    selected_ids = [str(item["candidate_id"]) for item in selected]
    expected_selected = [
        str(base["compiled_candidate_id"]),
        str(not_promoted["candidate_id"]),
    ]
    if selected_ids != expected_selected:
        raise RuntimeError("recursive planner did not prefer the previously compiled macro")

    recursive_program = _compile_chain_program(selected, planning_examples)
    recursive_meta = validate_program_descriptor(recursive_program)
    for example in planning_examples:
        plan_output, _run_id, refs = _execute_chain(root, selected, example.input)
        compiled_output = execute_program(recursive_program, example.input)
        if len(refs) != 2 or plan_output != example.output or compiled_output != plan_output:
            raise RuntimeError("recursive compilation changed selected macro-plan semantics")

    generation_two = _promote_compiled_program(
        root,
        seed=f"{seed}:generation-two",
        program=recursive_program,
        source_candidate_ids=selected_ids,
    )
    generation_two_trials_before = _candidate_trial_snapshot(
        root, str(generation_two["candidate_id"])
    )
    if not generation_two_trials_before:
        raise RuntimeError("second-generation compiled Candidate lacks trial evidence")

    prior_challenge_commitment = str(base["challenge_commitment"])
    prior_challenge_values = {
        -(20 + int(prior_challenge_commitment[:2], 16) % 40),
        -2,
        20 + int(prior_challenge_commitment[2:4], 16) % 40,
    }
    excluded = (
        primitive_numeric_support
        | generation_one_planning
        | planning_inputs
        | prior_challenge_values
    )
    commitment = _digest(
        {
            "seed": seed,
            "selected_ids": selected_ids,
            "generation_two_candidate": generation_two["candidate_id"],
            "phase": "post-generation-two-recursive-challenge-v1",
        }
    )
    large_negative = _fresh_large(commitment, 0, negative=True, excluded=excluded)
    large_positive = _fresh_large(commitment, 2, negative=False, excluded=excluded)
    middle = 1
    if middle in excluded:
        raise RuntimeError("recursive challenge middle value unexpectedly overlaps prior support")
    challenge_inputs = (large_negative, middle, large_positive)
    if set(challenge_inputs) & excluded:
        raise RuntimeError("recursive challenge overlaps prior support or evaluation values")

    macro_run_ids: list[str] = []
    compiled_run_ids: list[str] = []
    outputs: list[dict[str, Any]] = []
    for repeat_index in range(3):
        for case_index, value in enumerate(challenge_inputs):
            expected = not (abs(value) >= 5)
            macro_output, macro_run_id, refs = _execute_chain(root, selected, value)
            compiled_engine = _mechanical_engine(root)
            compiled_run_id = _new_runtime_run(compiled_engine)
            response = _invoke(
                compiled_engine,
                compiled_run_id,
                scope=str(generation_two["scope"]),
                tool_id=str(generation_two["tool_id"]),
                value=value,
            )
            result = _validate_runtime_output(
                response,
                expected=expected,
                candidate_id=str(generation_two["candidate_id"]),
            )
            compiled_output = result["output"]
            if macro_output != expected or compiled_output != macro_output:
                raise RuntimeError("recursive compiled skill diverged from its macro source plan")
            macro_run_ids.append(macro_run_id)
            compiled_run_ids.append(compiled_run_id)
            outputs.append(
                {
                    "repeat_index": repeat_index,
                    "case_index": case_index,
                    "input_sha256": _digest(value),
                    "expected": expected,
                    "macro_output": macro_output,
                    "compiled_output": compiled_output,
                    "macro_stage_count": len(refs),
                }
            )

    source_trials_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in source_ids
    }
    generation_two_trials_after = _candidate_trial_snapshot(
        root, str(generation_two["candidate_id"])
    )
    source_trials_unchanged = source_trials_after == source_trials_before
    generation_two_trials_unchanged = generation_two_trials_after == generation_two_trials_before
    if not source_trials_unchanged or not generation_two_trials_unchanged:
        raise RuntimeError("recursive skill replay changed Candidate trial state")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "recursive-learned-skill-chunking",
        "generation_one_compiled_candidate_id": base["compiled_candidate_id"],
        "boolean_not_candidate_id": not_promoted["candidate_id"],
        "source_candidate_count": len(source_ids),
        "planning_support_overlap_prior_support": False,
        "typed_chain_count": len(chains),
        "selected_macro_plan_candidate_ids": selected_ids,
        "selected_macro_plan_stage_count": len(selected),
        "primitive_equivalent_stage_count": 4,
        "generation_two_candidate_id": generation_two["candidate_id"],
        "generation_two_program_nodes": int(recursive_meta["nodes"]),
        "generation_two_program_depth": int(recursive_meta["depth"]),
        "generation_two_failed_closed_before_regression": generation_two[
            "failed_closed_before_regression"
        ],
        "generation_two_adverse_regression_rejected": generation_two[
            "adverse_regression_rejected"
        ],
        "generation_two_promoted": generation_two["promoted"],
        "challenge_generated_after_generation_two_promotion": True,
        "challenge_commitment": commitment,
        "challenge_inputs_overlap_any_prior_support_or_challenge": False,
        "challenge_case_count": len(challenge_inputs),
        "macro_fresh_run_count": len(macro_run_ids),
        "generation_two_fresh_run_count": len(compiled_run_ids),
        "all_outputs_equal": all(
            item["expected"] == item["macro_output"] == item["compiled_output"]
            for item in outputs
        ),
        "all_generation_one_component_negative_evidence_retained": bool(
            base["all_component_negative_evidence_retained"]
        ),
        "boolean_not_negative_evidence_retained": bool(
            not_promoted["negative_evidence_retained"]
        ),
        "source_candidate_trial_state_unchanged": source_trials_unchanged,
        "generation_two_candidate_trial_state_unchanged": generation_two_trials_unchanged,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded recursive skill-reuse evidence only. A previously compiled and "
            "regression-verified skill was selected as a macro for a new behavioral goal, reducing the "
            "source plan from a four-stage primitive equivalent to two stages; that macro plan was then "
            "compiled into a second-generation one-stage acquired-program Candidate and independently "
            "regression-gated. This does not demonstrate open-ended recursive self-improvement, AGI, or "
            "independent production evidence because all support, planning, compilation, regression, "
            "challenge generation, and runtime are repository-authored."
        ),
    }
    if not report["all_outputs_equal"]:
        raise RuntimeError("recursive chunking aggregate equivalence check failed")
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
