from __future__ import annotations

from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program, validate_program_descriptor
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.heterogeneous_retention_campaign import _digest, _measurement
from agi.learned_tool_chain_compilation import _compile_chain_program
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
    _validate_runtime_output,
)
from agi.memory_first_macro_resolver import resolve_verified_macro_with_memory
from agi.multiround_disambiguation_bank import (
    _base_support,
    _promoted_candidates,
    _target_answer,
    bank_examples,
)
from continual.acquired_program_tools import acquired_program_candidate_payload
from continual.candidate_regression import record_candidate_regression
from continual.learned_tools import LearnedToolError


class MemoryFirstMacroMaterializationError(RuntimeError):
    pass


def run_memory_first_macro_materialization(root: Path, seed: str) -> dict[str, Any]:
    """Learn ambiguity-resolving memory, compile the resolved route, and reuse both later.

    A first fresh set of three independently regression-promoted object→boolean Candidates is
    intentionally ambiguous on the base support. The memory-first resolver must commit probes before
    querying the repository-authored oracle, append exactly the counterexamples needed to isolate the
    target route, and leave source Candidate trial evidence unchanged. The resolved route is then
    compiled into a separate pure acquired-program Candidate, which must fail closed before its own
    exact-scope regression, retain an adverse protected-regression rejection, pass repeated target
    improvement with protected retention, and replay mechanically across fresh Engines.

    A later disjoint fresh Candidate set for the same exact task context must use the durable
    counterexamples before generating a new probe and resolve with zero oracle calls. The later route,
    the original resolved route, and the separately promoted compiled skill must agree on fresh
    challenges while all source and compiled Candidate trial ledgers remain unchanged.

    This is internal continual-learning and recursive skill-materialization evidence only. The task,
    oracle, regression measurements, challenges, and runtime are repository-authored; it is not AGI or
    independent production evidence.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    token = _digest({"campaign": "memory-first-macro-materialization-v1", "seed": seed})[:16]
    task_key = f"memory-materialization-has-a-{token}"
    support = _base_support()

    first_candidates = _promoted_candidates(root, f"{seed}:first")
    first_ids = [str(item["candidate_id"]) for item in first_candidates]
    first_trials_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in first_ids
    }
    if not all(first_trials_before.values()):
        raise MemoryFirstMacroMaterializationError(
            "first source Candidate lacks durable regression evidence"
        )

    first = resolve_verified_macro_with_memory(
        root,
        candidates=first_candidates,
        task_key=task_key,
        input_domain="object",
        output_domain="boolean",
        support=support,
        max_depth=1,
        max_rounds=3,
        oracle=_target_answer,
    )
    first_report = first["report"]
    if first_report["initial_matching_route_count"] != 3:
        raise MemoryFirstMacroMaterializationError("first campaign was not initially ambiguous")
    if first_report["route_counts_during_new_learning"] != [3, 2, 1]:
        raise MemoryFirstMacroMaterializationError(
            "memory-first learning did not monotonically isolate the target route"
        )
    if first_report["oracle_query_count"] != 2:
        raise MemoryFirstMacroMaterializationError("first campaign used an unexpected oracle budget")
    if not first_report["candidate_trial_state_unchanged"]:
        raise MemoryFirstMacroMaterializationError("resolver changed source Candidate trial evidence")

    remembered = bank_examples(
        root,
        task_key=task_key,
        input_domain="object",
        output_domain="boolean",
        support=support,
    )
    if len(remembered) != 2:
        raise MemoryFirstMacroMaterializationError("expected exactly two durable counterexamples")
    planning_examples = (*support, *remembered)
    selected = tuple(first["selected"])
    compiled_program = _compile_chain_program(selected, planning_examples)
    compiled_meta = validate_program_descriptor(compiled_program)
    if compiled_meta["input_domain"] != "object" or compiled_meta["output_domain"] != "boolean":
        raise MemoryFirstMacroMaterializationError("compiled program has unexpected domains")
    for example in planning_examples:
        source_output, _run_id, _refs = _execute_chain(root, selected, example.input)
        compiled_output = execute_program(compiled_program, example.input)
        if source_output != example.output or compiled_output != source_output:
            raise MemoryFirstMacroMaterializationError(
                "compiled skill changed committed memory-resolved behavior"
            )

    candidate_id = f"memory-materialized-{token}"
    tool_id = f"memory-materialized-tool-{token}"
    scope = "agi/acquired-program/compiled-plan/memory-resolved-has-a/v1"
    candidate_dir = root / ".continual" / "candidates" / candidate_id
    if candidate_dir.exists():
        raise MemoryFirstMacroMaterializationError(
            "compiled Candidate identity already exists; refusing to overwrite durable evidence"
        )
    _atomic_json(
        candidate_dir / "candidate.json",
        acquired_program_candidate_payload(
            candidate_id=candidate_id,
            tool_id=tool_id,
            scope=scope,
            program=compiled_program,
            description=(
                "Pure acquired skill compiled from a memory-first resolved verified-macro route after "
                "durable counterexample disambiguation."
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
            value={"a": 1, "novel": 1},
        )
    except LearnedToolError:
        baseline_failed_closed = True
    if not baseline_failed_closed:
        raise MemoryFirstMacroMaterializationError(
            "compiled Candidate activated before exact-scope regression"
        )

    target_task = f"withheld-{candidate_id}"
    target_domain = "memory-materialized:object-to-boolean"
    baseline: list[dict[str, Any]] = []
    trial: list[dict[str, Any]] = []
    for repeat_index in range(3):
        artifact = {
            "candidate_id": candidate_id,
            "repeat_index": repeat_index,
            "source_candidate_ids": first_ids,
            "compiled_program_sha256": _digest(compiled_program),
            "memory_entry_count": len(remembered),
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
            "source_trial_digests": {
                candidate_id_: _digest(first_trials_before[candidate_id_])
                for candidate_id_ in first_ids
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
        raise MemoryFirstMacroMaterializationError(
            "compiled Candidate ignored protected-regression failure"
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
        raise MemoryFirstMacroMaterializationError(
            "compiled Candidate failed valid exact-scope promotion"
        )

    compiled_trials_before_replay = _candidate_trial_snapshot(root, candidate_id)
    first_trials_before_replay = {
        candidate_id_: _candidate_trial_snapshot(root, candidate_id_)
        for candidate_id_ in first_ids
    }
    if first_trials_before_replay != first_trials_before:
        raise MemoryFirstMacroMaterializationError(
            "compiled Candidate promotion changed first source trial evidence"
        )
    if not compiled_trials_before_replay:
        raise MemoryFirstMacroMaterializationError("compiled Candidate lacks regression evidence")

    later_candidates = _promoted_candidates(root, f"{seed}:later")
    later_ids = [str(item["candidate_id"]) for item in later_candidates]
    if set(first_ids) & set(later_ids):
        raise MemoryFirstMacroMaterializationError(
            "later campaign reused first-campaign Candidate identity"
        )
    later_trials_before = {
        candidate_id_: _candidate_trial_snapshot(root, candidate_id_)
        for candidate_id_ in later_ids
    }
    if not all(later_trials_before.values()):
        raise MemoryFirstMacroMaterializationError(
            "later source Candidate lacks durable regression evidence"
        )

    def forbidden_oracle(_value: Any) -> Any:
        raise MemoryFirstMacroMaterializationError(
            "durable memory should resolve the later fresh Candidate set without an oracle"
        )

    later = resolve_verified_macro_with_memory(
        root,
        candidates=later_candidates,
        task_key=task_key,
        input_domain="object",
        output_domain="boolean",
        support=support,
        max_depth=1,
        max_rounds=3,
        oracle=forbidden_oracle,
    )
    later_report = later["report"]
    if later_report["remembered_counterexample_count"] != 2:
        raise MemoryFirstMacroMaterializationError("later campaign did not load durable memory")
    if later_report["route_count_after_memory"] != 1 or later_report["oracle_query_count"] != 0:
        raise MemoryFirstMacroMaterializationError(
            "later fresh Candidate set failed zero-oracle memory resolution"
        )
    later_selected = tuple(later["selected"])

    challenge_values = (
        {},
        {"a": 1},
        {"b": 1},
        {"a": False, "z": 1},
        {"z": 1},
    )
    replay_outputs: list[dict[str, Any]] = []
    fresh_engine_runs: list[str] = []
    for repeat_index in range(3):
        for case_index, value in enumerate(challenge_values):
            expected = _target_answer(value)
            first_output, _first_run, _first_refs = _execute_chain(root, selected, value)
            later_output, _later_run, _later_refs = _execute_chain(root, later_selected, value)
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
            if not (
                first_output == expected
                and later_output == expected
                and compiled_output == expected
            ):
                raise MemoryFirstMacroMaterializationError(
                    "memory-resolved source routes and compiled skill diverged on fresh challenge"
                )
            fresh_engine_runs.append(run_id)
            replay_outputs.append(
                {
                    "repeat_index": repeat_index,
                    "case_index": case_index,
                    "input_sha256": _digest(value),
                    "expected": expected,
                    "first_source_output": first_output,
                    "later_source_output": later_output,
                    "compiled_output": compiled_output,
                }
            )

    first_trials_after = {
        candidate_id_: _candidate_trial_snapshot(root, candidate_id_)
        for candidate_id_ in first_ids
    }
    later_trials_after = {
        candidate_id_: _candidate_trial_snapshot(root, candidate_id_)
        for candidate_id_ in later_ids
    }
    compiled_trials_after = _candidate_trial_snapshot(root, candidate_id)
    if first_trials_after != first_trials_before:
        raise MemoryFirstMacroMaterializationError("fresh replay changed first source trial evidence")
    if later_trials_after != later_trials_before:
        raise MemoryFirstMacroMaterializationError("fresh replay changed later source trial evidence")
    if compiled_trials_after != compiled_trials_before_replay:
        raise MemoryFirstMacroMaterializationError("fresh replay changed compiled Candidate trial evidence")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "memory-first-macro-materialization-v1",
        "task_key": task_key,
        "first_source_candidate_ids": first_ids,
        "later_source_candidate_ids": later_ids,
        "source_candidate_identity_sets_disjoint": not bool(set(first_ids) & set(later_ids)),
        "first_initial_matching_route_count": first_report["initial_matching_route_count"],
        "first_route_counts": first_report["route_counts_during_new_learning"],
        "first_oracle_query_count": first_report["oracle_query_count"],
        "durable_counterexample_count": len(remembered),
        "later_route_count_after_memory": later_report["route_count_after_memory"],
        "later_oracle_query_count": later_report["oracle_query_count"],
        "compiled_candidate_id": candidate_id,
        "compiled_scope": scope,
        "compiled_program_nodes": int(compiled_meta["nodes"]),
        "compiled_program_depth": int(compiled_meta["depth"]),
        "baseline_before_compiled_regression_failed_closed": baseline_failed_closed,
        "adverse_compiled_regression_rejected": not rejected["decision"]["adopt_candidate"],
        "compiled_candidate_promoted": bool(promoted["decision"]["adopt_candidate"]),
        "fresh_engine_run_count": len(fresh_engine_runs),
        "fresh_challenge_case_count": len(challenge_values),
        "all_fresh_outputs_equal": all(
            item["expected"]
            == item["first_source_output"]
            == item["later_source_output"]
            == item["compiled_output"]
            for item in replay_outputs
        ),
        "first_source_candidate_trial_state_unchanged": first_trials_after == first_trials_before,
        "later_source_candidate_trial_state_unchanged": later_trials_after == later_trials_before,
        "compiled_candidate_trial_state_unchanged": (
            compiled_trials_after == compiled_trials_before_replay
        ),
        "claim_boundary": (
            "Internal continual-learning and recursive skill-materialization evidence only. Durable "
            "counterexamples eliminated later oracle calls across disjoint fresh Candidate identities, "
            "and the resolved route was independently regression-gated and replayed as a compiled pure "
            "skill across fresh mechanical Engines. The task, oracle, regression measurements, "
            "challenges, and runtime remain repository-authored, so this is not AGI or independent "
            "production evidence."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "memory-first-macro-materialization"
        / f"materialization-{token}.json",
        report,
    )
    return report
