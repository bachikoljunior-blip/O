from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from continual.candidate_regression import candidate_verified_for_scope
from continual.learning_engine import LearningEnabledEngine

from .acquired_program_runtime import _digest, _new_runtime_run
from .acquired_programs import execute_program
from .generated_crossdomain_cegis import (
    GENERATED_CROSSDOMAIN_SCOPE_PREFIX,
    generated_crossdomain_target,
)


def _runtime_call(
    engine: LearningEnabledEngine,
    run_id: str,
    *,
    scope: str,
    tool_id: str,
    value: Any,
) -> Any:
    output = engine._invoke(
        run_id,
        "execute",
        {
            "snapshot": {"revision": 0},
            "execution_unit": {
                "goal": "re-verify an already promoted generated cross-domain capability",
                "scope": scope,
                "learned_tool_call": {"tool_id": tool_id, "input": value},
            },
        },
    )
    return output["result"]["output"]


def verify_generated_crossdomain_replay(
    root: Path,
    seed: str,
    *,
    engine_factory: Callable[[Path], LearningEnabledEngine] = LearningEnabledEngine,
) -> dict[str, Any]:
    """Re-verify a persisted generated campaign without rerunning acquisition or consuming trials.

    The evaluator deterministically reconstructs its committed target and sealed final inputs from the
    ephemeral seed, verifies that the exact persisted Candidate still has deterministic scope
    authority, replays only mechanical runtime calls in fresh Engine instances, and asserts that both
    Candidate state and the persistent trial ledger are byte-for-byte stable across verification.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be a non-empty string")
    root = root.resolve()
    target, metadata, _, diagnostic_inputs, final_inputs = generated_crossdomain_target(seed)
    diagnostic_expected = tuple(execute_program(target, value) for value in diagnostic_inputs)
    final_expected = tuple(execute_program(target, value) for value in final_inputs)
    commitment_payload = {
        "generator_version": metadata["generator_version"],
        "seed_sha256": _digest(seed),
        "target": target,
        "diagnostic": [
            {"input": query, "expected": expected}
            for query, expected in zip(diagnostic_inputs, diagnostic_expected, strict=True)
        ],
        "final": [
            {"input": query, "expected": expected}
            for query, expected in zip(final_inputs, final_expected, strict=True)
        ],
    }
    commitment = _digest(commitment_payload)
    token = commitment[:16]
    campaign_id = f"generated-crossdomain-{token}"
    candidate_id = f"generated-crossdomain-{token}"
    tool_id = f"generated-crossdomain-tool-{token}"
    scope = f"{GENERATED_CROSSDOMAIN_SCOPE_PREFIX}/{token}"

    candidate_path = root / ".continual" / "candidates" / candidate_id / "candidate.json"
    if not candidate_path.is_file():
        raise FileNotFoundError(f"persisted generated Candidate is missing: {candidate_id}")
    candidate_bytes_before = candidate_path.read_bytes()
    candidate = json.loads(candidate_bytes_before.decode("utf-8"))
    if not candidate_verified_for_scope(root, candidate, scope):
        raise RuntimeError("persisted generated Candidate is not deterministically verified for scope")

    source_evidence_path = (
        root
        / ".continual"
        / "evidence"
        / "generated-crossdomain-cegis"
        / f"{campaign_id}.json"
    )
    if not source_evidence_path.is_file():
        raise FileNotFoundError(f"source generated campaign evidence is missing: {campaign_id}")
    source_evidence = json.loads(source_evidence_path.read_text(encoding="utf-8"))
    source_digest = _digest(
        {key: value for key, value in source_evidence.items() if key != "digest"}
    )
    if (
        source_evidence.get("digest") != source_digest
        or source_evidence.get("commitment") != commitment
        or source_evidence.get("candidate_id") != candidate_id
        or source_evidence.get("tool_id") != tool_id
        or source_evidence.get("scope") != scope
        or source_evidence.get("passed") is not True
    ):
        raise RuntimeError("persisted generated campaign evidence failed identity verification")

    trial_dir = candidate_path.parent / "trials"
    trial_files = sorted(trial_dir.glob("*.json"))
    if len(trial_files) != 1:
        raise RuntimeError("generated Candidate must have exactly one scope trial ledger")
    ledger_bytes_before = trial_files[0].read_bytes()
    ledger = json.loads(ledger_bytes_before.decode("utf-8"))
    attempts = ledger.get("attempts")
    if not isinstance(attempts, list) or len(attempts) != 2:
        raise RuntimeError("generated Candidate must retain exactly adverse and valid trial attempts")
    if any(item.get("state") != "COMPLETED" for item in attempts if isinstance(item, dict)):
        raise RuntimeError("generated Candidate has an incomplete persistent trial")

    replay_passes: list[dict[str, Any]] = []
    for replay_index in range(2):
        engine = engine_factory(root)
        run_id = _new_runtime_run(engine)
        results = []
        for query, expected in zip(final_inputs, final_expected, strict=True):
            answer = _runtime_call(
                engine,
                run_id,
                scope=scope,
                tool_id=tool_id,
                value=query,
            )
            results.append(
                {
                    "query_sha256": _digest(query),
                    "answer_sha256": _digest(answer),
                    "success": answer == expected,
                }
            )
        replay_passes.append(
            {
                "replay_index": replay_index,
                "score": sum(1.0 if item["success"] else 0.0 for item in results)
                / len(results),
                "result_digests": [_digest(item) for item in results],
            }
        )

    candidate_unchanged = candidate_path.read_bytes() == candidate_bytes_before
    ledger_unchanged = trial_files[0].read_bytes() == ledger_bytes_before
    report = {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "candidate_id": candidate_id,
        "tool_id": tool_id,
        "scope": scope,
        "seed_sha256": _digest(seed),
        "commitment": commitment,
        "source_evidence_digest": source_digest,
        "candidate_verified_for_scope": True,
        "trial_attempt_count_before": len(attempts),
        "trial_attempt_count_after": len(json.loads(trial_files[0].read_text(encoding="utf-8"))["attempts"]),
        "candidate_state_unchanged": candidate_unchanged,
        "trial_ledger_unchanged": ledger_unchanged,
        "replay_passes": replay_passes,
        "replay_consumed_trial_budget": not ledger_unchanged,
        "seed_value_persisted": False,
        "claim_boundary": (
            "This verifies restart-safe replay of an internally generated cross-domain capability. "
            "It does not create independent production AGI evidence."
        ),
    }
    report["passed"] = bool(
        report["candidate_verified_for_scope"]
        and report["trial_attempt_count_before"] == 2
        and report["trial_attempt_count_after"] == 2
        and report["candidate_state_unchanged"]
        and report["trial_ledger_unchanged"]
        and not report["replay_consumed_trial_budget"]
        and all(item["score"] == 1.0 for item in replay_passes)
    )
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    replay_path = (
        root
        / ".continual"
        / "evidence"
        / "generated-crossdomain-replay"
        / f"{campaign_id}.json"
    )
    replay_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = replay_path.with_suffix(replay_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(replay_path)
    return report
