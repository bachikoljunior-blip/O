from __future__ import annotations

from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
    _validate_runtime_output,
)


def _chain_specs() -> tuple[dict[str, Any], ...]:
    return (
        {
            "name": "chain-abs",
            "input_domain": "numeric",
            "output_domain": "numeric",
            "examples": (
                ProgramExample(-3, 3),
                ProgramExample(0, 0),
                ProgramExample(5, 5),
            ),
            "probe": -11,
            "expected": 11,
            "max_nodes": 3,
        },
        {
            "name": "chain-object",
            "input_domain": "numeric",
            "output_domain": "object",
            "examples": (
                ProgramExample(2, {"score": 2}),
                ProgramExample(-3, {"score": -3}),
                ProgramExample(7, {"score": 7}),
            ),
            "probe": 11,
            "expected": {"score": 11},
            "max_nodes": 3,
        },
        {
            "name": "chain-threshold",
            "input_domain": "object",
            "output_domain": "boolean",
            "examples": (
                ProgramExample({"score": -2}, False),
                ProgramExample({"score": 4}, False),
                ProgramExample({"score": 5}, True),
                ProgramExample({"score": 12}, True),
            ),
            "probe": {"score": 11},
            "expected": True,
            "max_nodes": 6,
        },
    )


def _run_chain(
    root: Path,
    run_id: str,
    promoted: tuple[dict[str, Any], ...],
    value: int,
) -> tuple[bool, list[dict[str, Any]]]:
    engine = _mechanical_engine(root)
    # _new_runtime_run belongs to the engine whose invocation journal will be used.
    actual_run_id = _new_runtime_run(engine)
    if run_id and run_id != actual_run_id:
        # The caller supplies an empty string in normal use; keep the parameter explicit so a future
        # persisted runner cannot accidentally reuse a run from another Engine.
        raise RuntimeError("learned-tool chain attempted to reuse a run across fresh Engines")

    current: Any = value
    refs: list[dict[str, Any]] = []
    expected_values: tuple[Any, ...] = (
        abs(value),
        {"score": abs(value)},
        abs(value) >= 5,
    )
    for index, (item, expected) in enumerate(zip(promoted, expected_values, strict=True)):
        response = _invoke(
            engine,
            actual_run_id,
            scope=str(item["scope"]),
            tool_id=str(item["tool_id"]),
            value=current,
        )
        result = _validate_runtime_output(
            response,
            expected=expected,
            candidate_id=str(item["candidate_id"]),
        )
        current = result["output"]
        refs.append(
            {
                "stage_index": index,
                "candidate_id": item["candidate_id"],
                "scope": item["scope"],
                "input_sha256": _digest(value if index == 0 else expected_values[index - 1]),
                "output_sha256": _digest(expected),
                "program_kind": result.get("program_kind"),
            }
        )
    if not isinstance(current, bool):
        raise RuntimeError("learned-tool chain did not terminate in a boolean result")
    return current, [{"run_id": actual_run_id, **item} for item in refs]


def run_learned_tool_chain_transfer(root: Path, seed: str) -> dict[str, Any]:
    """Compose three independently promoted learned tools on post-promotion unseen inputs.

    The campaign learns and regression-promotes separate numeric absolute-value, numeric-to-object,
    and object-threshold programs. Only after all three are active is a two-case challenge family
    derived. Three fresh Engines then execute the full three-stage chain. Candidate trial ledgers are
    frozen before chain execution and must remain byte-identical afterward.

    This is internal development evidence for longer typed composition. The chain is mechanically
    orchestrated, uses repository-authored demonstrations/scoring, and does not substitute for a live
    model autonomously discovering open-world tool composition or for independent production AGI
    evaluation.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    promoted = tuple(
        _promote_task(root, seed=f"{seed}:learned-chain", spec=spec)
        for spec in _chain_specs()
    )
    candidate_ids = [str(item["candidate_id"]) for item in promoted]
    if len(set(candidate_ids)) != 3:
        raise RuntimeError("learned-tool chain reused a Candidate identity")

    trial_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in candidate_ids
    }
    if not all(trial_before.values()):
        raise RuntimeError("learned-tool chain Candidate lacks regression trial evidence")

    commitment = _digest(
        {
            "seed": seed,
            "candidate_ids": candidate_ids,
            "phase": "post-promotion-chain-challenge-v1",
        }
    )
    false_case = -(1 + int(commitment[:2], 16) % 3)
    true_case = -(20 + int(commitment[2:4], 16) % 40)
    challenge_inputs = (false_case, true_case)
    support_numeric_inputs = {-3, 0, 5, 2, 7}
    if any(value in support_numeric_inputs for value in challenge_inputs):
        raise RuntimeError("learned-tool chain challenge overlaps numeric synthesis support")

    fresh_engine_runs: list[str] = []
    chain_refs: list[dict[str, Any]] = []
    outputs: list[bool] = []
    for engine_index in range(3):
        for case_index, value in enumerate(challenge_inputs):
            output, refs = _run_chain(root, "", promoted, value)
            run_ids = {str(item["run_id"]) for item in refs}
            if len(run_ids) != 1:
                raise RuntimeError("one learned-tool chain case crossed Engine runs")
            run_id = next(iter(run_ids))
            fresh_engine_runs.append(run_id)
            outputs.append(output)
            chain_refs.extend(
                {
                    "engine_index": engine_index,
                    "case_index": case_index,
                    "challenge_input_sha256": _digest(value),
                    **item,
                }
                for item in refs
            )

    expected_outputs = [abs(value) >= 5 for _engine in range(3) for value in challenge_inputs]
    if outputs != expected_outputs:
        raise RuntimeError("learned-tool chain did not transfer on post-promotion challenge cases")

    trial_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in candidate_ids
    }
    trial_state_unchanged = trial_after == trial_before
    if not trial_state_unchanged:
        raise RuntimeError("learned-tool chain execution changed Candidate trial state")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "three-stage-learned-tool-chain-transfer",
        "candidate_ids": candidate_ids,
        "scopes": [str(item["scope"]) for item in promoted],
        "expressions": [item["expression"] for item in promoted],
        "chain_domains": [
            "numeric->numeric",
            "numeric->object",
            "object->boolean",
        ],
        "challenge_generated_after_promotion": True,
        "challenge_commitment": commitment,
        "challenge_inputs_overlap_synthesis_support": False,
        "challenge_case_count": len(challenge_inputs),
        "fresh_engine_chain_count": len(fresh_engine_runs),
        "fresh_engine_runs": fresh_engine_runs,
        "stage_invocation_count": len(chain_refs),
        "all_chain_outputs_matched": True,
        "all_negative_evidence_retained": all(
            bool(item["negative_evidence_retained"]) for item in promoted
        ),
        "candidate_trial_state_unchanged": trial_state_unchanged,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded three-stage learned-tool composition evidence only. Independently "
            "promoted numeric, object-construction, and object-predicate programs transfer as a chain "
            "on challenge inputs derived after promotion, across fresh mechanical Engines with "
            "unchanged Candidate trial ledgers. Orchestration and scoring remain repository-authored "
            "and credential-free, so this does not establish autonomous open-world composition, AGI, "
            "or the strict independent production evidence gate."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "learned-tool-chain-transfer"
        / f"learned-tool-chain-{_digest(seed)[:16]}.json",
        report,
    )
    return report
