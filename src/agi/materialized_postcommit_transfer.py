from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from agi.acquired_programs import execute_program
from agi.materialized_retention_matrix import _active_acquired_candidates
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
)
from continual.learned_tools import LearnedToolError


def _challenge_inputs(candidate_id: str, domain: str) -> tuple[Any, ...]:
    digest = hashlib.sha256(f"post-materialization-transfer-v1\0{candidate_id}".encode()).digest()
    if domain == "string":
        return tuple(f"postcommit-{candidate_id[-8:]}-{digest[index]:02x}" for index in range(3))
    if domain == "sequence":
        return tuple(
            [int(digest[index * 4 + offset]) - 128 for offset in range(4)]
            for index in range(3)
        )
    if domain == "numeric":
        return tuple(int(digest[index]) - 128 for index in range(3))
    if domain == "object":
        return tuple(
            {"alpha": int(digest[index]), "beta": int(digest[index + 3]) - 128}
            for index in range(3)
        )
    raise RuntimeError(f"unsupported post-materialization challenge domain: {domain}")


def _runtime_output(response: Mapping[str, Any], candidate_id: str) -> Any:
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise RuntimeError("post-materialization transfer returned malformed runtime output")
    if result.get("candidate_id") != candidate_id:
        raise RuntimeError("post-materialization transfer used the wrong Candidate")
    if result.get("execution_kind") != "verified_learned_tool" or result.get("program_kind") != "acquired_program":
        raise RuntimeError("post-materialization transfer bypassed verified acquired-program execution")
    return result.get("output")


def run_materialized_postcommit_transfer(root: Path, *, minimum_candidates: int = 3) -> dict[str, Any]:
    """Measure retained capability lift on challenge inputs created after materialization.

    This remains an internal oracle-based test, but the challenge family is distinct from the
    acquisition campaigns. Each committed Candidate is compared with a no-Candidate baseline, then
    exercised in its own durable runtime run. Candidate regression-trial files must remain unchanged,
    and the earliest capability is retested after all later capabilities to detect forgetting.
    """

    root = root.resolve()
    candidates = _active_acquired_candidates(root)
    if len(candidates) < minimum_candidates:
        raise RuntimeError(
            f"post-materialization transfer requires at least {minimum_candidates} active acquired Candidates"
        )

    candidate_ids = [str(candidate["candidate_id"]) for candidate, _ in candidates]
    trial_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id) for candidate_id in candidate_ids
    }
    results: list[dict[str, Any]] = []

    for candidate, acquired in candidates:
        candidate_id = str(candidate["candidate_id"])
        scope = str(acquired["scope"])
        tool_id = str(acquired["tool_id"])
        program = acquired.get("program")
        if not isinstance(program, Mapping):
            raise RuntimeError(f"Candidate {candidate_id} is missing its acquired program")
        domain = str(program.get("input_domain", ""))
        inputs = _challenge_inputs(candidate_id, domain)
        expected = tuple(execute_program(program, value) for value in inputs)

        baseline_engine = _mechanical_engine(root.parent / f".empty-{candidate_id}")
        baseline_run = _new_runtime_run(baseline_engine)
        baseline_successes = 0
        for value in inputs:
            try:
                _invoke(baseline_engine, baseline_run, scope=scope, tool_id=tool_id, value=value)
            except LearnedToolError:
                continue
            baseline_successes += 1
        if baseline_successes:
            raise RuntimeError(f"Candidate {candidate_id} baseline unexpectedly exposed the learned tool")

        engine = _mechanical_engine(root)
        run_id = _new_runtime_run(engine)
        successes = 0
        for value, answer_expected in zip(inputs, expected, strict=True):
            response = _invoke(engine, run_id, scope=scope, tool_id=tool_id, value=value)
            if _runtime_output(response, candidate_id) == answer_expected:
                successes += 1
        run_dir = engine.store.run_dir(run_id)
        score = successes / len(inputs)
        if score != 1.0 or not run_dir.is_dir() or not (run_dir / "snapshot.json").is_file():
            raise RuntimeError(f"Candidate {candidate_id} failed its durable post-materialization challenge")
        results.append(
            {
                "candidate_id": candidate_id,
                "scope": scope,
                "tool_id": tool_id,
                "input_domain": domain,
                "challenge_count": len(inputs),
                "baseline_score": 0.0,
                "candidate_score": score,
                "durable_run_id": run_id,
                "durable_run_persisted": True,
            }
        )

    first_candidate, first_acquired = candidates[0]
    first_id = str(first_candidate["candidate_id"])
    first_program = first_acquired["program"]
    first_domain = str(first_program.get("input_domain", ""))
    first_probe = _challenge_inputs(first_id, first_domain)[0]
    first_expected = execute_program(first_program, first_probe)
    retest_engine = _mechanical_engine(root)
    retest_run = _new_runtime_run(retest_engine)
    retest_response = _invoke(
        retest_engine,
        retest_run,
        scope=str(first_acquired["scope"]),
        tool_id=str(first_acquired["tool_id"]),
        value=first_probe,
    )
    earliest_retained = _runtime_output(retest_response, first_id) == first_expected
    if not earliest_retained:
        raise RuntimeError("earliest materialized capability was forgotten after later challenges")

    trial_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id) for candidate_id in candidate_ids
    }
    if trial_after != trial_before:
        raise RuntimeError("post-materialization runtime challenges changed Candidate trial ledgers")

    return {
        "schema_version": 1,
        "passed": True,
        "challenge_family": "post-materialization-transfer-v1",
        "candidate_count": len(candidates),
        "results": results,
        "all_baseline_scores": [item["baseline_score"] for item in results],
        "all_candidate_scores": [item["candidate_score"] for item in results],
        "earliest_candidate_retained_after_later_challenges": earliest_retained,
        "candidate_trial_state_unchanged": True,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal post-materialization transfer evidence only. Challenges and semantic expectations "
            "are repository-internal and therefore do not count as independent production AGI evidence."
        ),
    }
