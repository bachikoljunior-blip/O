from __future__ import annotations

from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import AcquiredProgramError, ProgramExample, synthesize_program
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
    _validate_runtime_output,
)


def _sequence_spec() -> dict[str, Any]:
    return {
        "name": "sequence-duplicate",
        "input_domain": "sequence",
        "output_domain": "sequence",
        "examples": (
            ProgramExample([1, 2], [1, 2, 1, 2]),
            ProgramExample(["a"], ["a", "a"]),
            ProgramExample([True, 0], [True, 0, True, 0]),
        ),
        "probe": [7, "probe"],
        "expected": [7, "probe", 7, "probe"],
        "max_nodes": 3,
    }


def run_sequence_construction_transfer(root: Path, seed: str) -> dict[str, Any]:
    """Promote and transfer a bounded generic sequence-construction program.

    The task requires building a new finite sequence by concatenating the input sequence with itself.
    A two-node search must fail; the three-node bounded typed grammar must discover the generic
    ``concat_sequence(input, input)`` program. The Candidate then passes the normal repeated target
    improvement/protected-retention gate before post-promotion challenge cases are generated and
    replayed across fresh Engines.

    This is internal capability evidence. The demonstrations, challenge generator, scorer, and runtime
    are repository-authored and credential-free, so it is not independent production evidence or AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    spec = _sequence_spec()

    try:
        synthesize_program(
            input_domain="sequence",
            output_domain="sequence",
            examples=spec["examples"],
            max_nodes=2,
        )
    except AcquiredProgramError:
        insufficient_search_failed = True
    else:
        raise RuntimeError("sequence construction unexpectedly fit inside two program nodes")

    learned = synthesize_program(
        input_domain="sequence",
        output_domain="sequence",
        examples=spec["examples"],
        max_nodes=3,
    )
    expected_expression = {
        "op": "concat_sequence",
        "left": {"op": "input"},
        "right": {"op": "input"},
    }
    if learned.expression != expected_expression:
        raise RuntimeError("sequence construction learned an unexpected bounded program")

    promoted = _promote_task(
        root,
        seed=f"{seed}:sequence-construction",
        spec=spec,
    )
    candidate_id = str(promoted["candidate_id"])
    trial_before = _candidate_trial_snapshot(root, candidate_id)
    if not trial_before:
        raise RuntimeError("sequence construction Candidate lacks regression trial evidence")

    commitment = _digest(
        {
            "seed": seed,
            "candidate_id": candidate_id,
            "phase": "post-promotion-sequence-construction-challenge-v1",
        }
    )
    a = 10 + int(commitment[:2], 16) % 50
    b = -(10 + int(commitment[2:4], 16) % 50)
    challenge_inputs: tuple[list[Any], ...] = (
        [a, b, 3],
        ["novel", a],
        [False, b, "mixed"],
    )
    support_signatures = {_digest(item.input) for item in spec["examples"]}
    if any(_digest(value) in support_signatures for value in challenge_inputs):
        raise RuntimeError("sequence construction challenge overlaps synthesis support")

    expected_outputs = [list(value) + list(value) for value in challenge_inputs]
    fresh_engine_runs: list[str] = []
    outputs: list[list[Any]] = []
    invocation_refs: list[dict[str, Any]] = []
    for engine_index in range(3):
        for case_index, (value, expected) in enumerate(
            zip(challenge_inputs, expected_outputs, strict=True)
        ):
            engine = _mechanical_engine(root)
            run_id = _new_runtime_run(engine)
            response = _invoke(
                engine,
                run_id,
                scope=str(promoted["scope"]),
                tool_id=str(promoted["tool_id"]),
                value=value,
            )
            result = _validate_runtime_output(
                response,
                expected=expected,
                candidate_id=candidate_id,
            )
            output = result["output"]
            if not isinstance(output, list):
                raise RuntimeError("sequence construction runtime did not return a sequence")
            fresh_engine_runs.append(run_id)
            outputs.append(output)
            invocation_refs.append(
                {
                    "engine_index": engine_index,
                    "case_index": case_index,
                    "run_id": run_id,
                    "input_sha256": _digest(value),
                    "output_sha256": _digest(output),
                }
            )

    trial_after = _candidate_trial_snapshot(root, candidate_id)
    trial_state_unchanged = trial_after == trial_before
    if not trial_state_unchanged:
        raise RuntimeError("sequence construction replay changed Candidate trial state")

    expected_repeated = expected_outputs * 3
    all_outputs_matched = outputs == expected_repeated
    if not all_outputs_matched:
        raise RuntimeError("sequence construction failed post-promotion transfer")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "bounded-sequence-construction-transfer",
        "candidate_id": candidate_id,
        "scope": promoted["scope"],
        "expression": learned.expression,
        "insufficient_two_node_search_failed": insufficient_search_failed,
        "negative_evidence_retained": bool(promoted["negative_evidence_retained"]),
        "challenge_generated_after_promotion": True,
        "challenge_commitment": commitment,
        "challenge_inputs_overlap_synthesis_support": False,
        "challenge_case_count": len(challenge_inputs),
        "fresh_engine_run_count": len(fresh_engine_runs),
        "fresh_engine_runs": fresh_engine_runs,
        "invocation_count": len(invocation_refs),
        "all_outputs_matched": all_outputs_matched,
        "candidate_trial_state_unchanged": trial_state_unchanged,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded sequence-construction evidence only. A generic typed sequence program "
            "was synthesized, regression-promoted, and transferred on post-promotion challenges across "
            "fresh mechanical Engines with unchanged Candidate trial evidence. Demonstrations, scoring, "
            "challenge generation, and runtime remain repository-authored and credential-free, so this "
            "does not establish open-world learning, AGI, or the strict independent production gate."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "sequence-construction-transfer"
        / f"sequence-construction-{_digest(seed)[:16]}.json",
        report,
    )
    return report
