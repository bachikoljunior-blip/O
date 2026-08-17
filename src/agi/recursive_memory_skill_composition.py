from __future__ import annotations

from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program, validate_program_descriptor
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.bounded_verified_macro_search import select_unique_minimal_verified_macro_chain
from agi.heterogeneous_retention_campaign import _digest, _measurement, _promote_task
from agi.learned_tool_chain_compilation import _compile_chain_program
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
    _validate_runtime_output,
)
from agi.memory_first_macro_materialization import run_memory_first_macro_materialization
from agi.multiround_disambiguation_bank import _target_answer
from agi.verified_macro_synthesis import _load_verified_macro_items
from continual.acquired_program_tools import acquired_program_candidate_payload
from continual.candidate_regression import record_candidate_regression
from continual.learned_tools import LearnedToolError


class RecursiveMemorySkillCompositionError(RuntimeError):
    pass


def _not_spec() -> dict[str, Any]:
    return {
        "name": "recursive-memory-not",
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


def _identity_spec() -> dict[str, Any]:
    return {
        "name": "recursive-memory-identity",
        "input_domain": "boolean",
        "output_domain": "boolean",
        "examples": (
            ProgramExample(True, True),
            ProgramExample(False, False),
            ProgramExample(True, True),
        ),
        "probe": False,
        "expected": False,
        "max_nodes": 1,
    }


def run_recursive_memory_skill_composition(root: Path, seed: str) -> dict[str, Any]:
    """Recursively reuse a memory-materialized skill to learn and gate a second-generation skill.

    The first stage runs the memory-first materialization campaign, yielding a separately regression-
    promoted object→boolean acquired skill for has-key-a. Two fresh exact-scope boolean→boolean learned
    skills are then promoted: logical negation and an identity distractor. A bounded behavior search must
    select the unique minimal has-a→not route for a new object→boolean task, compile that route into a
    second-generation pure acquired program, keep it inactive before its own regression, retain an
    adverse protected-regression rejection, then promote it only after repeated target improvement and
    protected retention. Fresh mechanical Engines must replay the second-generation skill while every
    component and compiled Candidate trial ledger stays unchanged.

    This demonstrates bounded recursive reuse of a skill whose source route was itself chosen using
    durable active-learning memory. All tasks, measurements, challenges, and runtime are repository-
    authored internal evidence; this is not AGI or independent production evidence.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    memory_report = run_memory_first_macro_materialization(root, f"{seed}:memory-base")
    if not memory_report["passed"] or not memory_report["compiled_candidate_promoted"]:
        raise RecursiveMemorySkillCompositionError(
            "memory-first base skill did not reach verified materialization"
        )
    base_candidate_id = str(memory_report["compiled_candidate_id"])

    negated = _promote_task(root, seed=f"{seed}:negation", spec=_not_spec())
    identity = _promote_task(root, seed=f"{seed}:identity", spec=_identity_spec())
    component_ids = [
        base_candidate_id,
        str(negated["candidate_id"]),
        str(identity["candidate_id"]),
    ]
    components = _load_verified_macro_items(root, component_ids)
    component_trials_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in component_ids
    }
    if not all(component_trials_before.values()):
        raise RecursiveMemorySkillCompositionError(
            "recursive composition component lacks durable regression evidence"
        )

    planning_examples = (
        ProgramExample({}, True),
        ProgramExample({"a": 1}, False),
        ProgramExample({"b": 1}, True),
        ProgramExample({"a": False, "b": 1}, False),
    )
    selected, search_metrics = select_unique_minimal_verified_macro_chain(
        root,
        candidates=components,
        input_domain="object",
        output_domain="boolean",
        examples=planning_examples,
        max_depth=3,
        max_expanded_prefixes=64,
        max_evaluated_complete_routes=32,
    )
    selected_ids = [str(item["candidate_id"]) for item in selected]
    expected_selected = [base_candidate_id, str(negated["candidate_id"])]
    if selected_ids != expected_selected:
        raise RecursiveMemorySkillCompositionError(
            "bounded behavior search selected an unexpected recursive route"
        )
    if search_metrics["selected_depth"] != 2:
        raise RecursiveMemorySkillCompositionError(
            "recursive route was not selected at minimal depth two"
        )

    compiled_program = _compile_chain_program(selected, planning_examples)
    compiled_meta = validate_program_descriptor(compiled_program)
    for example in planning_examples:
        source_output, _run_id, _refs = _execute_chain(root, selected, example.input)
        compiled_output = execute_program(compiled_program, example.input)
        if source_output != example.output or compiled_output != source_output:
            raise RecursiveMemorySkillCompositionError(
                "second-generation compilation changed committed behavior"
            )

    token = _digest(
        {
            "campaign": "recursive-memory-skill-composition-v1",
            "seed": seed,
            "selected": selected_ids,
            "support": compiled_program["support_sha256"],
        }
    )[:16]
    candidate_id = f"recursive-memory-skill-{token}"
    tool_id = f"recursive-memory-tool-{token}"
    scope = "agi/acquired-program/compiled-plan/recursive-memory-not-has-a/v1"
    candidate_dir = root / ".continual" / "candidates" / candidate_id
    if candidate_dir.exists():
        raise RecursiveMemorySkillCompositionError(
            "second-generation Candidate identity already exists; refusing evidence overwrite"
        )
    _atomic_json(
        candidate_dir / "candidate.json",
        acquired_program_candidate_payload(
            candidate_id=candidate_id,
            tool_id=tool_id,
            scope=scope,
            program=compiled_program,
            description=(
                "Second-generation pure learned skill compiled from a memory-materialized object "
                "predicate followed by an independently verified learned negation."
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
            value={"a": 1, "fresh": True},
        )
    except LearnedToolError:
        baseline_failed_closed = True
    if not baseline_failed_closed:
        raise RecursiveMemorySkillCompositionError(
            "second-generation Candidate activated before its own regression"
        )

    target_task = f"withheld-{candidate_id}"
    target_domain = "recursive-memory-skill:object-to-boolean"
    baseline: list[dict[str, Any]] = []
    trial: list[dict[str, Any]] = []
    for repeat_index in range(3):
        artifact = {
            "candidate_id": candidate_id,
            "repeat_index": repeat_index,
            "selected_candidate_ids": selected_ids,
            "compiled_program_sha256": _digest(compiled_program),
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
            "component_trial_digests": {
                component_id: _digest(component_trials_before[component_id])
                for component_id in component_ids
            },
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
        raise RecursiveMemorySkillCompositionError(
            "second-generation Candidate ignored protected-regression failure"
        )
    promoted = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline_path,
        candidate_path=valid_path,
        target_task_ids=[target_task],
    )
    if not promoted["decision"]["adopt_candidate"]:
        raise RecursiveMemorySkillCompositionError(
            "second-generation Candidate failed valid promotion"
        )

    component_trials_before_replay = {
        component_id: _candidate_trial_snapshot(root, component_id)
        for component_id in component_ids
    }
    if component_trials_before_replay != component_trials_before:
        raise RecursiveMemorySkillCompositionError(
            "second-generation promotion changed component trial evidence"
        )
    compiled_trials_before_replay = _candidate_trial_snapshot(root, candidate_id)
    if not compiled_trials_before_replay:
        raise RecursiveMemorySkillCompositionError(
            "second-generation Candidate lacks durable regression evidence"
        )

    challenge_values = (
        {"z": 1},
        {"a": 0, "z": 2},
        {"b": 0, "z": 3},
        {"a": False, "fresh": 4},
        {"c": 1, "fresh": 5},
    )
    planning_inputs = {repr(example.input) for example in planning_examples}
    if any(repr(value) in planning_inputs for value in challenge_values):
        raise RecursiveMemorySkillCompositionError(
            "recursive fresh challenge overlaps planning support"
        )

    replay_outputs: list[dict[str, Any]] = []
    fresh_engine_runs: list[str] = []
    for repeat_index in range(3):
        for case_index, value in enumerate(challenge_values):
            expected = not _target_answer(value)
            source_output, _source_run, _refs = _execute_chain(root, selected, value)
            engine = _mechanical_engine(root)
            run_id = _new_runtime_run(engine)
            response = _invoke(
                engine,
                run_id,
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
                raise RecursiveMemorySkillCompositionError(
                    "recursive source route and second-generation skill diverged on fresh challenge"
                )
            fresh_engine_runs.append(run_id)
            replay_outputs.append(
                {
                    "repeat_index": repeat_index,
                    "case_index": case_index,
                    "input_sha256": _digest(value),
                    "expected": expected,
                    "source_output": source_output,
                    "compiled_output": compiled_output,
                }
            )

    component_trials_after = {
        component_id: _candidate_trial_snapshot(root, component_id)
        for component_id in component_ids
    }
    compiled_trials_after = _candidate_trial_snapshot(root, candidate_id)
    if component_trials_after != component_trials_before:
        raise RecursiveMemorySkillCompositionError(
            "fresh recursive replay changed component Candidate trial evidence"
        )
    if compiled_trials_after != compiled_trials_before_replay:
        raise RecursiveMemorySkillCompositionError(
            "fresh recursive replay changed second-generation Candidate trial evidence"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "recursive-memory-skill-composition-v1",
        "memory_base_candidate_id": base_candidate_id,
        "memory_base_first_oracle_query_count": memory_report["first_oracle_query_count"],
        "memory_base_later_oracle_query_count": memory_report["later_oracle_query_count"],
        "memory_base_durable_counterexample_count": memory_report["durable_counterexample_count"],
        "component_candidate_ids": component_ids,
        "selected_candidate_ids": selected_ids,
        "selected_depth": search_metrics["selected_depth"],
        "expanded_prefix_count": search_metrics["expanded_prefix_count"],
        "evaluated_complete_route_count": search_metrics["evaluated_complete_route_count"],
        "second_generation_candidate_id": candidate_id,
        "second_generation_scope": scope,
        "compiled_program_nodes": int(compiled_meta["nodes"]),
        "compiled_program_depth": int(compiled_meta["depth"]),
        "baseline_before_second_generation_regression_failed_closed": baseline_failed_closed,
        "adverse_second_generation_regression_rejected": not rejected["decision"]["adopt_candidate"],
        "second_generation_candidate_promoted": bool(promoted["decision"]["adopt_candidate"]),
        "fresh_engine_run_count": len(fresh_engine_runs),
        "fresh_challenge_case_count": len(challenge_values),
        "all_fresh_outputs_equal": all(
            item["expected"] == item["source_output"] == item["compiled_output"]
            for item in replay_outputs
        ),
        "component_candidate_trial_state_unchanged": (
            component_trials_after == component_trials_before
        ),
        "second_generation_candidate_trial_state_unchanged": (
            compiled_trials_after == compiled_trials_before_replay
        ),
        "claim_boundary": (
            "Internal bounded recursive skill-reuse evidence only. A skill whose source route was "
            "resolved using durable active-learning memory was reused as a verified component, composed "
            "with an independently promoted negation, compiled into a second-generation skill, and "
            "independently regression-gated before fresh-Engine replay. Repository-authored tasks, "
            "measurements, challenges, and runtime do not satisfy the independent production AGI gate."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "recursive-memory-skill-composition"
        / f"composition-{token}.json",
        report,
    )
    return report
