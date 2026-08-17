from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from agi.external_tool_acquisition import (
    EXTERNAL_TOOL_SCOPE,
    _hidden_external_adapter,
    run_external_tool_acquisition_campaign,
)
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
)
from continual.contracted_external_tools import (
    ContractedExternalToolError,
    ContractedExternalToolRegistry,
    ExternalToolAdapter,
)
from continual.learning_engine import LearningEnabledEngine


def _engine_with_adapter(
    root: Path,
    *,
    tool_id: str,
    adapter: ExternalToolAdapter,
) -> LearningEnabledEngine:
    engine = _mechanical_engine(root)
    engine._external_tool_registry = ContractedExternalToolRegistry(root, {tool_id: adapter})
    return engine


def _result(response: Mapping[str, Any], *, candidate_id: str) -> Mapping[str, Any]:
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise RuntimeError("external runtime retention returned malformed execution output")
    if result.get("candidate_id") != candidate_id:
        raise RuntimeError("external runtime retention used the wrong Candidate")
    if result.get("execution_kind") != "verified_learned_tool":
        raise RuntimeError("external runtime retention bypassed verified-tool execution")
    if result.get("program_kind") != "contracted_external_tool":
        raise RuntimeError("external runtime retention bypassed contracted external-tool execution")
    return result


def run_external_tool_runtime_retention(root: Path, seed: str) -> dict[str, Any]:
    """Falsify whether a behavior-bound host tool can be safely reattached after Engine restart.

    Acquisition and deterministic regression happen first. The Candidate trial ledger is then frozen.
    A host adapter with the verified behavior is attached to one mechanical LearningEnabledEngine and
    invoked twice in a durable run, proving journal reuse. A new Engine instance receives the same host
    adapter and must reproduce the result in a distinct run. Finally, a fresh Engine receives a wrong
    function that deliberately claims the verified adapter identity; behavior-binding challenges must
    reject it before execution. Candidate regression-trial files must remain byte-identical throughout.

    This is retained host-tool evidence under a trusted zero-effect adapter boundary. It does not prove
    that an arbitrary hostile host implementation is side-effect-free and is not AGI evidence.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    acquisition = run_external_tool_acquisition_campaign(root, seed)
    if not acquisition.get("passed"):
        raise RuntimeError("external tool acquisition did not pass")

    candidate_id = str(acquisition["candidate_id"])
    tool_id = str(acquisition["tool_id"])
    adapter_fn, adapter_sha256 = _hidden_external_adapter(seed)
    if adapter_sha256 != acquisition.get("adapter_sha256"):
        raise RuntimeError("reattached adapter identity disagrees with acquired Candidate")
    adapter = ExternalToolAdapter(adapter_sha256=adapter_sha256, function=adapter_fn)

    trial_before = _candidate_trial_snapshot(root, candidate_id)
    if not trial_before:
        raise RuntimeError("external Candidate has no regression trial evidence")

    probe = {"restart": 11, "retained": 23, "label": "fresh-engine-host-tool"}
    expected = adapter_fn(probe)

    first_engine = _engine_with_adapter(root, tool_id=tool_id, adapter=adapter)
    first_run = _new_runtime_run(first_engine)
    first_response = _invoke(
        first_engine,
        first_run,
        scope=EXTERNAL_TOOL_SCOPE,
        tool_id=tool_id,
        value=probe,
    )
    repeated_response = _invoke(
        first_engine,
        first_run,
        scope=EXTERNAL_TOOL_SCOPE,
        tool_id=tool_id,
        value=probe,
    )
    first_result = _result(first_response, candidate_id=candidate_id)
    repeated_result = _result(repeated_response, candidate_id=candidate_id)
    if first_result.get("output") != expected or repeated_result != first_result:
        raise RuntimeError("same-run external tool retention replay failed")

    second_engine = _engine_with_adapter(root, tool_id=tool_id, adapter=adapter)
    second_run = _new_runtime_run(second_engine)
    restarted_response = _invoke(
        second_engine,
        second_run,
        scope=EXTERNAL_TOOL_SCOPE,
        tool_id=tool_id,
        value=probe,
    )
    restarted_result = _result(restarted_response, candidate_id=candidate_id)
    if restarted_result != first_result:
        raise RuntimeError("fresh Engine did not retain the behavior-bound external capability")

    def wrong_behavior(_value: Any) -> str:
        return "wrong-behavior-after-restart"

    spoofed = ExternalToolAdapter(adapter_sha256=adapter_sha256, function=wrong_behavior)
    spoofed_engine = _engine_with_adapter(root, tool_id=tool_id, adapter=spoofed)
    spoofed_run = _new_runtime_run(spoofed_engine)
    spoofed_rejected = False
    try:
        _invoke(
            spoofed_engine,
            spoofed_run,
            scope=EXTERNAL_TOOL_SCOPE,
            tool_id=tool_id,
            value=probe,
        )
    except ContractedExternalToolError:
        spoofed_rejected = True
    if not spoofed_rejected:
        raise RuntimeError("fresh Engine accepted a same-identity adapter with wrong behavior")

    trial_after = _candidate_trial_snapshot(root, candidate_id)
    trial_state_unchanged = trial_after == trial_before
    if not trial_state_unchanged:
        raise RuntimeError("external runtime reattachment rewrote Candidate regression trials")

    report = {
        "schema_version": 1,
        "passed": True,
        "candidate_id": candidate_id,
        "tool_id": tool_id,
        "scope": EXTERNAL_TOOL_SCOPE,
        "first_runtime_run_id": first_run,
        "fresh_runtime_run_id": second_run,
        "distinct_runtime_runs": first_run != second_run,
        "same_run_replay_reused": repeated_response == first_response,
        "fresh_engine_replay_matched": restarted_result == first_result,
        "spoofed_identity_wrong_behavior_rejected_after_restart": spoofed_rejected,
        "candidate_trial_state_unchanged": trial_state_unchanged,
        "candidate_trial_file_count": len(trial_before),
        "probe_sha256": hashlib.sha256(
            __import__("json").dumps(probe, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "output_sha256": hashlib.sha256(
            __import__("json").dumps(expected, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "execution_kind": first_result.get("execution_kind"),
        "program_kind": first_result.get("program_kind"),
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal retained host-tool evidence only. Behavior binding protects against identity "
            "spoofing under committed challenges, but declared zero effects do not prove an arbitrary "
            "host implementation is harmless, and this does not prove open-ended tool use or AGI."
        ),
    }
    return report
