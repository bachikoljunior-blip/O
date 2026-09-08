from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from agi.acquired_program_runtime import _atomic_json
from agi.heterogeneous_retention_campaign import (
    _digest,
    _task_specs,
    run_heterogeneous_retention_campaign,
)
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
    _validate_runtime_output,
)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _candidate_runtime(root: Path, candidate_id: str) -> dict[str, Any]:
    path = root / ".continual" / "candidates" / candidate_id / "candidate.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise RuntimeError("heterogeneous Candidate payload is malformed")
    acquired = raw.get("acquired_program")
    if not isinstance(acquired, Mapping):
        raise RuntimeError("heterogeneous Candidate lacks acquired-program payload")
    program = acquired.get("program")
    if not isinstance(program, Mapping):
        raise RuntimeError("heterogeneous Candidate lacks acquired-program descriptor")
    return {
        "candidate_id": candidate_id,
        "scope": str(acquired.get("scope", "")),
        "tool_id": str(acquired.get("tool_id", "")),
        "program": dict(program),
    }


def _challenge_cases(name: str, digest: str) -> tuple[tuple[Any, Any], ...]:
    numbers = [int(digest[index : index + 2], 16) for index in range(0, 12, 2)]
    if name == "numeric-abs":
        first = -(100 + numbers[0])
        second = 100 + numbers[1]
        return ((first, abs(first)), (second, abs(second)))
    if name == "string-upper":
        first = f"post-{digest[12:20]}-alpha"
        second = f"post-{digest[20:28]}-mixedCase"
        return ((first, first.upper()), (second, second.upper()))
    if name == "sequence-sum":
        first = [30 + numbers[2], 40 + numbers[3], -(10 + numbers[4])]
        second = [50 + numbers[5], -17, 23]
        return ((first, sum(first)), (second, sum(second)))
    if name == "object-kind":
        first = {
            "kind": "target",
            "noise": 1000 + numbers[0],
            "post_promotion_nonce": digest[28:44],
        }
        second = {
            "kind": f"novel-{digest[44:52]}",
            "noise": 2000 + numbers[1],
            "post_promotion_nonce": digest[52:60],
        }
        return ((first, True), (second, False))
    raise RuntimeError(f"unknown heterogeneous challenge task: {name}")


def run_heterogeneous_postpromotion_transfer(root: Path, seed: str) -> dict[str, Any]:
    """Derive fresh cross-domain probes only after promotion and replay them on fresh Engines.

    The underlying heterogeneous continual campaign first promotes numeric, string, sequence, and
    object Candidates and freezes their regression evidence. Only after that campaign returns do we
    derive a new challenge family from its final report digest. Challenge inputs are required not to
    overlap any synthesis demonstration input. Three newly reconstructed mechanical Engines must then
    reproduce all eight post-promotion cases without changing one byte of any Candidate trial ledger.

    This strengthens temporal separation and delayed-retention evidence. The challenge generator and
    scorer are still repository-authored and the replay is credential-free, so the result is internal
    development evidence rather than independent production AGI evidence.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    promotion_report = run_heterogeneous_retention_campaign(root, seed)
    if not promotion_report.get("passed"):
        raise RuntimeError("heterogeneous promotion campaign did not pass")
    promotion_digest = str(promotion_report.get("digest", ""))
    if len(promotion_digest) != 64:
        raise RuntimeError("heterogeneous promotion report lacks a canonical digest")

    specs = _task_specs()
    candidate_ids = [str(value) for value in promotion_report["candidate_ids"]]
    scopes = [str(value) for value in promotion_report["scopes"]]
    if len(specs) != len(candidate_ids) or len(scopes) != len(candidate_ids):
        raise RuntimeError("heterogeneous promotion report has inconsistent capability count")

    prepared: list[dict[str, Any]] = []
    all_support_inputs: set[str] = set()
    for spec in specs:
        all_support_inputs.update(_canonical(item.input) for item in spec["examples"])

    for index, (spec, candidate_id, scope) in enumerate(zip(specs, candidate_ids, scopes, strict=True)):
        runtime = _candidate_runtime(root, candidate_id)
        if runtime["scope"] != scope:
            raise RuntimeError("heterogeneous post-promotion scope changed after promotion")
        task_digest = _digest(
            {
                "promotion_digest": promotion_digest,
                "task": spec["name"],
                "index": index,
                "phase": "post-promotion-challenge-v1",
            }
        )
        cases = _challenge_cases(str(spec["name"]), task_digest)
        for probe, expected in cases:
            if _canonical(probe) in all_support_inputs:
                raise RuntimeError("post-promotion challenge overlaps a synthesis demonstration")
        prepared.append(
            {
                **runtime,
                "name": spec["name"],
                "challenge_digest": task_digest,
                "cases": cases,
            }
        )

    trial_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in candidate_ids
    }
    if not all(trial_before.values()):
        raise RuntimeError("post-promotion challenge lacks frozen Candidate trial evidence")

    fresh_engine_runs: list[str] = []
    replay_refs: list[dict[str, Any]] = []
    for engine_index in range(3):
        engine = _mechanical_engine(root)
        run_id = _new_runtime_run(engine)
        fresh_engine_runs.append(run_id)
        for item in prepared:
            for case_index, (probe, expected) in enumerate(item["cases"]):
                response = _invoke(
                    engine,
                    run_id,
                    scope=str(item["scope"]),
                    tool_id=str(item["tool_id"]),
                    value=probe,
                )
                result = _validate_runtime_output(
                    response,
                    expected=expected,
                    candidate_id=str(item["candidate_id"]),
                )
                replay_refs.append(
                    {
                        "engine_index": engine_index,
                        "run_id": run_id,
                        "candidate_id": item["candidate_id"],
                        "task": item["name"],
                        "case_index": case_index,
                        "probe_sha256": _digest(probe),
                        "output_sha256": _digest(expected),
                        "program_kind": result.get("program_kind"),
                    }
                )

    trial_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in candidate_ids
    }
    trial_state_unchanged = trial_after == trial_before
    if not trial_state_unchanged:
        changed = sorted(
            candidate_id
            for candidate_id in candidate_ids
            if trial_before[candidate_id] != trial_after[candidate_id]
        )
        raise RuntimeError(
            "post-promotion heterogeneous replay changed Candidate trials: " + ",".join(changed)
        )

    challenge_input_hashes = [
        _digest(probe)
        for item in prepared
        for probe, _expected in item["cases"]
    ]
    if len(set(challenge_input_hashes)) != len(challenge_input_hashes):
        raise RuntimeError("post-promotion heterogeneous challenge produced duplicate probes")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "heterogeneous-postpromotion-transfer",
        "promotion_report_digest": promotion_digest,
        "challenge_generated_after_promotion": True,
        "challenge_generator_version": "post-promotion-challenge-v1",
        "challenge_inputs_overlap_synthesis_support": False,
        "capability_count": len(prepared),
        "challenge_case_count": sum(len(item["cases"]) for item in prepared),
        "fresh_engine_count": len(fresh_engine_runs),
        "fresh_engine_runs": fresh_engine_runs,
        "replay_invocation_count": len(replay_refs),
        "candidate_ids": candidate_ids,
        "input_domains": list(promotion_report["input_domains"]),
        "candidate_trial_state_unchanged": trial_state_unchanged,
        "all_fresh_engine_replays_matched": True,
        "live_model_invocation_required": False,
        "challenge_refs": [
            {
                "candidate_id": item["candidate_id"],
                "task": item["name"],
                "challenge_digest": item["challenge_digest"],
                "probe_sha256": [_digest(probe) for probe, _expected in item["cases"]],
                "expected_sha256": [_digest(expected) for _probe, expected in item["cases"]],
            }
            for item in prepared
        ],
        "claim_boundary": (
            "Internal delayed-retention and temporal-separation evidence only. The eight challenge "
            "cases are derived after Candidate promotion and do not overlap synthesis demonstrations, "
            "and three fresh Engines replay all four typed capabilities with unchanged Candidate trial "
            "ledgers. The challenge generator/scorer remain repository-authored and no live production "
            "model or independent evaluator is used, so this does not establish AGI or satisfy the "
            "strict external production evidence gate."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "heterogeneous-postpromotion-transfer"
        / f"heterogeneous-postpromotion-{_digest(seed)[:16]}.json",
        report,
    )
    return report
