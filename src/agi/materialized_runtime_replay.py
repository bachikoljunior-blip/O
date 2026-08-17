from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

from agi.acquired_programs import execute_program
from continual.contracted_external_tools import ContractedExternalToolRegistry
from continual.learned_tools import LearnedToolError
from continual.learning_engine import LearningEnabledEngine
from continual.store import Store


class _ForbiddenModelClient:
    """Fail immediately if a supposedly mechanical replay tries to invoke a live model."""

    model = "disabled-for-mechanical-replay"

    def call(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(
            "materialized runtime replay unexpectedly requested live model execution"
        )


def _mechanical_engine(root: Path) -> LearningEnabledEngine:
    """Construct only the stateful mechanical half of LearningEnabledEngine.

    The retained-capability replay deliberately exercises the verified learned-tool dispatch path,
    which is required to be deterministic and credential-free after Candidate materialization. Using
    the normal constructor would eagerly create ModelClient even though this probe must never call a
    model. Bypassing that eager dependency makes the test stricter: any accidental semantic fallback
    hits _ForbiddenModelClient and fails immediately instead of depending on CI credentials.
    """

    resolved = root.resolve()
    engine = object.__new__(LearningEnabledEngine)
    engine.root = resolved
    engine.store = Store(resolved)
    engine.model = _ForbiddenModelClient()
    engine._external_tool_registry = ContractedExternalToolRegistry(resolved, {})
    return engine


def _digest_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate_trial_snapshot(root: Path, candidate_id: str) -> dict[str, str]:
    trial_dir = root / ".continual" / "candidates" / candidate_id / "trials"
    return {path.name: _digest_bytes(path) for path in sorted(trial_dir.glob("*.json"))}


def _new_runtime_run(engine: LearningEnabledEngine) -> str:
    run_id = engine.store.new_id("run")
    run_dir = engine.store.run_dir(run_id)
    run_dir.mkdir(parents=True)
    engine.store.atomic_json(
        run_dir / "snapshot.json",
        {"revision": 0, "status": "continue", "phase": "unit_pending"},
    )
    return run_id


def _invoke(
    engine: LearningEnabledEngine,
    run_id: str,
    *,
    scope: str,
    tool_id: str,
    value: Any,
) -> dict[str, Any]:
    return engine._invoke(
        run_id,
        "execute",
        {
            "snapshot": {"revision": 0},
            "execution_unit": {
                "goal": "replay a persisted regression-verified capability after repository materialization",
                "scope": scope,
                "learned_tool_call": {"tool_id": tool_id, "input": value},
            },
        },
    )


def _probe_for_domain(domain: str) -> Any:
    if domain == "string":
        return "materialized-retention-probe"
    if domain == "sequence":
        return [13, 21, 34, 55]
    if domain == "numeric":
        return 17
    if domain == "object":
        return {"alpha": 3, "beta": "retention", "gamma": [2, 5, 8]}
    raise RuntimeError(f"unsupported materialized capability input domain: {domain}")


def _select_acquired_candidates(root: Path) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for path in sorted((root / ".continual" / "candidates").glob("*/candidate.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            continue
        if raw.get("target_component") != "acquired_program":
            continue
        if raw.get("status") != "active-for-scope":
            continue
        acquired = raw.get("acquired_program")
        verified = raw.get("verified_scope_states")
        if not isinstance(acquired, Mapping) or not isinstance(verified, Mapping):
            continue
        scope = acquired.get("scope")
        if not isinstance(scope, str) or scope not in verified:
            continue
        candidates.append((dict(raw), dict(acquired)))
    if not candidates:
        raise RuntimeError("no active materialized acquired-program Candidate is available")
    return candidates


def _validate_runtime_output(
    output: Mapping[str, Any],
    *,
    expected: Any,
    candidate_id: str,
) -> Mapping[str, Any]:
    result = output.get("result")
    if not isinstance(result, Mapping):
        raise RuntimeError("materialized runtime replay returned malformed execution output")
    if result.get("output") != expected:
        raise RuntimeError("materialized runtime replay disagreed with persisted program semantics")
    if result.get("candidate_id") != candidate_id:
        raise RuntimeError("materialized runtime replay used the wrong Candidate")
    if result.get("program_kind") != "acquired_program":
        raise RuntimeError("materialized runtime replay did not use the acquired-program path")
    return result


def run_materialized_runtime_retention_replay(root: Path) -> dict[str, Any]:
    """Falsify whether all committed learned capabilities survive fresh Engine instances.

    Repository materialization is useful only if every active verified acquired-program Candidate can
    be consumed after checkout without spending another Candidate trial. The replay discovers all
    materialized active-for-scope acquired programs, proves each exact-scope tool fails closed from a
    root with no Candidate state, invokes all capabilities through one real LearningEnabledEngine
    runtime, repeats the calls in the same run to exercise invocation reuse, then reconstructs a fresh
    Engine and replays the capabilities in reverse order. Every output must match the persisted
    program semantics, each dispatch must resolve to the intended Candidate, and every Candidate trial
    file must remain byte-identical across the whole multi-capability replay. No live model credential
    is permitted or required.
    """

    root = root.resolve()
    selected = _select_acquired_candidates(root)
    prepared: list[dict[str, Any]] = []
    for candidate, acquired in selected:
        candidate_id = str(candidate["candidate_id"])
        scope = str(acquired["scope"])
        tool_id = str(acquired["tool_id"])
        program = acquired.get("program")
        if not isinstance(program, Mapping):
            raise RuntimeError("materialized acquired program descriptor is missing")
        input_domain = str(program.get("input_domain", ""))
        probe = _probe_for_domain(input_domain)
        prepared.append(
            {
                "candidate_id": candidate_id,
                "scope": scope,
                "tool_id": tool_id,
                "program": dict(program),
                "input_domain": input_domain,
                "probe": probe,
                "expected": execute_program(program, probe),
            }
        )

    candidate_ids = [item["candidate_id"] for item in prepared]
    scopes = [item["scope"] for item in prepared]
    if len(set(candidate_ids)) != len(candidate_ids):
        raise RuntimeError("materialized capability discovery returned duplicate Candidate IDs")
    if len(set(scopes)) != len(scopes):
        raise RuntimeError("materialized capability discovery returned duplicate verified scopes")

    with tempfile.TemporaryDirectory(prefix="agi-materialized-baseline-") as temporary:
        empty_root = Path(temporary)
        empty_engine = _mechanical_engine(empty_root)
        empty_run = _new_runtime_run(empty_engine)
        for item in prepared:
            baseline_failed_closed = False
            try:
                _invoke(
                    empty_engine,
                    empty_run,
                    scope=item["scope"],
                    tool_id=item["tool_id"],
                    value=item["probe"],
                )
            except LearnedToolError:
                baseline_failed_closed = True
            if not baseline_failed_closed:
                raise RuntimeError(
                    f"materialized capability {item['candidate_id']} was callable without Candidate state"
                )
            item["baseline_without_candidate_failed_closed"] = True

    trial_snapshots_before = {
        item["candidate_id"]: _candidate_trial_snapshot(root, item["candidate_id"])
        for item in prepared
    }

    first_engine = _mechanical_engine(root)
    first_run = _new_runtime_run(first_engine)
    first_outputs: dict[str, dict[str, Any]] = {}
    repeated_outputs: dict[str, dict[str, Any]] = {}
    for item in prepared:
        first_outputs[item["candidate_id"]] = _invoke(
            first_engine,
            first_run,
            scope=item["scope"],
            tool_id=item["tool_id"],
            value=item["probe"],
        )
    for item in prepared:
        repeated_outputs[item["candidate_id"]] = _invoke(
            first_engine,
            first_run,
            scope=item["scope"],
            tool_id=item["tool_id"],
            value=item["probe"],
        )

    second_engine = _mechanical_engine(root)
    second_run = _new_runtime_run(second_engine)
    restarted_outputs: dict[str, dict[str, Any]] = {}
    for item in reversed(prepared):
        restarted_outputs[item["candidate_id"]] = _invoke(
            second_engine,
            second_run,
            scope=item["scope"],
            tool_id=item["tool_id"],
            value=item["probe"],
        )

    capabilities: list[dict[str, Any]] = []
    for item in prepared:
        candidate_id = item["candidate_id"]
        first = first_outputs[candidate_id]
        repeated = repeated_outputs[candidate_id]
        restarted = restarted_outputs[candidate_id]
        first_result = _validate_runtime_output(
            first,
            expected=item["expected"],
            candidate_id=candidate_id,
        )
        _validate_runtime_output(
            repeated,
            expected=item["expected"],
            candidate_id=candidate_id,
        )
        restarted_result = _validate_runtime_output(
            restarted,
            expected=item["expected"],
            candidate_id=candidate_id,
        )
        capabilities.append(
            {
                "candidate_id": candidate_id,
                "scope": item["scope"],
                "tool_id": item["tool_id"],
                "input_domain": item["input_domain"],
                "probe_sha256": hashlib.sha256(
                    json.dumps(item["probe"], sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
                "output_sha256": hashlib.sha256(
                    json.dumps(item["expected"], sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
                "baseline_without_candidate_failed_closed": item[
                    "baseline_without_candidate_failed_closed"
                ],
                "same_run_replay_reused": repeated == first,
                "fresh_engine_replay_matched": restarted_result == first_result,
                "candidate_trial_file_count": len(trial_snapshots_before[candidate_id]),
                "execution_kind": first_result.get("execution_kind"),
                "program_kind": first_result.get("program_kind"),
            }
        )

    trial_snapshots_after = {
        item["candidate_id"]: _candidate_trial_snapshot(root, item["candidate_id"])
        for item in prepared
    }
    if trial_snapshots_after != trial_snapshots_before:
        changed = sorted(
            candidate_id
            for candidate_id in candidate_ids
            if trial_snapshots_before[candidate_id] != trial_snapshots_after[candidate_id]
        )
        raise RuntimeError(
            "materialized runtime replay consumed or rewrote Candidate trial state: "
            + ", ".join(changed)
        )

    first_capability = capabilities[0]
    all_passed = all(
        item["baseline_without_candidate_failed_closed"]
        and item["same_run_replay_reused"]
        and item["fresh_engine_replay_matched"]
        and item["execution_kind"] == "verified_learned_tool"
        and item["program_kind"] == "acquired_program"
        for item in capabilities
    )
    return {
        "schema_version": 2,
        "passed": all_passed,
        "capability_count": len(capabilities),
        "distinct_scope_count": len(set(scopes)),
        "candidate_ids": candidate_ids,
        "capabilities": capabilities,
        "all_materialized_capabilities_replayed": all_passed,
        "all_candidate_trial_state_unchanged": trial_snapshots_after == trial_snapshots_before,
        "fresh_engine_reverse_order_replay": True,
        "first_runtime_run_id": first_run,
        "fresh_runtime_run_id": second_run,
        "live_model_invocation_required": False,
        # Legacy single-capability fields remain as a compatibility summary of the first replayed
        # Candidate while callers migrate to the per-capability records above.
        "candidate_id": first_capability["candidate_id"],
        "scope": first_capability["scope"],
        "tool_id": first_capability["tool_id"],
        "input_domain": first_capability["input_domain"],
        "probe_sha256": first_capability["probe_sha256"],
        "output_sha256": first_capability["output_sha256"],
        "baseline_without_candidate_failed_closed": first_capability[
            "baseline_without_candidate_failed_closed"
        ],
        "same_run_replay_reused": first_capability["same_run_replay_reused"],
        "fresh_engine_replay_matched": first_capability["fresh_engine_replay_matched"],
        "candidate_trial_state_unchanged": trial_snapshots_after == trial_snapshots_before,
        "candidate_trial_file_count": first_capability["candidate_trial_file_count"],
        "execution_kind": first_capability["execution_kind"],
        "program_kind": first_capability["program_kind"],
        "claim_boundary": (
            "Internal multi-capability retained-runtime evidence only. The capabilities, evaluator, "
            "and materialization pipeline remain repository-internal and do not prove AGI."
        ),
    }
