from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program, synthesize_program
from agi.external_tool_runtime_retention import _engine_with_adapter
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _new_runtime_run,
)
from continual.acquired_program_tools import acquired_program_candidate_payload
from continual.candidate_regression import record_candidate_regression
from continual.contracted_external_tools import (
    ExternalToolAdapter,
    contracted_external_tool_candidate_payload,
)


STRUCTURED_EXTERNAL_SCOPE = "agi/contracted-external-tool/numeric-to-object-v1"
STRUCTURED_PROGRAM_SCOPE = "agi/acquired-program/object-score-threshold-v1"


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _measurement(
    task_id: str,
    repeat: int,
    score: float,
    artifact: Any,
    *,
    domain: str,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "criterion": "self_improvement",
        "domain": domain,
        "repeat_index": repeat,
        "passed": score == 1.0,
        "score": score,
        "artifact_sha256": _digest(artifact),
    }


def _structured_adapter(seed: str):
    tag = hashlib.sha256(f"structured-output-v1\0{seed}".encode("utf-8")).hexdigest()[:8]

    def adapter(value: Any) -> dict[str, Any]:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("numeric input required")
        return {
            "score": value * 2 + 1,
            "nonnegative": value >= 0,
            "tag": tag,
        }

    identity = _digest(
        {
            "kind": "host-numeric-to-object-v1",
            "tag": tag,
            "input_domain": "numeric",
            "output_domain": "object",
        }
    )
    return adapter, identity


def _promote_external_candidate(
    root: Path,
    *,
    seed: str,
) -> tuple[dict[str, Any], ExternalToolAdapter]:
    adapter_fn, adapter_sha256 = _structured_adapter(seed)
    token = _digest({"kind": "structured-external", "seed": seed})[:16]
    candidate_id = f"structured-external-{token}"
    tool_id = f"host-structured-output-{token}"
    heldout = (-4, 0, 5)
    binding_challenges = tuple(
        {
            "input": value,
            "output_sha256": _digest(adapter_fn(value)),
        }
        for value in heldout
    )
    candidate = contracted_external_tool_candidate_payload(
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=STRUCTURED_EXTERNAL_SCOPE,
        input_domain="numeric",
        output_domain="object",
        effects=(),
        max_calls=4,
        max_input_bytes=256,
        max_output_bytes=1024,
        adapter_sha256=adapter_sha256,
        description="Behavior-bound host adapter that emits finite structured JSON for learned composition.",
        binding_challenges=binding_challenges,
    )
    candidate["supporting_evidence"] = [
        {
            "type": "host_adapter_contract",
            "adapter_sha256": adapter_sha256,
            "effects": [],
            "input_domain": "numeric",
            "output_domain": "object",
            "binding_challenge_count": len(binding_challenges),
            "adapter_source_persisted": False,
        }
    ]
    candidate_dir = root / ".continual" / "candidates" / candidate_id
    _atomic_json(candidate_dir / "candidate.json", candidate)

    target = f"withheld-structured-external-{token}"
    baseline = []
    trial = []
    for repeat, value in enumerate(heldout):
        artifact = {
            "input_sha256": _digest(value),
            "output_sha256": _digest(adapter_fn(value)),
            "adapter_sha256": adapter_sha256,
        }
        baseline.append(
            _measurement(
                target,
                repeat,
                0.0,
                artifact,
                domain="external-tool:numeric-to-object",
            )
        )
        trial.append(
            _measurement(
                target,
                repeat,
                1.0,
                artifact,
                domain="external-tool:numeric-to-object",
            )
        )
    protected = _measurement(
        "protected-existing-capability",
        0,
        1.0,
        {"candidate_id": candidate_id, "invariant": "prior capabilities retained"},
        domain="protected",
    )
    baseline_path = candidate_dir / "regression-input" / "baseline.json"
    adverse_path = candidate_dir / "regression-input" / "candidate-adverse.json"
    valid_path = candidate_dir / "regression-input" / "candidate-valid.json"
    _atomic_json(baseline_path, [*baseline, protected])
    _atomic_json(
        adverse_path,
        [
            *trial,
            {
                **protected,
                "passed": False,
                "score": 0.0,
                "artifact_sha256": _digest({"forced-protected-drop": candidate_id}),
            },
        ],
    )
    _atomic_json(valid_path, [*trial, protected])
    rejected = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=STRUCTURED_EXTERNAL_SCOPE,
        baseline_path=baseline_path,
        candidate_path=adverse_path,
        target_task_ids=[target],
    )
    promoted = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=STRUCTURED_EXTERNAL_SCOPE,
        baseline_path=baseline_path,
        candidate_path=valid_path,
        target_task_ids=[target],
    )
    if rejected["decision"]["adopt_candidate"]:
        raise RuntimeError("external structured Candidate ignored a protected regression")
    if not promoted["decision"]["adopt_candidate"]:
        raise RuntimeError("external structured Candidate failed valid repeated regression")
    return (
        {
            "candidate_id": candidate_id,
            "tool_id": tool_id,
            "adapter_sha256": adapter_sha256,
            "target_task_id": target,
            "negative_evidence_retained": True,
        },
        ExternalToolAdapter(adapter_sha256=adapter_sha256, function=adapter_fn),
    )


def _promote_structured_program(root: Path, *, seed: str) -> dict[str, Any]:
    token = _digest({"kind": "structured-program", "seed": seed})[:16]
    candidate_id = f"structured-program-{token}"
    tool_id = f"acquired-object-threshold-{token}"
    examples = [
        ProgramExample({"score": -5, "noise": "a"}, False),
        ProgramExample({"score": 0, "noise": "b"}, False),
        ProgramExample({"score": 1, "noise": "c"}, True),
        ProgramExample({"score": 9, "noise": "d"}, True),
    ]
    learned = synthesize_program(
        input_domain="object",
        output_domain="boolean",
        examples=examples,
        max_nodes=6,
    )
    candidate_dir = root / ".continual" / "candidates" / candidate_id
    _atomic_json(
        candidate_dir / "candidate.json",
        acquired_program_candidate_payload(
            candidate_id=candidate_id,
            tool_id=tool_id,
            scope=STRUCTURED_PROGRAM_SCOPE,
            program=learned.descriptor(),
            description="Bounded object score predicate synthesized from demonstrations.",
        ),
    )

    target = f"withheld-structured-program-{token}"
    protected = _measurement(
        "protected-existing-capability",
        0,
        1.0,
        {"candidate_id": candidate_id, "invariant": "prior capabilities retained"},
        domain="protected",
    )
    one_repeat_baseline = candidate_dir / "regression-input" / "baseline-one.json"
    one_repeat_trial = candidate_dir / "regression-input" / "candidate-one.json"
    _atomic_json(
        one_repeat_baseline,
        [
            _measurement(
                target,
                0,
                0.0,
                {"probe": "single-repeat"},
                domain="structured-program",
            ),
            protected,
        ],
    )
    _atomic_json(
        one_repeat_trial,
        [
            _measurement(
                target,
                0,
                1.0,
                {"probe": "single-repeat"},
                domain="structured-program",
            ),
            protected,
        ],
    )
    rejected = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=STRUCTURED_PROGRAM_SCOPE,
        baseline_path=one_repeat_baseline,
        candidate_path=one_repeat_trial,
        target_task_ids=[target],
    )
    if rejected["decision"]["adopt_candidate"]:
        raise RuntimeError("structured learned Candidate promoted without repeated target evidence")

    baseline_path = candidate_dir / "regression-input" / "baseline.json"
    valid_path = candidate_dir / "regression-input" / "candidate-valid.json"
    baseline = [
        _measurement(
            target,
            repeat,
            0.0,
            {"probe": repeat, "withheld": True},
            domain="structured-program",
        )
        for repeat in range(2)
    ]
    trial = [
        _measurement(
            target,
            repeat,
            1.0,
            {"probe": repeat, "withheld": True},
            domain="structured-program",
        )
        for repeat in range(2)
    ]
    _atomic_json(baseline_path, [*baseline, protected])
    _atomic_json(valid_path, [*trial, protected])
    promoted = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=STRUCTURED_PROGRAM_SCOPE,
        baseline_path=baseline_path,
        candidate_path=valid_path,
        target_task_ids=[target],
    )
    if not promoted["decision"]["adopt_candidate"]:
        raise RuntimeError("structured learned Candidate failed repeated target regression")
    state = json.loads((candidate_dir / "candidate.json").read_text(encoding="utf-8"))
    negative_retained = any(
        isinstance(item, Mapping)
        and item.get("type") == "deterministic_regression_gate_failure"
        for item in state.get("contradictory_evidence", [])
    )
    if not negative_retained:
        raise RuntimeError("structured program did not retain failed single-repeat evidence")
    return {
        "candidate_id": candidate_id,
        "tool_id": tool_id,
        "program": learned.descriptor(),
        "target_task_id": target,
        "negative_evidence_retained": negative_retained,
        "target_repeats": promoted["decision"]["target_repeats"],
    }


def _result(
    response: Mapping[str, Any],
    *,
    candidate_id: str,
    scope: str,
    tool_id: str,
    program_kind: str,
) -> Any:
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise RuntimeError("structured composition returned malformed execution output")
    if result.get("candidate_id") != candidate_id:
        raise RuntimeError("structured composition selected the wrong Candidate")
    if result.get("scope") != scope or result.get("tool_id") != tool_id:
        raise RuntimeError("structured composition crossed an exact scope/tool boundary")
    if result.get("execution_kind") != "verified_learned_tool":
        raise RuntimeError("structured composition bypassed verified learned-tool execution")
    if result.get("program_kind") != program_kind:
        raise RuntimeError("structured composition used the wrong program kind")
    return result.get("output")


def run_structured_external_acquired_composition(root: Path, seed: str) -> dict[str, Any]:
    """Compose a behavior-bound object-producing host tool into a newly learned object program.

    Both capabilities must first pass exact-scope deterministic Candidate regression. The learned
    program is deliberately rejected once for insufficient target repeats, and the external Candidate
    is deliberately rejected once for a protected capability regression. Those failures stay in the
    ledgers. The promoted pair is then executed as numeric -> object -> boolean through two distinct
    fresh mechanical Engine instances. Runtime use must not consume or rewrite either Candidate's
    regression trial evidence.

    This remains internal bounded transfer evidence. Behavior binding does not prove an arbitrary host
    callable has no ambient filesystem/network/subprocess effects, and this is not independent AGI
    evidence.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    external, adapter = _promote_external_candidate(root, seed=seed)
    learned = _promote_structured_program(root, seed=seed)
    trial_before = {
        external["candidate_id"]: _candidate_trial_snapshot(root, external["candidate_id"]),
        learned["candidate_id"]: _candidate_trial_snapshot(root, learned["candidate_id"]),
    }
    if not all(trial_before.values()):
        raise RuntimeError("structured composition Candidates lack persisted regression trials")

    adapter_fn, adapter_sha256 = _structured_adapter(seed)
    if adapter_sha256 != external["adapter_sha256"]:
        raise RuntimeError("structured adapter identity changed before composition")
    probes = (-3, 4)
    results = []
    for probe in probes:
        expected_object = adapter_fn(probe)
        expected_boolean = execute_program(learned["program"], expected_object)
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
        structured = _result(
            external_response,
            candidate_id=external["candidate_id"],
            scope=STRUCTURED_EXTERNAL_SCOPE,
            tool_id=external["tool_id"],
            program_kind="contracted_external_tool",
        )
        learned_response = _invoke(
            engine,
            run_id,
            scope=STRUCTURED_PROGRAM_SCOPE,
            tool_id=learned["tool_id"],
            value=structured,
        )
        decision = _result(
            learned_response,
            candidate_id=learned["candidate_id"],
            scope=STRUCTURED_PROGRAM_SCOPE,
            tool_id=learned["tool_id"],
            program_kind="acquired_program",
        )
        if structured != expected_object or decision is not expected_boolean:
            raise RuntimeError("fresh Engine structured composition disagreed with verified semantics")
        results.append(
            {
                "run_id": run_id,
                "probe_sha256": _digest(probe),
                "object_sha256": _digest(structured),
                "decision": decision,
            }
        )
    if len({item["run_id"] for item in results}) != len(results):
        raise RuntimeError("structured composition reused an Engine run")

    trial_after = {
        external["candidate_id"]: _candidate_trial_snapshot(root, external["candidate_id"]),
        learned["candidate_id"]: _candidate_trial_snapshot(root, learned["candidate_id"]),
    }
    trial_state_unchanged = trial_after == trial_before
    if not trial_state_unchanged:
        raise RuntimeError("structured composition rewrote Candidate regression trial state")

    report = {
        "schema_version": 1,
        "passed": True,
        "candidate_ids": [external["candidate_id"], learned["candidate_id"]],
        "tool_ids": [external["tool_id"], learned["tool_id"]],
        "scopes": [STRUCTURED_EXTERNAL_SCOPE, STRUCTURED_PROGRAM_SCOPE],
        "domain_chain": ["numeric", "object", "boolean"],
        "fresh_engine_runs": [item["run_id"] for item in results],
        "fresh_engine_composition_matched": True,
        "candidate_trial_state_unchanged": trial_state_unchanged,
        "external_negative_evidence_retained": external["negative_evidence_retained"],
        "learned_negative_evidence_retained": learned["negative_evidence_retained"],
        "learned_target_repeats": learned["target_repeats"],
        "decisions": [item["decision"] for item in results],
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded structured transfer/retention evidence only. Behavior binding and finite "
            "JSON contracts do not prove arbitrary in-process host code lacks ambient side effects, and "
            "the evaluator is not an independent production AGI gate."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "structured-external-acquired-composition"
        / f"structured-composition-{_digest(seed)[:16]}.json",
        report,
    )
    return report
