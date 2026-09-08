from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program, validate_program_descriptor
from agi.behavior_guided_tool_chain_discovery import _execute_chain, _typed_chains
from agi.cross_domain_recursive_macro_transfer import run_cross_domain_recursive_macro_transfer
from agi.heterogeneous_retention_campaign import _digest, _measurement, _promote_task
from agi.learned_tool_chain_compilation import _compile_chain_program, _select_unique_minimal_chain
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


class VerifiedMacroSynthesisError(RuntimeError):
    pass


def _load_verified_macro_items(
    root: Path,
    candidate_ids: Sequence[str],
) -> tuple[dict[str, Any], ...]:
    root = root.resolve()
    normalized = [str(candidate_id) for candidate_id in candidate_ids]
    if not normalized or any(not candidate_id for candidate_id in normalized):
        raise ValueError("candidate_ids must contain at least one non-empty Candidate ID")
    if len(set(normalized)) != len(normalized):
        raise ValueError("candidate_ids must not contain duplicates")

    loaded: list[dict[str, Any]] = []
    for candidate_id in normalized:
        path = root / ".continual" / "candidates" / candidate_id / "candidate.json"
        if not path.is_file():
            raise FileNotFoundError(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping) or raw.get("candidate_id") != candidate_id:
            raise VerifiedMacroSynthesisError("verified macro Candidate identity mismatch")
        spec = AcquiredProgramToolSpec.from_candidate(raw)
        if not candidate_verified_for_scope(root, raw, spec.scope):
            raise VerifiedMacroSynthesisError(
                f"Candidate is not exact-scope regression verified: {candidate_id}"
            )
        meta = validate_program_descriptor(spec.program)
        loaded.append(
            {
                "candidate_id": spec.candidate_id,
                "tool_id": spec.tool_id,
                "scope": spec.scope,
                "input_domain": str(meta["input_domain"]),
                "output_domain": str(meta["output_domain"]),
                "program": spec.program,
                "program_nodes": int(meta["nodes"]),
                "program_depth": int(meta["depth"]),
                "negative_evidence_retained": bool(raw.get("contradictory_evidence")),
            }
        )
    return tuple(loaded)


def synthesize_verified_macro_program(
    root: Path,
    *,
    candidate_ids: Sequence[str],
    input_domain: str,
    output_domain: str,
    examples: Sequence[ProgramExample],
    max_depth: int = 4,
) -> dict[str, Any]:
    """Compile a unique minimal behavior-matching chain of verified learned programs.

    This is a bounded synthesis primitive over already exact-scope-regression-verified acquired
    programs. It does not trust type compatibility alone: every typed route is tested against committed
    behavior examples through the real learned-tool dispatch path, including fail-closed treatment of
    deterministic partial-program rejection. The selected route must be uniquely minimal, and its
    programs are then substituted into one finite pure descriptor. Candidate trial ledgers are read-only
    throughout synthesis.

    The function deliberately does not promote the compiled descriptor. Callers must create a new
    Candidate and pass exact-scope regression before the result can become an active learned tool.
    """

    if not isinstance(input_domain, str) or not input_domain:
        raise ValueError("input_domain must be non-empty")
    if not isinstance(output_domain, str) or not output_domain:
        raise ValueError("output_domain must be non-empty")
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 1 <= max_depth <= 6:
        raise ValueError("max_depth must be an integer in [1, 6]")
    normalized_examples = tuple(examples)
    if len(normalized_examples) < 2:
        raise ValueError("at least two behavior examples are required")

    root = root.resolve()
    macros = _load_verified_macro_items(root, candidate_ids)
    trials_before = {
        str(item["candidate_id"]): _candidate_trial_snapshot(root, str(item["candidate_id"]))
        for item in macros
    }
    if not all(trials_before.values()):
        raise VerifiedMacroSynthesisError("a verified macro lacks durable regression trials")

    chains = _typed_chains(
        macros,
        input_domain=input_domain,
        output_domain=output_domain,
        max_depth=max_depth,
    )
    if not chains:
        raise VerifiedMacroSynthesisError("no type-correct verified macro chain is available")
    shortest_type_depth = min(len(chain) for chain in chains)
    selected = _select_unique_minimal_chain(root, chains, normalized_examples)
    selected_ids = [str(item["candidate_id"]) for item in selected]
    program = _compile_chain_program(selected, normalized_examples)
    meta = validate_program_descriptor(program)
    if meta["input_domain"] != input_domain or meta["output_domain"] != output_domain:
        raise VerifiedMacroSynthesisError("compiled macro program changed requested type")
    for example in normalized_examples:
        source_output, _run_id, _refs = _execute_chain(root, selected, example.input)
        compiled_output = execute_program(program, example.input)
        if source_output != example.output or compiled_output != source_output:
            raise VerifiedMacroSynthesisError("compiled macro program changed committed behavior")

    trials_after = {
        str(item["candidate_id"]): _candidate_trial_snapshot(root, str(item["candidate_id"]))
        for item in macros
    }
    if trials_after != trials_before:
        raise VerifiedMacroSynthesisError("macro synthesis consumed or rewrote Candidate trial evidence")

    result: dict[str, Any] = {
        "schema_version": 1,
        "candidate_pool_ids": [str(item["candidate_id"]) for item in macros],
        "typed_chain_count": len(chains),
        "shortest_type_chain_depth": shortest_type_depth,
        "selected_chain_depth": len(selected),
        "selected_candidate_ids": selected_ids,
        "compiled_program": program,
        "compiled_program_nodes": int(meta["nodes"]),
        "compiled_program_depth": int(meta["depth"]),
        "input_domain": input_domain,
        "output_domain": output_domain,
        "behavior_example_count": len(normalized_examples),
        "candidate_trial_state_unchanged": True,
        "all_source_negative_evidence_retained": all(
            bool(item["negative_evidence_retained"]) for item in macros
        ),
    }
    result["digest"] = _digest({key: value for key, value in result.items() if key != "digest"})
    return result


def _numeric_object_wrapper_spec() -> dict[str, Any]:
    return {
        "name": "generic-macro-object-wrapper",
        "input_domain": "numeric",
        "output_domain": "object",
        "examples": (
            ProgramExample(-4, {"score": -4}),
            ProgramExample(0, {"score": 0}),
            ProgramExample(6, {"score": 6}),
        ),
        "probe": 10,
        "expected": {"score": 10},
        "max_nodes": 3,
    }


def _numeric_threshold_distractor_spec() -> dict[str, Any]:
    return {
        "name": "generic-macro-direct-threshold",
        "input_domain": "numeric",
        "output_domain": "boolean",
        "examples": (
            ProgramExample(-2, False),
            ProgramExample(4, False),
            ProgramExample(5, True),
            ProgramExample(12, True),
        ),
        "probe": 11,
        "expected": True,
        "max_nodes": 4,
    }


def run_generic_verified_macro_synthesis(root: Path, seed: str) -> dict[str, Any]:
    """Demonstrate a third-level learned skill assembled through the generic macro synthesizer.

    A separately verified cross-domain recursive object-to-boolean macro is first produced by the prior
    campaign. New numeric-to-object and direct numeric-to-boolean Candidates are then independently
    promoted. The generic synthesizer receives only Candidate IDs, desired types, and committed
    examples for a new numeric inverted-absolute-threshold task. A one-stage direct threshold route is
    type-correct but wrong; the correct route wraps the input as an object and then invokes the recursive
    macro. The generic synthesizer must select and compile that route without hard-coded Candidate names
    or positions. The resulting third-level Candidate remains inactive until its own regression gate.

    This is internal bounded synthesis evidence, not independent production evidence or AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    source = run_cross_domain_recursive_macro_transfer(root, f"{seed}:recursive-source")
    if not source.get("passed") or not source.get("compiled_candidate_promoted"):
        raise VerifiedMacroSynthesisError("cross-domain recursive source macro was not verified")
    recursive_macro_id = str(source["compiled_candidate_id"])
    recursive_macro = _load_verified_macro_items(root, (recursive_macro_id,))[0]
    if recursive_macro["input_domain"] != "object" or recursive_macro["output_domain"] != "boolean":
        raise VerifiedMacroSynthesisError("recursive source macro has unexpected type")

    wrapper = _promote_task(
        root,
        seed=f"{seed}:generic-components",
        spec=_numeric_object_wrapper_spec(),
    )
    distractor = _promote_task(
        root,
        seed=f"{seed}:generic-components",
        spec=_numeric_threshold_distractor_spec(),
    )
    pool_ids = [
        str(wrapper["candidate_id"]),
        recursive_macro_id,
        str(distractor["candidate_id"]),
    ]
    pool_trials_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id) for candidate_id in pool_ids
    }
    if not all(pool_trials_before.values()):
        raise VerifiedMacroSynthesisError("generic synthesis pool lacks regression evidence")

    planning_examples = (
        ProgramExample(-19, False),
        ProgramExample(3, True),
        ProgramExample(21, False),
    )
    component_numeric_support = {-4, 0, 6, 10, -2, 4, 5, 12, 11}
    planning_inputs = {example.input for example in planning_examples}
    if planning_inputs & component_numeric_support:
        raise VerifiedMacroSynthesisError("generic planning examples overlap new component support")

    synthesized = synthesize_verified_macro_program(
        root,
        candidate_ids=pool_ids,
        input_domain="numeric",
        output_domain="boolean",
        examples=planning_examples,
        max_depth=2,
    )
    expected_selected = [str(wrapper["candidate_id"]), recursive_macro_id]
    if synthesized["shortest_type_chain_depth"] != 1:
        raise VerifiedMacroSynthesisError("generic synthesis lacks a shorter type-correct distractor")
    if synthesized["selected_candidate_ids"] != expected_selected:
        raise VerifiedMacroSynthesisError("generic synthesis did not select wrapper plus recursive macro")
    if synthesized["selected_chain_depth"] != 2:
        raise VerifiedMacroSynthesisError("generic synthesis selected unexpected route depth")

    program = synthesized["compiled_program"]
    token = _digest(
        {
            "seed": seed,
            "pool_ids": pool_ids,
            "selected_ids": synthesized["selected_candidate_ids"],
            "support_sha256": program["support_sha256"],
        }
    )[:16]
    candidate_id = f"generic-verified-macro-plan-{token}"
    tool_id = f"generic-verified-macro-tool-{token}"
    scope = "agi/acquired-program/compiled-plan/generic-recursive-numeric-threshold/v1"
    candidate_dir = root / ".continual" / "candidates" / candidate_id
    _atomic_json(
        candidate_dir / "candidate.json",
        acquired_program_candidate_payload(
            candidate_id=candidate_id,
            tool_id=tool_id,
            scope=scope,
            program=program,
            description=(
                "Third-level pure acquired program assembled by generic behavior-guided synthesis over "
                "independently verified learned macros."
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
            value=-19,
        )
    except LearnedToolError:
        baseline_failed_closed = True
    if not baseline_failed_closed:
        raise VerifiedMacroSynthesisError("generic compiled Candidate became callable before regression")

    target_task = f"withheld-{candidate_id}"
    target_domain = "generic-verified-macro-synthesis:numeric-to-boolean"
    baseline: list[dict[str, Any]] = []
    trial: list[dict[str, Any]] = []
    for repeat_index in range(3):
        artifact = {
            "candidate_id": candidate_id,
            "repeat_index": repeat_index,
            "generic_synthesis_digest": synthesized["digest"],
            "recursive_macro_candidate_id": recursive_macro_id,
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
            "recursive_macro_candidate_id": recursive_macro_id,
            "invariant": "all generic synthesis source macros remain independently verified",
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
        raise VerifiedMacroSynthesisError("generic compiled Candidate ignored protected regression")
    promoted = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline_path,
        candidate_path=valid_path,
        target_task_ids=[target_task],
    )
    if not promoted["decision"]["adopt_candidate"]:
        raise VerifiedMacroSynthesisError("generic compiled Candidate failed valid target improvement")

    compiled_trials_before = _candidate_trial_snapshot(root, candidate_id)
    if not compiled_trials_before:
        raise VerifiedMacroSynthesisError("generic compiled Candidate lacks regression evidence")

    commitment = _digest(
        {
            "seed": seed,
            "candidate_id": candidate_id,
            "recursive_macro_candidate_id": recursive_macro_id,
            "phase": "post-promotion-generic-macro-challenge-v1",
        }
    )
    large_negative = -(30 + int(commitment[:2], 16) % 50)
    large_positive = 30 + int(commitment[2:4], 16) % 50
    challenge_inputs = (large_negative, 2.5, large_positive)
    if set(challenge_inputs) & (planning_inputs | component_numeric_support):
        raise VerifiedMacroSynthesisError("generic challenge overlaps planning or component support")

    selected_items = _load_verified_macro_items(root, expected_selected)
    source_run_ids: list[str] = []
    compiled_run_ids: list[str] = []
    outputs: list[dict[str, Any]] = []
    for repeat_index in range(3):
        for case_index, value in enumerate(challenge_inputs):
            expected = abs(value) < 5
            source_output, source_run_id, refs = _execute_chain(root, selected_items, value)
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
                raise VerifiedMacroSynthesisError("generic macro replay diverged from selected source route")
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

    pool_trials_after = {
        candidate_id_value: _candidate_trial_snapshot(root, candidate_id_value)
        for candidate_id_value in pool_ids
    }
    compiled_trials_after = _candidate_trial_snapshot(root, candidate_id)
    pool_trials_unchanged = pool_trials_after == pool_trials_before
    compiled_trials_unchanged = compiled_trials_after == compiled_trials_before
    if not pool_trials_unchanged or not compiled_trials_unchanged:
        raise VerifiedMacroSynthesisError("generic macro replay changed Candidate trial evidence")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "generic-verified-macro-synthesis",
        "recursive_macro_candidate_id": recursive_macro_id,
        "source_candidate_ids": pool_ids,
        "generic_synthesis_digest": synthesized["digest"],
        "typed_chain_count": synthesized["typed_chain_count"],
        "shortest_type_chain_depth": synthesized["shortest_type_chain_depth"],
        "selected_chain_depth": synthesized["selected_chain_depth"],
        "selected_candidate_ids": synthesized["selected_candidate_ids"],
        "generic_synthesis_trial_state_unchanged": synthesized[
            "candidate_trial_state_unchanged"
        ],
        "generic_synthesis_negative_evidence_retained": synthesized[
            "all_source_negative_evidence_retained"
        ],
        "compiled_candidate_id": candidate_id,
        "compiled_tool_id": tool_id,
        "compiled_scope": scope,
        "compiled_program_nodes": synthesized["compiled_program_nodes"],
        "compiled_program_depth": synthesized["compiled_program_depth"],
        "planning_examples_overlap_new_component_support": False,
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
        "source_candidate_trial_state_unchanged": pool_trials_unchanged,
        "compiled_candidate_trial_state_unchanged": compiled_trials_unchanged,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded generic macro-synthesis evidence only. A generic synthesizer selected and "
            "compiled a route over independently regression-verified learned programs, including a "
            "recursively compiled cross-domain macro, while rejecting a shorter type-correct wrong "
            "route. The new third-level Candidate then passed its own exact-scope regression and "
            "fresh-Engine equivalence checks without consuming source trial ledgers. Tasks, behavior "
            "examples, evaluator, search bounds, and runtime remain repository-authored and credential-"
            "free, so this is not AGI or independent production evidence."
        ),
    }
    if not report["all_outputs_equal"]:
        raise VerifiedMacroSynthesisError("generic macro aggregate equivalence failed")
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "generic-verified-macro-synthesis"
        / f"generic-macro-synthesis-{_digest(seed)[:16]}.json",
        report,
    )
    return report
