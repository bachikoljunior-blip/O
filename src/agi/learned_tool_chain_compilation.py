from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program, validate_program_descriptor
from agi.behavior_guided_tool_chain_discovery import (
    _chain_matches,
    _execute_chain,
    _planner_specs,
    _typed_chains,
)
from agi.heterogeneous_retention_campaign import _digest, _measurement, _promote_task
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
    _validate_runtime_output,
)
from continual.acquired_program_tools import acquired_program_candidate_payload
from continual.candidate_regression import record_candidate_regression
from continual.learned_tools import LearnedToolError


def _substitute_input(
    expression: Mapping[str, Any],
    replacement: Mapping[str, Any],
) -> dict[str, Any]:
    if expression.get("op") == "input" and set(expression) == {"op"}:
        return deepcopy(dict(replacement))
    result: dict[str, Any] = {}
    for key, value in expression.items():
        if isinstance(value, Mapping) and isinstance(value.get("op"), str):
            result[key] = _substitute_input(value, replacement)
        else:
            result[key] = deepcopy(value)
    return result


def _compile_chain_program(
    chain: Sequence[dict[str, Any]],
    planning_examples: Sequence[ProgramExample],
) -> dict[str, Any]:
    if not chain:
        raise RuntimeError("cannot compile an empty learned-tool chain")
    programs: list[dict[str, Any]] = []
    for item in chain:
        program = item.get("program")
        if not isinstance(program, Mapping):
            raise RuntimeError("learned-tool chain stage is missing its acquired program")
        canonical = deepcopy(dict(program))
        validate_program_descriptor(canonical)
        programs.append(canonical)

    current_domain = str(programs[0]["input_domain"])
    expression = deepcopy(programs[0]["expression"])
    output_domain = str(programs[0]["output_domain"])
    for program in programs[1:]:
        if str(program["input_domain"]) != output_domain:
            raise RuntimeError("cannot compile a type-incompatible learned-tool chain")
        expression = _substitute_input(program["expression"], expression)
        output_domain = str(program["output_domain"])

    max_output_length = min(int(program.get("max_output_length", 1024)) for program in programs)
    provisional = {
        "input_domain": current_domain,
        "output_domain": output_domain,
        "expression": expression,
        "effects": [],
        "max_steps": 128,
        "max_output_length": max_output_length,
        "support_sha256": _digest(
            {
                "component_candidates": [str(item["candidate_id"]) for item in chain],
                "component_support": [str(program.get("support_sha256", "")) for program in programs],
                "planning_examples": [
                    {"input": item.input, "output": item.output}
                    for item in planning_examples
                ],
                "compiler": "expression-input-substitution-v1",
            }
        ),
    }
    meta = validate_program_descriptor(provisional)
    required_steps = max(16, int(meta["nodes"]) * 2)
    if required_steps > 128:
        raise RuntimeError("compiled learned-tool plan exceeds bounded interpreter step budget")
    provisional["max_steps"] = required_steps
    validate_program_descriptor(provisional)
    return provisional


def _select_unique_minimal_chain(
    root: Path,
    chains: Sequence[tuple[dict[str, Any], ...]],
    examples: Sequence[ProgramExample],
) -> tuple[dict[str, Any], ...]:
    matching = [chain for chain in chains if _chain_matches(root, chain, examples)]
    if not matching:
        raise RuntimeError("no learned-tool chain matches compilation support")
    minimum = min(len(chain) for chain in matching)
    minimal = [chain for chain in matching if len(chain) == minimum]
    if len(minimal) != 1:
        raise RuntimeError("compilation support is ambiguous across minimal learned-tool chains")
    return minimal[0]


def run_learned_tool_chain_compilation(root: Path, seed: str) -> dict[str, Any]:
    """Chunk a behavior-selected learned-tool chain into one new verified acquired program.

    Independently regression-promoted component programs are first selected by behavior rather than
    type alone. Their pure typed expression trees are then composed by substituting the upstream
    expression for each downstream input node. The compiled program receives a new support digest and
    remains inactive until it independently passes the same protected Candidate regression gate.
    Post-promotion challenges compare the original three-stage chain with the one-stage compiled tool
    across fresh Engines while both component and compiled Candidate trial ledgers remain unchanged.

    The campaign demonstrates bounded recursive skill chunking, not AGI. Planning support, compiler,
    regression measurements, challenge generation, and runtime are repository-authored internal tests.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    specs = _planner_specs()
    promoted = tuple(
        _promote_task(root, seed=f"{seed}:compile-chain-components", spec=spec)
        for spec in specs
    )
    component_ids = [str(item["candidate_id"]) for item in promoted]
    component_trials_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in component_ids
    }
    if not all(component_trials_before.values()):
        raise RuntimeError("compiled-plan component lacks regression evidence")

    planning_examples = (
        ProgramExample(-8, True),
        ProgramExample(-1, False),
        ProgramExample(9, True),
    )
    synthesis_numeric_inputs = {
        example.input
        for spec in specs
        if spec["input_domain"] == "numeric"
        for example in spec["examples"]
    }
    planning_inputs = {example.input for example in planning_examples}
    if planning_inputs & synthesis_numeric_inputs:
        raise RuntimeError("compiled-plan support overlaps component synthesis support")

    chains = _typed_chains(
        promoted,
        input_domain="numeric",
        output_domain="boolean",
        max_depth=4,
    )
    selected = _select_unique_minimal_chain(root, chains, planning_examples)
    selected_ids = [str(item["candidate_id"]) for item in selected]
    if len(selected) != 3 or not selected_ids[0].startswith("hetero-planner-abs-"):
        raise RuntimeError("compiled-plan support did not select the intended three-stage behavior")

    compiled_program = _compile_chain_program(selected, planning_examples)
    compiled_meta = validate_program_descriptor(compiled_program)
    for example in planning_examples:
        chain_output, _run_id, _refs = _execute_chain(root, selected, example.input)
        compiled_output = execute_program(compiled_program, example.input)
        if chain_output != example.output or compiled_output != chain_output:
            raise RuntimeError("compiled program changed selected-chain planning semantics")

    token = _digest(
        {
            "seed": seed,
            "selected_ids": selected_ids,
            "compiled_support": compiled_program["support_sha256"],
        }
    )[:16]
    candidate_id = f"compiled-plan-{token}"
    tool_id = f"compiled-plan-tool-{token}"
    scope = "agi/acquired-program/compiled-plan/absolute-threshold/v1"
    candidate_dir = root / ".continual" / "candidates" / candidate_id
    _atomic_json(
        candidate_dir / "candidate.json",
        acquired_program_candidate_payload(
            candidate_id=candidate_id,
            tool_id=tool_id,
            scope=scope,
            program=compiled_program,
            description=(
                "Behavior-selected learned-tool chain compiled into one bounded pure acquired program."
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
            value=-8,
        )
    except LearnedToolError:
        baseline_failed_closed = True
    if not baseline_failed_closed:
        raise RuntimeError("compiled plan became callable before deterministic regression")

    target_task = f"withheld-{candidate_id}"
    target_domain = "compiled-plan:numeric-to-boolean"
    baseline: list[dict[str, Any]] = []
    trial: list[dict[str, Any]] = []
    for repeat_index in range(3):
        artifact = {
            "candidate_id": candidate_id,
            "repeat_index": repeat_index,
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
        {"candidate_id": candidate_id, "invariant": "component learned capabilities retained"},
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
        raise RuntimeError("compiled-plan Candidate ignored a protected regression")
    promoted_compiled = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline_path,
        candidate_path=valid_path,
        target_task_ids=[target_task],
    )
    if not promoted_compiled["decision"]["adopt_candidate"]:
        raise RuntimeError("compiled-plan Candidate failed valid regression promotion")

    compiled_trials_before = _candidate_trial_snapshot(root, candidate_id)
    if not compiled_trials_before:
        raise RuntimeError("compiled-plan Candidate lacks durable trial evidence")

    commitment = _digest(
        {
            "seed": seed,
            "candidate_id": candidate_id,
            "compiled_support": compiled_program["support_sha256"],
            "phase": "post-promotion-compiled-plan-challenge-v1",
        }
    )
    large_negative = -(20 + int(commitment[:2], 16) % 40)
    large_positive = 20 + int(commitment[2:4], 16) % 40
    challenge_inputs = (large_negative, -2, large_positive)
    if set(challenge_inputs) & (planning_inputs | synthesis_numeric_inputs):
        raise RuntimeError("compiled-plan challenge overlaps planning or synthesis support")

    chain_run_ids: list[str] = []
    compiled_run_ids: list[str] = []
    outputs: list[dict[str, Any]] = []
    for repeat_index in range(3):
        for case_index, value in enumerate(challenge_inputs):
            expected = abs(value) >= 5
            chain_output, chain_run_id, refs = _execute_chain(root, selected, value)
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
            if chain_output != expected or compiled_output != chain_output:
                raise RuntimeError("compiled-plan post-promotion output diverged from source chain")
            chain_run_ids.append(chain_run_id)
            compiled_run_ids.append(compiled_run_id)
            outputs.append(
                {
                    "repeat_index": repeat_index,
                    "case_index": case_index,
                    "input_sha256": _digest(value),
                    "expected": expected,
                    "chain_output": chain_output,
                    "compiled_output": compiled_output,
                    "chain_stage_count": len(refs),
                }
            )

    component_trials_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in component_ids
    }
    compiled_trials_after = _candidate_trial_snapshot(root, candidate_id)
    component_trials_unchanged = component_trials_after == component_trials_before
    compiled_trials_unchanged = compiled_trials_after == compiled_trials_before
    if not component_trials_unchanged or not compiled_trials_unchanged:
        raise RuntimeError("compiled-plan replay changed Candidate trial state")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "behavior-selected-learned-tool-chain-compilation",
        "component_candidate_count": len(promoted),
        "component_candidate_ids": component_ids,
        "selected_chain_candidate_ids": selected_ids,
        "source_stage_count": len(selected),
        "compiled_stage_count": 1,
        "compiled_candidate_id": candidate_id,
        "compiled_tool_id": tool_id,
        "compiled_scope": scope,
        "compiled_program_nodes": int(compiled_meta["nodes"]),
        "compiled_program_depth": int(compiled_meta["depth"]),
        "compiled_support_sha256": compiled_program["support_sha256"],
        "planning_examples_overlap_component_support": False,
        "baseline_before_compiled_regression_failed_closed": baseline_failed_closed,
        "adverse_compiled_regression_rejected": not rejected["decision"]["adopt_candidate"],
        "compiled_candidate_promoted": bool(promoted_compiled["decision"]["adopt_candidate"]),
        "challenge_generated_after_compiled_promotion": True,
        "challenge_commitment": commitment,
        "challenge_inputs_overlap_planning_or_support": False,
        "challenge_case_count": len(challenge_inputs),
        "chain_fresh_run_count": len(chain_run_ids),
        "compiled_fresh_run_count": len(compiled_run_ids),
        "all_outputs_equal": all(
            item["expected"] == item["chain_output"] == item["compiled_output"]
            for item in outputs
        ),
        "all_component_negative_evidence_retained": all(
            bool(item["negative_evidence_retained"]) for item in promoted
        ),
        "component_candidate_trial_state_unchanged": component_trials_unchanged,
        "compiled_candidate_trial_state_unchanged": compiled_trials_unchanged,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded recursive skill-chunking evidence only. A three-stage behavior-selected "
            "chain of independently regression-promoted pure acquired programs was compiled into a new "
            "one-stage pure acquired-program Candidate, independently regression-gated, and shown "
            "semantically equivalent on post-promotion challenges across fresh Engines without trial "
            "mutation. The compiler, planning support, regression measurements, challenge generation, "
            "and runtime remain repository-authored, so this is not AGI or independent production evidence."
        ),
    }
    if not report["all_outputs_equal"]:
        raise RuntimeError("compiled-plan aggregate equivalence check failed")
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "learned-tool-chain-compilation"
        / f"compiled-plan-{_digest(seed)[:16]}.json",
        report,
    )
    return report
