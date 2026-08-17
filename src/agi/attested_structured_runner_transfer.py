from __future__ import annotations

from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import execute_program
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
)
from agi.structured_external_acquired_composition import (
    STRUCTURED_EXTERNAL_SCOPE,
    STRUCTURED_PROGRAM_SCOPE,
    _digest,
    _promote_external_candidate,
    _promote_structured_program,
    _result,
    _structured_adapter,
)
from continual.attested_external_tools import (
    AttestedContractedExternalToolRegistry,
    ExternalToolRunner,
    PinnedRunnerAttestationVerifier,
    RunnerIsolationAttestation,
)


def _runner_for_seed(seed: str, adapter_sha256: str, adapter_fn):
    token = _digest({"kind": "attested-structured-runner", "seed": seed})[:16]
    runner_id = f"structured-runner-{token}"
    verifier_id = "internal-structured-runner-pin"
    runner_sha256 = _digest(
        {
            "kind": "attested-structured-runner-test-double",
            "runner_id": runner_id,
            "transport": "canonical-json",
            "adapter_sha256": adapter_sha256,
        }
    )
    attestation = RunnerIsolationAttestation.create(
        verifier_id=verifier_id,
        runner_id=runner_id,
        runner_sha256=runner_sha256,
        adapter_sha256=adapter_sha256,
    )
    runner = ExternalToolRunner(
        adapter_sha256=adapter_sha256,
        runner_sha256=runner_sha256,
        attestation=attestation,
        invoke=adapter_fn,
    )
    verifier = PinnedRunnerAttestationVerifier(
        {(verifier_id, runner_id): attestation.statement_sha256}
    )
    return runner, verifier


def run_attested_structured_runner_transfer(root: Path, seed: str) -> dict[str, Any]:
    """Compose a promoted structured external Candidate through the attested runner boundary.

    The external numeric->object Candidate and synthesized object->boolean Candidate first pass their
    existing deterministic Candidate regression gates, including retained negative evidence. Their
    trial ledgers are then frozen. Three probes run through three fresh credential-free mechanical
    Engines; before every external invocation the active registry requires a separately supplied exact
    isolation-attestation pin, then rechecks behavior binding, domains, bytes, and call budgets. The
    resulting object is passed into the promoted acquired program. Both Candidate trial ledgers must
    remain byte-identical after the transfer.

    The runner used here is deliberately an internal deterministic test double. This campaign proves
    that structured post-promotion transfer is compatible with the new fail-closed attestation state
    machine; it does not independently establish that filesystem/network/subprocess isolation is real.
    Production evidence therefore still requires an independently operated runner and verifier.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    external, _legacy_adapter = _promote_external_candidate(root, seed=seed)
    learned = _promote_structured_program(root, seed=seed)
    candidate_ids = [str(external["candidate_id"]), str(learned["candidate_id"])]
    trial_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in candidate_ids
    }
    if not all(trial_before.values()):
        raise RuntimeError("attested structured transfer Candidates lack persisted regression trials")

    adapter_fn, adapter_sha256 = _structured_adapter(seed)
    if adapter_sha256 != external["adapter_sha256"]:
        raise RuntimeError("structured adapter identity changed before attested transfer")
    runner, verifier = _runner_for_seed(seed, adapter_sha256, adapter_fn)

    probes = (-3, 0, 4)
    results: list[dict[str, Any]] = []
    for probe in probes:
        expected_object = adapter_fn(probe)
        expected_boolean = execute_program(learned["program"], expected_object)

        engine = _mechanical_engine(root)
        engine._external_tool_registry = AttestedContractedExternalToolRegistry(
            root,
            {str(external["tool_id"]): runner},
            verifier,
        )
        run_id = _new_runtime_run(engine)
        external_response = _invoke(
            engine,
            run_id,
            scope=STRUCTURED_EXTERNAL_SCOPE,
            tool_id=str(external["tool_id"]),
            value=probe,
        )
        structured = _result(
            external_response,
            candidate_id=str(external["candidate_id"]),
            scope=STRUCTURED_EXTERNAL_SCOPE,
            tool_id=str(external["tool_id"]),
            program_kind="contracted_external_tool",
        )
        learned_response = _invoke(
            engine,
            run_id,
            scope=STRUCTURED_PROGRAM_SCOPE,
            tool_id=str(learned["tool_id"]),
            value=structured,
        )
        decision = _result(
            learned_response,
            candidate_id=str(learned["candidate_id"]),
            scope=STRUCTURED_PROGRAM_SCOPE,
            tool_id=str(learned["tool_id"]),
            program_kind="acquired_program",
        )
        if structured != expected_object or decision is not expected_boolean:
            raise RuntimeError("attested structured transfer disagreed with promoted semantics")
        results.append(
            {
                "run_id": run_id,
                "probe_sha256": _digest(probe),
                "object_sha256": _digest(structured),
                "decision": decision,
            }
        )

    fresh_engine_runs = [item["run_id"] for item in results]
    if len(set(fresh_engine_runs)) != len(fresh_engine_runs):
        raise RuntimeError("attested structured transfer reused a runtime run")

    trial_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in candidate_ids
    }
    trial_state_unchanged = trial_after == trial_before
    if not trial_state_unchanged:
        raise RuntimeError("attested structured transfer rewrote Candidate regression trial state")

    report = {
        "schema_version": 1,
        "passed": True,
        "candidate_ids": candidate_ids,
        "tool_ids": [str(external["tool_id"]), str(learned["tool_id"])],
        "scopes": [STRUCTURED_EXTERNAL_SCOPE, STRUCTURED_PROGRAM_SCOPE],
        "domain_chain": ["numeric", "object", "boolean"],
        "fresh_engine_runs": fresh_engine_runs,
        "fresh_engine_count": len(fresh_engine_runs),
        "fresh_engine_composition_matched": True,
        "runner_attestation_checked": True,
        "runner_attestation_sha256": runner.attestation.statement_sha256,
        "runner_id": runner.attestation.runner_id,
        "runner_sha256": runner.runner_sha256,
        "runner_verifier_id": runner.attestation.verifier_id,
        "runner_isolation_profile": runner.attestation.profile.to_dict(),
        "runner_test_double_only": True,
        "candidate_trial_state_unchanged": trial_state_unchanged,
        "candidate_trial_file_counts": {
            candidate_id: len(trial_before[candidate_id])
            for candidate_id in candidate_ids
        },
        "external_negative_evidence_retained": bool(external["negative_evidence_retained"]),
        "learned_negative_evidence_retained": bool(learned["negative_evidence_retained"]),
        "learned_target_task_id": str(learned["target_task_id"]),
        "learned_target_repeats": learned["target_repeats"],
        "decisions": [item["decision"] for item in results],
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal structured transfer evidence through the fail-closed runner-attestation state "
            "machine only. The runner and trust pin are deterministic test doubles, so this does not "
            "independently prove operating-system isolation, safe arbitrary tool use, or AGI. A real "
            "independently operated isolated runner plus independently verifiable attestation remains "
            "required before production evidence can be admitted."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "attested-structured-runner-transfer"
        / f"attested-structured-transfer-{_digest(seed)[:16]}.json",
        report,
    )
    return report
