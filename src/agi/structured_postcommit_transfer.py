from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

from agi.acquired_program_runtime import _atomic_json, _digest
from agi.acquired_programs import execute_program
from agi.external_tool_runtime_retention import _engine_with_adapter
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
)
from agi.structured_acquired_program_cegis import (
    STRUCTURED_CEGIS_SCOPE,
    run_structured_acquired_program_cegis,
)
from agi.structured_external_acquired_composition import (
    STRUCTURED_EXTERNAL_SCOPE,
    _promote_external_candidate,
    _structured_adapter,
)
from continual.acquired_program_tools import (
    AcquiredProgramToolError,
    AcquiredProgramToolSpec,
)
from continual.learned_tools import LearnedToolError


def _candidate_spec(root: Path, candidate_id: str) -> AcquiredProgramToolSpec:
    path = root / ".continual" / "candidates" / candidate_id / "candidate.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise RuntimeError("structured CEGIS Candidate payload is malformed")
    return AcquiredProgramToolSpec.from_candidate(raw)


def _postcommit_probes(seed: str, promoted_digest: str) -> tuple[int, int, int]:
    entropy = hashlib.sha256(
        f"structured-postcommit-transfer-v1\0{seed}\0{promoted_digest}".encode("utf-8")
    ).digest()
    return (
        -(1 + entropy[0] % 11),
        0,
        1 + entropy[1] % 11,
    )


def _runtime_output(
    response: Mapping[str, Any],
    *,
    candidate_id: str,
    scope: str,
    tool_id: str,
    program_kind: str,
) -> Any:
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise RuntimeError("postcommit structured transfer returned malformed runtime output")
    if result.get("candidate_id") != candidate_id:
        raise RuntimeError("postcommit structured transfer selected the wrong Candidate")
    if result.get("scope") != scope or result.get("tool_id") != tool_id:
        raise RuntimeError("postcommit structured transfer crossed an exact scope/tool boundary")
    if result.get("execution_kind") != "verified_learned_tool":
        raise RuntimeError("postcommit structured transfer bypassed verified learned-tool execution")
    if result.get("program_kind") != program_kind:
        raise RuntimeError("postcommit structured transfer used the wrong program kind")
    return result.get("output")


def _learner_input_hashes(root: Path, candidate_id: str) -> set[str]:
    history_path = (
        root
        / ".continual"
        / "candidates"
        / candidate_id
        / "learning-history"
        / "structured-cegis.json"
    )
    history = json.loads(history_path.read_text(encoding="utf-8"))
    if not isinstance(history, Mapping):
        raise RuntimeError("structured CEGIS learning history is malformed")
    values: list[Any] = []
    for key in ("initial_demonstrations", "counterexamples"):
        items = history.get(key)
        if not isinstance(items, list):
            raise RuntimeError(f"structured CEGIS learning history lacks {key}")
        for item in items:
            if not isinstance(item, Mapping) or "input" not in item:
                raise RuntimeError("structured CEGIS learning history contains malformed support")
            values.append(item["input"])
    return {_digest(value) for value in values}


def run_structured_postcommit_transfer(root: Path, seed: str) -> dict[str, Any]:
    """Test a refined object predicate on structured work created only after promotion.

    The object->boolean Candidate is first learned and promoted by the evaluator-committed CEGIS
    campaign. Only after that report has a fixed digest do we derive a new numeric challenge family,
    promote a distinct behavior-bound numeric->object host capability, and compose the two through
    fresh durable Engine runs. Challenge objects must not overlap any synthesis-support input.
    A no-Candidate runtime provides the before baseline. Runtime transfer must leave both Candidates'
    regression trial ledgers byte-identical.

    This is stronger internal temporal/transfer separation, not independent production evidence.
    The host adapter is still a trusted in-process callable and is not a hostile-code sandbox.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    refined = run_structured_acquired_program_cegis(
        root,
        f"postcommit-refined::{seed}",
    )
    if not refined.get("passed"):
        raise RuntimeError("structured CEGIS prerequisite did not pass")
    if refined.get("final_answers_exposed_to_learner") is not False:
        raise RuntimeError("structured CEGIS prerequisite leaked final answers")

    refined_candidate_id = str(refined["candidate_id"])
    refined_tool_id = str(refined["tool_id"])
    refined_spec = _candidate_spec(root, refined_candidate_id)
    if refined_spec.scope != STRUCTURED_CEGIS_SCOPE or refined_spec.tool_id != refined_tool_id:
        raise RuntimeError("structured CEGIS Candidate identity changed after promotion")

    refined_trials_before_external = _candidate_trial_snapshot(root, refined_candidate_id)
    if not refined_trials_before_external:
        raise RuntimeError("structured CEGIS Candidate lacks persisted regression trials")

    probes = _postcommit_probes(seed, str(refined["digest"]))
    external_seed = f"postcommit-external::{seed}::{refined['digest']}"
    external, adapter = _promote_external_candidate(root, seed=external_seed)
    external_trials_before_runtime = _candidate_trial_snapshot(root, external["candidate_id"])
    if not external_trials_before_runtime:
        raise RuntimeError("postcommit external Candidate lacks persisted regression trials")

    adapter_fn, adapter_sha256 = _structured_adapter(external_seed)
    if adapter_sha256 != external["adapter_sha256"]:
        raise RuntimeError("postcommit external adapter identity changed after promotion")

    challenge_objects = tuple(adapter_fn(probe) for probe in probes)
    learner_hashes = _learner_input_hashes(root, refined_candidate_id)
    challenge_hashes = {_digest(value) for value in challenge_objects}
    support_overlap = bool(learner_hashes & challenge_hashes)
    if support_overlap:
        raise RuntimeError("postcommit challenge object overlaps CEGIS synthesis support")

    expected = tuple(execute_program(refined_spec.program, value) for value in challenge_objects)

    baseline_successes = 0
    with tempfile.TemporaryDirectory(prefix="agi-structured-postcommit-baseline-") as temporary:
        baseline_engine = _mechanical_engine(Path(temporary))
        baseline_run = _new_runtime_run(baseline_engine)
        for value in challenge_objects:
            try:
                _invoke(
                    baseline_engine,
                    baseline_run,
                    scope=STRUCTURED_CEGIS_SCOPE,
                    tool_id=refined_tool_id,
                    value=value,
                )
            except (LearnedToolError, AcquiredProgramToolError):
                continue
            baseline_successes += 1
    if baseline_successes:
        raise RuntimeError("no-Candidate baseline unexpectedly exposed the refined structured tool")

    results: list[dict[str, Any]] = []
    for probe, expected_object, expected_boolean in zip(
        probes,
        challenge_objects,
        expected,
        strict=True,
    ):
        engine = _engine_with_adapter(
            root,
            tool_id=external["tool_id"],
            adapter=adapter,
        )
        run_id = _new_runtime_run(engine)
        external_response = _invoke(
            engine,
            run_id,
            scope=STRUCTURED_EXTERNAL_SCOPE,
            tool_id=external["tool_id"],
            value=probe,
        )
        structured = _runtime_output(
            external_response,
            candidate_id=external["candidate_id"],
            scope=STRUCTURED_EXTERNAL_SCOPE,
            tool_id=external["tool_id"],
            program_kind="contracted_external_tool",
        )
        learned_response = _invoke(
            engine,
            run_id,
            scope=STRUCTURED_CEGIS_SCOPE,
            tool_id=refined_tool_id,
            value=structured,
        )
        decision = _runtime_output(
            learned_response,
            candidate_id=refined_candidate_id,
            scope=STRUCTURED_CEGIS_SCOPE,
            tool_id=refined_tool_id,
            program_kind="acquired_program",
        )
        if structured != expected_object or decision is not expected_boolean:
            raise RuntimeError("postcommit structured composition disagreed with verified semantics")
        run_dir = engine.store.run_dir(run_id)
        if not run_dir.is_dir() or not (run_dir / "snapshot.json").is_file():
            raise RuntimeError("postcommit structured transfer did not persist its Engine run")
        results.append(
            {
                "run_id": run_id,
                "probe_sha256": _digest(probe),
                "object_sha256": _digest(structured),
                "decision": decision,
                "durable_run_persisted": True,
            }
        )

    if len({item["run_id"] for item in results}) != len(results):
        raise RuntimeError("postcommit structured transfer reused an Engine run")

    refined_trials_after = _candidate_trial_snapshot(root, refined_candidate_id)
    external_trials_after = _candidate_trial_snapshot(root, external["candidate_id"])
    trial_state_unchanged = bool(
        refined_trials_after == refined_trials_before_external
        and external_trials_after == external_trials_before_runtime
    )
    if not trial_state_unchanged:
        raise RuntimeError("postcommit transfer rewrote Candidate regression trial evidence")

    score = sum(
        1.0 if item["decision"] is expected_value else 0.0
        for item, expected_value in zip(results, expected, strict=True)
    ) / len(results)
    report = {
        "schema_version": 1,
        "passed": True,
        "challenge_family": "structured-postcommit-transfer-v1",
        "challenge_derived_after_refined_promotion": True,
        "refined_campaign_digest": refined["digest"],
        "refined_candidate_id": refined_candidate_id,
        "external_candidate_id": external["candidate_id"],
        "domain_chain": ["numeric", "object", "boolean"],
        "challenge_count": len(results),
        "challenge_support_overlap": support_overlap,
        "baseline_score": 0.0,
        "candidate_score": score,
        "fresh_engine_runs": [item["run_id"] for item in results],
        "fresh_engine_count": len(results),
        "durable_runs_persisted": all(item["durable_run_persisted"] for item in results),
        "candidate_trial_state_unchanged": trial_state_unchanged,
        "refined_failed_hypothesis_retained": refined["failed_hypothesis_evidence_retained"],
        "external_negative_evidence_retained": external["negative_evidence_retained"],
        "decisions": [item["decision"] for item in results],
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal post-promotion structured transfer evidence only. Challenge generation, semantics, "
            "and evaluation are repository-authored; the behavior-bound adapter remains a trusted "
            "in-process callable rather than an enforceable hostile-code sandbox. This is not "
            "independent production AGI evidence."
        ),
    }
    report["passed"] = bool(
        report["challenge_derived_after_refined_promotion"]
        and report["challenge_support_overlap"] is False
        and report["baseline_score"] == 0.0
        and report["candidate_score"] == 1.0
        and report["fresh_engine_count"] >= 3
        and report["durable_runs_persisted"]
        and report["candidate_trial_state_unchanged"]
        and report["refined_failed_hypothesis_retained"]
        and report["external_negative_evidence_retained"]
        and len(set(report["fresh_engine_runs"])) == report["fresh_engine_count"]
        and set(report["decisions"]) == {False, True}
    )
    if not report["passed"]:
        raise RuntimeError("postcommit structured transfer failed a required evidence gate")
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "structured-postcommit-transfer"
        / f"structured-postcommit-{_digest(seed)[:16]}.json",
        report,
    )
    return report
