from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, validate_program_descriptor
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.heterogeneous_retention_campaign import _digest, _measurement, _promote_task
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
    _validate_runtime_output,
)
from agi.verified_macro_synthesis import synthesize_verified_macro_program
from continual.acquired_program_tools import (
    AcquiredProgramToolSpec,
    acquired_program_candidate_payload,
)
from continual.candidate_regression import candidate_verified_for_scope, record_candidate_regression
from continual.learned_tools import LearnedToolError


class VerifiedMacroLibraryError(RuntimeError):
    pass


def discover_verified_acquired_program_ids(
    root: Path,
    *,
    max_candidates: int = 64,
) -> list[str]:
    """Discover the bounded exact-scope verified acquired-program library.

    Candidate identities are derived from durable repository state instead of supplied by a task
    planner. Every returned Candidate must parse as an acquired-program tool and pass the existing
    immutable exact-scope integrity/regression check. The discovery fails closed rather than silently
    truncating when the library exceeds its configured bound, because silent truncation could hide the
    only valid route or make task behavior depend on filesystem ordering.
    """

    if (
        not isinstance(max_candidates, int)
        or isinstance(max_candidates, bool)
        or not 1 <= max_candidates <= 256
    ):
        raise ValueError("max_candidates must be an integer in [1, 256]")
    root = root.resolve()
    candidates_dir = root / ".continual" / "candidates"
    if not candidates_dir.exists():
        return []

    verified: list[str] = []
    for path in sorted(candidates_dir.glob("*/candidate.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping) or raw.get("target_component") != "acquired_program":
            continue
        spec = AcquiredProgramToolSpec.from_candidate(raw)
        if candidate_verified_for_scope(root, raw, spec.scope):
            verified.append(spec.candidate_id)
    if len(verified) > max_candidates:
        raise VerifiedMacroLibraryError(
            f"verified acquired-program library exceeds bound: {len(verified)}>{max_candidates}"
        )
    return verified


def synthesize_from_verified_macro_library(
    root: Path,
    *,
    input_domain: str,
    output_domain: str,
    examples: Sequence[ProgramExample],
    max_depth: int = 4,
    max_candidates: int = 64,
) -> dict[str, Any]:
    """Discover the verified macro library and synthesize without caller-supplied Candidate IDs."""

    candidate_ids = discover_verified_acquired_program_ids(
        root,
        max_candidates=max_candidates,
    )
    if not candidate_ids:
        raise VerifiedMacroLibraryError("verified acquired-program library is empty")
    result = synthesize_verified_macro_program(
        root,
        candidate_ids=candidate_ids,
        input_domain=input_domain,
        output_domain=output_domain,
        examples=examples,
        max_depth=max_depth,
    )
    return {
        **result,
        "library_candidate_ids": candidate_ids,
        "library_candidate_count": len(candidate_ids),
        "candidate_ids_supplied_by_caller": False,
    }


def _sequence_sum_spec() -> dict[str, Any]:
    return {
        "name": "library-sequence-sum",
        "input_domain": "sequence",
        "output_domain": "numeric",
        "examples": (
            ProgramExample([1, 2], 3),
            ProgramExample([-3, 1], -2),
            ProgramExample([4, 0, 2], 6),
        ),
        "probe": [-5, 2, 1],
        "expected": -2,
        "max_nodes": 2,
    }


def _numeric_to_string_spec() -> dict[str, Any]:
    return {
        "name": "library-numeric-to-string",
        "input_domain": "numeric",
        "output_domain": "string",
        "examples": (
            ProgramExample(-7, "-7"),
            ProgramExample(0, "0"),
            ProgramExample(9, "9"),
        ),
        "probe": 13,
        "expected": "13",
        "max_nodes": 2,
    }


def _sequence_length_string_distractor_spec() -> dict[str, Any]:
    return {
        "name": "library-sequence-length-string",
        "input_domain": "sequence",
        "output_domain": "string",
        "examples": (
            ProgramExample([8], "1"),
            ProgramExample([8, 9], "2"),
            ProgramExample([8, 9, 10], "3"),
        ),
        "probe": [8, 9, 10, 11],
        "expected": "4",
        "max_nodes": 3,
    }


def run_verified_macro_library_discovery(root: Path, seed: str) -> dict[str, Any]:
    """Discover useful learned tools from the durable library and compile a new skill.

    The task is to return the decimal string of the numeric sum of a sequence. Three independently
    regression-promoted learned tools are added to the durable library: sequence sum, numeric-to-string,
    and a shorter direct sequence-to-string length distractor. The synthesis call receives no Candidate
    IDs. It must scan exact-scope verified acquired programs, reject the shorter type-correct length
    route using behavior examples, select sum→to-string, compile the route, and then submit that new
    compiled Candidate to its own exact-scope regression gate before replay.

    This is bounded internal library-discovery evidence. The library, examples, search bounds,
    evaluator, challenge generation, and runtime are repository-authored; no independent production
    evidence or AGI claim follows.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    sequence_sum = _promote_task(
        root,
        seed=f"{seed}:library-components",
        spec=_sequence_sum_spec(),
    )
    numeric_to_string = _promote_task(
        root,
        seed=f"{seed}:library-components",
        spec=_numeric_to_string_spec(),
    )
    distractor = _promote_task(
        root,
        seed=f"{seed}:library-components",
        spec=_sequence_length_string_distractor_spec(),
    )
    generated_ids = [
        str(sequence_sum["candidate_id"]),
        str(numeric_to_string["candidate_id"]),
        str(distractor["candidate_id"]),
    ]
    generated_trials_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in generated_ids
    }
    if not all(generated_trials_before.values()):
        raise VerifiedMacroLibraryError("generated library component lacks regression evidence")

    planning_examples = (
        ProgramExample([10, -1], "9"),
        ProgramExample([2, 1, 0], "3"),
        ProgramExample([-8, 1], "-7"),
    )
    component_support = {
        (1, 2),
        (-3, 1),
        (4, 0, 2),
        (-5, 2, 1),
        (8,),
        (8, 9),
        (8, 9, 10),
        (8, 9, 10, 11),
    }
    planning_inputs = {tuple(item.input) for item in planning_examples}
    if planning_inputs & component_support:
        raise VerifiedMacroLibraryError("planning examples overlap generated component support")

    synthesized = synthesize_from_verified_macro_library(
        root,
        input_domain="sequence",
        output_domain="string",
        examples=planning_examples,
        max_depth=2,
        max_candidates=64,
    )
    expected_selected = generated_ids[:2]
    if synthesized["candidate_ids_supplied_by_caller"]:
        raise VerifiedMacroLibraryError("library synthesis unexpectedly relied on caller Candidate IDs")
    if not set(generated_ids).issubset(set(synthesized["library_candidate_ids"])):
        raise VerifiedMacroLibraryError("library discovery omitted newly verified components")
    if synthesized["shortest_type_chain_depth"] != 1:
        raise VerifiedMacroLibraryError("library task lacks the shorter type-correct distractor")
    if synthesized["selected_chain_depth"] != 2:
        raise VerifiedMacroLibraryError("library synthesis selected unexpected route depth")
    if synthesized["selected_candidate_ids"] != expected_selected:
        raise VerifiedMacroLibraryError("library synthesis did not select sum then numeric-to-string")

    program = synthesized["compiled_program"]
    meta = validate_program_descriptor(program)
    token = _digest(
        {
            "seed": seed,
            "library_digest": synthesized["digest"],
            "selected": synthesized["selected_candidate_ids"],
            "program_support": program["support_sha256"],
        }
    )[:16]
    candidate_id = f"library-discovered-plan-{token}"
    tool_id = f"library-discovered-tool-{token}"
    scope = "agi/acquired-program/compiled-plan/library-discovered-sequence-sum-string/v1"
    candidate_dir = root / ".continual" / "candidates" / candidate_id
    _atomic_json(
        candidate_dir / "candidate.json",
        acquired_program_candidate_payload(
            candidate_id=candidate_id,
            tool_id=tool_id,
            scope=scope,
            program=program,
            description=(
                "Pure sequence-to-string acquired program compiled from a behavior-selected route "
                "discovered automatically in the exact-scope verified macro library."
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
            value=[10, -1],
        )
    except LearnedToolError:
        baseline_failed_closed = True
    if not baseline_failed_closed:
        raise VerifiedMacroLibraryError("library-compiled Candidate activated before regression")

    target_task = f"withheld-{candidate_id}"
    target_domain = "verified-macro-library:sequence-to-string"
    baseline: list[dict[str, Any]] = []
    trial: list[dict[str, Any]] = []
    for repeat_index in range(3):
        artifact = {
            "candidate_id": candidate_id,
            "repeat_index": repeat_index,
            "library_synthesis_digest": synthesized["digest"],
            "selected_candidate_ids": synthesized["selected_candidate_ids"],
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
            "invariant": "discovered source library Candidate regressions remain unchanged",
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
        raise VerifiedMacroLibraryError("library-compiled Candidate ignored protected regression")
    promoted = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=scope,
        baseline_path=baseline_path,
        candidate_path=valid_path,
        target_task_ids=[target_task],
    )
    if not promoted["decision"]["adopt_candidate"]:
        raise VerifiedMacroLibraryError("library-compiled Candidate failed valid target improvement")

    compiled_trials_before = _candidate_trial_snapshot(root, candidate_id)
    all_source_trials_before_replay = {
        source_id: _candidate_trial_snapshot(root, source_id)
        for source_id in synthesized["library_candidate_ids"]
    }
    if not compiled_trials_before or not all(all_source_trials_before_replay.values()):
        raise VerifiedMacroLibraryError("library replay lacks durable source or compiled trial evidence")

    commitment = _digest(
        {
            "seed": seed,
            "candidate_id": candidate_id,
            "library_synthesis_digest": synthesized["digest"],
            "phase": "post-promotion-library-discovery-challenge-v1",
        }
    )
    magnitude = 10 + int(commitment[:2], 16) % 20
    challenge_inputs = (
        [magnitude, -3, 1],
        [-magnitude, 4, 2],
        [7, -2, 5, -1],
    )
    challenge_tuples = {tuple(value) for value in challenge_inputs}
    if challenge_tuples & (planning_inputs | component_support):
        raise VerifiedMacroLibraryError("library challenge overlaps planning or component support")

    selected_items = []
    for selected_id in synthesized["selected_candidate_ids"]:
        path = root / ".continual" / "candidates" / selected_id / "candidate.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        spec = AcquiredProgramToolSpec.from_candidate(raw)
        selected_items.append(
            {
                "candidate_id": spec.candidate_id,
                "tool_id": spec.tool_id,
                "scope": spec.scope,
                "input_domain": str(spec.program["input_domain"]),
                "output_domain": str(spec.program["output_domain"]),
                "program": spec.program,
            }
        )

    source_run_ids: list[str] = []
    compiled_run_ids: list[str] = []
    outputs: list[dict[str, Any]] = []
    for repeat_index in range(3):
        for case_index, value in enumerate(challenge_inputs):
            expected = str(sum(value))
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
                raise VerifiedMacroLibraryError("library-discovered compiled route diverged on challenge")
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

    all_source_trials_after_replay = {
        source_id: _candidate_trial_snapshot(root, source_id)
        for source_id in synthesized["library_candidate_ids"]
    }
    compiled_trials_after = _candidate_trial_snapshot(root, candidate_id)
    all_source_trials_unchanged = all_source_trials_after_replay == all_source_trials_before_replay
    compiled_trials_unchanged = compiled_trials_after == compiled_trials_before
    generated_trials_after = {
        generated_id: _candidate_trial_snapshot(root, generated_id)
        for generated_id in generated_ids
    }
    if generated_trials_after != generated_trials_before:
        raise VerifiedMacroLibraryError("library synthesis changed generated component trial evidence")
    if not all_source_trials_unchanged or not compiled_trials_unchanged:
        raise VerifiedMacroLibraryError("library challenge replay changed Candidate trial evidence")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "verified-macro-library-discovery",
        "library_candidate_count": synthesized["library_candidate_count"],
        "candidate_ids_supplied_by_caller": synthesized["candidate_ids_supplied_by_caller"],
        "generated_component_candidate_ids": generated_ids,
        "shorter_direct_distractor_candidate_id": distractor["candidate_id"],
        "typed_chain_count": synthesized["typed_chain_count"],
        "shortest_type_chain_depth": synthesized["shortest_type_chain_depth"],
        "selected_chain_depth": synthesized["selected_chain_depth"],
        "selected_candidate_ids": synthesized["selected_candidate_ids"],
        "selected_sum_candidate_id": sequence_sum["candidate_id"],
        "selected_to_string_candidate_id": numeric_to_string["candidate_id"],
        "compiled_candidate_id": candidate_id,
        "compiled_tool_id": tool_id,
        "compiled_scope": scope,
        "compiled_program_nodes": int(meta["nodes"]),
        "compiled_program_depth": int(meta["depth"]),
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
        "generated_component_trial_state_unchanged": generated_trials_after
        == generated_trials_before,
        "all_discovered_source_trial_state_unchanged": all_source_trials_unchanged,
        "compiled_candidate_trial_state_unchanged": compiled_trials_unchanged,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded verified-macro-library discovery evidence only. The synthesizer discovered "
            "exact-scope verified acquired programs from durable Candidate state without caller-supplied "
            "Candidate IDs, behaviorally rejected a shorter type-correct wrong route, compiled a correct "
            "multi-stage route, independently regression-gated the new Candidate, and replayed it across "
            "fresh mechanical Engines with source and compiled trial ledgers unchanged. The library, "
            "tasks, evaluator, search bounds, and challenges remain repository-authored, so this is not "
            "AGI or independent production evidence."
        ),
    }
    if not report["all_outputs_equal"]:
        raise VerifiedMacroLibraryError("library-discovery aggregate equivalence failed")
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "verified-macro-library-discovery"
        / f"library-discovery-{_digest(seed)[:16]}.json",
        report,
    )
    return report
