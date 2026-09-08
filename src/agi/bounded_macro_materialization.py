from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.bounded_verified_macro_search import (
    _abs_spec,
    _add_one_spec,
    synthesize_with_bounded_verified_macro_search,
)
from agi.heterogeneous_retention_campaign import _digest, _measurement, _promote_task
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
    _validate_runtime_output,
)
from agi.verified_macro_library_discovery import _numeric_to_string_spec
from agi.verified_macro_synthesis import _load_verified_macro_items
from continual.acquired_program_tools import acquired_program_candidate_payload
from continual.candidate_regression import record_candidate_regression
from continual.learned_tools import LearnedToolError


class BoundedMacroMaterializationError(RuntimeError):
    pass


def run_bounded_macro_materialization(root: Path, seed: str) -> dict[str, Any]:
    """Turn a bounded best-first macro plan into a separately verified reusable learned skill.

    The task is decimal-string(abs(x)). Components are independently exact-scope regression promoted,
    discovered from durable Candidate state, selected by the bounded minimal-depth search, and compiled
    into one pure acquired program. The compiled Candidate must remain unavailable before its own
    regression, retain an explicit protected-regression rejection, then pass repeated target improvement
    and protected retention before any fresh-Engine replay.

    This is internal bounded skill-materialization evidence only. It does not satisfy the independent
    production evidence gate and is not an AGI claim.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    absolute = _promote_task(
        root,
        seed=f"{seed}:components",
        spec=_abs_spec(),
    )
    to_string = _promote_task(
        root,
        seed=f"{seed}:components",
        spec=_numeric_to_string_spec(),
    )
    distractors = [
        _promote_task(
            root,
            seed=f"{seed}:distractor-{index}",
            spec=_add_one_spec(index),
        )
        for index in range(4)
    ]
    source_ids = [
        str(absolute["candidate_id"]),
        str(to_string["candidate_id"]),
        *[str(item["candidate_id"]) for item in distractors],
    ]
    source_trials_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in source_ids
    }
    if not all(source_trials_before.values()):
        raise BoundedMacroMaterializationError("source macro lacks durable regression evidence")

    planning_examples = (
        ProgramExample(-13, "13"),
        ProgramExample(4, "4"),
        ProgramExample(-8, "8"),
    )
    synthesized = synthesize_with_bounded_verified_macro_search(
        root,
        input_domain="numeric",
        output_domain="string",
        examples=planning_examples,
        max_depth=3,
        max_library_candidates=16,
        max_relevant_candidates=16,
        max_expanded_prefixes=256,
        max_evaluated_complete_routes=64,
    )
    expected_selected = [str(absolute["candidate_id"]), str(to_string["candidate_id"])]
    if synthesized["selected_candidate_ids"] != expected_selected:
        raise BoundedMacroMaterializationError("bounded planner selected an unexpected source route")
    if synthesized["selected_depth"] != 2:
        raise BoundedMacroMaterializationError("bounded planner failed minimal-depth selection")

    program = synthesized["compiled_program"]
    token = _digest(
        {
            "seed": seed,
            "selected": synthesized["selected_candidate_ids"],
            "support": program["support_sha256"],
        }
    )[:16]
    candidate_id = f"bounded-materialized-plan-{token}"
    tool_id = f"bounded-materialized-tool-{token}"
    scope = "agi/acquired-program/compiled-plan/bounded-abs-string/v1"
    candidate_dir = root / ".continual" / "candidates" / candidate_id
    _atomic_json(
        candidate_dir / "candidate.json",
        acquired_program_candidate_payload(
            candidate_id=candidate_id,
            tool_id=tool_id,
            scope=scope,
            program=program,
            description=(
                "Pure learned skill compiled from the unique minimal behavior-matching route found by "
                "bounded best-first verified-macro search."
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
        raise BoundedMacroMaterializationError("compiled Candidate activated before regression")

    target_task = f"withheld-{candidate_id}"
    target_domain = "bounded-materialization:numeric-to-string"
    baseline: list[dict[str, Any]] = []
    trial: list[dict[str, Any]] = []
    for repeat_index in range(3):
        artifact = {
            "candidate_id": candidate_id,
            "repeat_index": repeat_index,
            "selected_candidate_ids": synthesized["selected_candidate_ids"],
            "compiled_support": program["support_sha256"],
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
                source_id: _digest(source_trials_before[source_id])
                for source_id in source_ids
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
        raise BoundedMacroMaterializationError("compiled Candidate ignored protected regression")
    promoted = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline_path,
        candidate_path=valid_path,
        target_task_ids=[target_task],
    )
    if not promoted["decision"]["adopt_candidate"]:
        raise BoundedMacroMaterializationError("compiled Candidate failed valid regression promotion")

    compiled_trials_before = _candidate_trial_snapshot(root, candidate_id)
    source_trials_before_replay = {
        source_id: _candidate_trial_snapshot(root, source_id)
        for source_id in source_ids
    }
    if not compiled_trials_before or source_trials_before_replay != source_trials_before:
        raise BoundedMacroMaterializationError("regression promotion changed source trial evidence")

    selected_items = _load_verified_macro_items(root, synthesized["selected_candidate_ids"])
    commitment = _digest(
        {
            "seed": seed,
            "candidate_id": candidate_id,
            "phase": "post-promotion-bounded-materialization-challenge-v1",
        }
    )
    magnitude = 20 + int(commitment[:2], 16) % 30
    challenge_inputs = (-magnitude, 6, -(magnitude + 7))
    planning_inputs = {example.input for example in planning_examples}
    if any(value in planning_inputs for value in challenge_inputs):
        raise BoundedMacroMaterializationError("materialization challenge overlaps planning support")

    source_runs: list[str] = []
    compiled_runs: list[str] = []
    outputs: list[dict[str, Any]] = []
    for repeat_index in range(3):
        for case_index, value in enumerate(challenge_inputs):
            expected = str(abs(value))
            source_output, source_run, _refs = _execute_chain(root, selected_items, value)
            engine = _mechanical_engine(root)
            compiled_run = _new_runtime_run(engine)
            response = _invoke(
                engine,
                compiled_run,
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
                raise BoundedMacroMaterializationError("materialized skill diverged on fresh challenge")
            source_runs.append(source_run)
            compiled_runs.append(compiled_run)
            outputs.append(
                {
                    "repeat_index": repeat_index,
                    "case_index": case_index,
                    "input_sha256": _digest(value),
                    "expected": expected,
                    "source_output": source_output,
                    "compiled_output": compiled_output,
                }
            )

    source_trials_after = {
        source_id: _candidate_trial_snapshot(root, source_id)
        for source_id in source_ids
    }
    compiled_trials_after = _candidate_trial_snapshot(root, candidate_id)
    if source_trials_after != source_trials_before:
        raise BoundedMacroMaterializationError("fresh replay changed source Candidate trial evidence")
    if compiled_trials_after != compiled_trials_before:
        raise BoundedMacroMaterializationError("fresh replay changed compiled Candidate trial evidence")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "bounded-macro-materialization",
        "source_candidate_count": len(source_ids),
        "selected_candidate_ids": synthesized["selected_candidate_ids"],
        "selected_depth": synthesized["selected_depth"],
        "expanded_prefix_count": synthesized["expanded_prefix_count"],
        "evaluated_complete_route_count": synthesized["evaluated_complete_route_count"],
        "compiled_candidate_id": candidate_id,
        "compiled_scope": scope,
        "baseline_before_compiled_regression_failed_closed": baseline_failed_closed,
        "adverse_compiled_regression_rejected": not rejected["decision"]["adopt_candidate"],
        "compiled_candidate_promoted": bool(promoted["decision"]["adopt_candidate"]),
        "challenge_generated_after_compiled_promotion": True,
        "challenge_case_count": len(challenge_inputs),
        "source_fresh_run_count": len(source_runs),
        "compiled_fresh_run_count": len(compiled_runs),
        "all_outputs_equal": all(
            item["expected"] == item["source_output"] == item["compiled_output"]
            for item in outputs
        ),
        "source_candidate_trial_state_unchanged": source_trials_after == source_trials_before,
        "compiled_candidate_trial_state_unchanged": compiled_trials_after == compiled_trials_before,
        "claim_boundary": (
            "Internal bounded skill-materialization evidence only. A best-first selected verified-macro "
            "route was compiled, independently regression-gated, and replayed across fresh mechanical "
            "Engines with immutable source/compiled trial evidence. Tasks, evaluator, challenges, and "
            "runtime remain repository-authored, so this is not AGI or independent production evidence."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "bounded-macro-materialization"
        / f"materialization-{_digest(seed)[:16]}.json",
        report,
    )
    return report
