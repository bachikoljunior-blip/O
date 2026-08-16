from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from continual.acquired_program_tools import (
    AcquiredProgramRegistry,
    AcquiredProgramToolError,
    acquired_program_candidate_payload,
)
from continual.candidate_regression import record_candidate_regression
from continual.learned_tools import LearnedToolError
from continual.learning_engine import LearningEnabledEngine

from .acquired_program_runtime import (
    ACQUIRED_RUNTIME_SCOPE,
    _atomic_json,
    _digest,
    _hidden_descriptor,
    _inputs,
    _new_runtime_run,
)
from .acquired_programs import ProgramExample, execute_program, synthesize_program


ACQUIRED_CONTINUAL_SCOPE_PREFIX = f"{ACQUIRED_RUNTIME_SCOPE}/accumulation"


def _measurement(
    *,
    task_id: str,
    repeat: int,
    score: float,
    artifact: Any,
    domain: str,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "criterion": "continual_learning",
        "domain": domain,
        "repeat_index": repeat,
        "passed": score == 1.0,
        "score": score,
        "artifact_sha256": _digest(artifact),
    }


def _runtime_call_scope(
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
                "goal": "apply a previously regression-verified acquired program after another learning stage",
                "scope": scope,
                "learned_tool_call": {"tool_id": tool_id, "input": value},
            },
        },
    )


def _stable_candidate(value: Mapping[str, Any]) -> dict[str, Any]:
    stable = dict(value)
    for key in (
        "status",
        "scope_states",
        "verified_scope_states",
        "regression_decision_refs",
        "supporting_evidence",
        "contradictory_evidence",
    ):
        stable.pop(key, None)
    return stable


def _retest_stage(
    root: Path,
    stage: Mapping[str, Any],
    *,
    engine_factory: Callable[[Path], LearningEnabledEngine],
) -> dict[str, Any]:
    hidden = _hidden_descriptor(str(stage["seed_ephemeral"]))
    _, heldout_inputs = _inputs(str(hidden["input_domain"]))
    expected = tuple(execute_program(hidden, value) for value in heldout_inputs)
    engine = engine_factory(root)
    run_id = _new_runtime_run(engine)
    results: list[dict[str, Any]] = []
    for query, answer_expected in zip(heldout_inputs, expected, strict=True):
        output = _runtime_call_scope(
            engine,
            run_id,
            scope=str(stage["scope"]),
            tool_id=str(stage["tool_id"]),
            value=query,
        )
        answer = output["result"]["output"]
        results.append(
            {
                "query_sha256": _digest(query),
                "answer_sha256": _digest(answer),
                "success": answer == answer_expected,
            }
        )
    return {
        "candidate_id": stage["candidate_id"],
        "tool_id": stage["tool_id"],
        "scope": stage["scope"],
        "run_id": run_id,
        "score": sum(1.0 if item["success"] else 0.0 for item in results) / len(results),
        "results": results,
    }


def run_acquired_program_continual_campaign(
    root: Path,
    seeds: Sequence[str],
    *,
    engine_factory: Callable[[Path], LearningEnabledEngine] = LearningEnabledEngine,
) -> dict[str, Any]:
    """Sequentially acquire hidden programs while requiring retention after every fresh-engine restart.

    Each stage uses a distinct exact scope and evaluator commitment. Before a new Candidate may be
    promoted, all previously acquired programs are re-run on withheld inputs and included as
    protected regression measurements. An intentionally corrupted protected measurement is first
    required to block adoption and persist contradictory evidence. The valid trial may pass only if
    every measured prior capability still succeeds. After promotion, a fresh Engine re-runs all
    accumulated capabilities again. Seeds are ephemeral inputs and are omitted from the persisted
    report.
    """

    if len(seeds) < 3 or len(set(seeds)) != len(seeds) or any(not isinstance(seed, str) or not seed for seed in seeds):
        raise ValueError("at least three distinct non-empty hidden seeds are required")
    root = root.resolve()
    stage_specs: list[dict[str, Any]] = []
    stage_reports: list[dict[str, Any]] = []
    all_contamination: list[dict[str, Any]] = []

    for stage_index, seed in enumerate(seeds, start=1):
        hidden = _hidden_descriptor(seed)
        training_inputs, heldout_inputs = _inputs(str(hidden["input_domain"]))
        demonstrations = tuple(
            ProgramExample(value, execute_program(hidden, value)) for value in training_inputs
        )
        heldout_expected = tuple(execute_program(hidden, value) for value in heldout_inputs)
        commitment_payload = {
            "program": hidden,
            "heldout": [
                {"input": value, "expected": expected}
                for value, expected in zip(heldout_inputs, heldout_expected, strict=True)
            ],
            "stage": stage_index,
        }
        commitment = _digest(commitment_payload)
        token = commitment[:16]
        scope = f"{ACQUIRED_CONTINUAL_SCOPE_PREFIX}/stage-{stage_index}"
        candidate_id = f"acquired-continual-{stage_index}-{token}"
        tool_id = f"acquired-continual-tool-{stage_index}-{token}"

        training_digests = {_digest(item.input) for item in demonstrations}
        heldout_digests = {_digest(value) for value in heldout_inputs}
        overlap = sorted(training_digests & heldout_digests)
        contamination = [
            {
                "stage": stage_index,
                "type": "heldout_input_overlap",
                "input_digests": overlap,
            }
        ] if overlap else []
        all_contamination.extend(contamination)

        learned = synthesize_program(
            input_domain=str(hidden["input_domain"]),
            output_domain=str(hidden["output_domain"]),
            examples=demonstrations,
            max_nodes=8,
        )
        candidate_dir = root / ".continual" / "candidates" / candidate_id
        candidate_path = candidate_dir / "candidate.json"
        candidate = acquired_program_candidate_payload(
            candidate_id=candidate_id,
            tool_id=tool_id,
            scope=scope,
            program=learned.descriptor(),
            description="Sequential hidden acquired-program capability with prior-skill retention gate.",
        )
        candidate["supporting_evidence"] = [
            {
                "type": "evaluator_commitment",
                "commitment_sha256": commitment,
                "training_support_sha256": learned.support_sha256,
                "heldout_answers_exposed_to_learner": False,
                "stage": stage_index,
            }
        ]
        if candidate_path.exists():
            existing = json.loads(candidate_path.read_text(encoding="utf-8"))
            if not isinstance(existing, Mapping) or _stable_candidate(existing) != _stable_candidate(candidate):
                raise ValueError("existing continual acquired-program Candidate conflicts with campaign")
        else:
            _atomic_json(candidate_path, candidate)
        if AcquiredProgramRegistry(root).descriptors(scope):
            raise RuntimeError("new stage became callable before deterministic regression")

        baseline_engine = engine_factory(root)
        baseline_run_id = _new_runtime_run(baseline_engine)
        baseline: list[dict[str, Any]] = []
        candidate_measurements: list[dict[str, Any]] = []
        target_id = f"withheld-acquired-continual-stage-{stage_index}"
        for repeat, (query, expected) in enumerate(
            zip(heldout_inputs, heldout_expected, strict=True)
        ):
            pre_solved = False
            try:
                _runtime_call_scope(
                    baseline_engine,
                    baseline_run_id,
                    scope=scope,
                    tool_id=tool_id,
                    value=query,
                )
                pre_solved = True
            except (LearnedToolError, AcquiredProgramToolError):
                pre_solved = False
            artifact = {
                "query_sha256": _digest(query),
                "expected_sha256": _digest(expected),
                "commitment_sha256": commitment,
                "stage": stage_index,
            }
            baseline.append(
                _measurement(
                    task_id=target_id,
                    repeat=repeat,
                    score=1.0 if pre_solved else 0.0,
                    artifact={**artifact, "phase": "pre-promotion-runtime"},
                    domain="acquired-program:hidden-accumulation",
                )
            )
            answer = learned.apply(query)
            candidate_measurements.append(
                _measurement(
                    task_id=target_id,
                    repeat=repeat,
                    score=1.0 if answer == expected else 0.0,
                    artifact={
                        **artifact,
                        "phase": "isolated-candidate-sandbox",
                        "answer_sha256": _digest(answer),
                    },
                    domain="acquired-program:hidden-accumulation",
                )
            )

        prior_retests: list[dict[str, Any]] = []
        protected_baseline: list[dict[str, Any]] = [
            _measurement(
                task_id="protected-core-runtime",
                repeat=0,
                score=1.0,
                artifact={"invariant": "core runtime remains available", "stage": stage_index},
                domain="protected",
            )
        ]
        protected_valid = list(protected_baseline)
        for prior_index, prior in enumerate(stage_specs):
            retained = _retest_stage(root, prior, engine_factory=engine_factory)
            prior_retests.append(
                {
                    "candidate_id": retained["candidate_id"],
                    "scope": retained["scope"],
                    "score": retained["score"],
                    "result_count": len(retained["results"]),
                }
            )
            protected = _measurement(
                task_id=f"protected-prior-capability-{prior_index + 1}",
                repeat=0,
                score=retained["score"],
                artifact={
                    "candidate_id": retained["candidate_id"],
                    "scope": retained["scope"],
                    "result_digests": [_digest(item) for item in retained["results"]],
                    "stage": stage_index,
                },
                domain="protected-acquired-program",
            )
            protected_baseline.append(protected)
            protected_valid.append(protected)

        baseline_with_protected = [*baseline, *protected_baseline]
        adverse_protected = [dict(item) for item in protected_valid]
        adverse_protected[0] = {
            **adverse_protected[0],
            "passed": False,
            "score": 0.0,
            "artifact_sha256": _digest({"forced_regression_probe": True, "stage": stage_index}),
        }
        adverse_trial = [*candidate_measurements, *adverse_protected]
        valid_trial = [*candidate_measurements, *protected_valid]
        regression_dir = candidate_dir / "regression-input"
        baseline_path = regression_dir / "baseline.json"
        adverse_path = regression_dir / "candidate-adverse.json"
        valid_path = regression_dir / "candidate-valid.json"
        _atomic_json(baseline_path, baseline_with_protected)
        _atomic_json(adverse_path, adverse_trial)
        _atomic_json(valid_path, valid_trial)

        failed = record_candidate_regression(
            root,
            candidate_id=candidate_id,
            scope=scope,
            baseline_path=baseline_path,
            candidate_path=adverse_path,
            target_task_ids=[target_id],
        )
        if failed["decision"]["adopt_candidate"]:
            raise RuntimeError("forced protected regression was incorrectly adopted")
        if AcquiredProgramRegistry(root).descriptors(scope):
            raise RuntimeError("failed stage regression exposed Candidate")
        promoted = record_candidate_regression(
            root,
            candidate_id=candidate_id,
            scope=scope,
            baseline_path=baseline_path,
            candidate_path=valid_path,
            target_task_ids=[target_id],
        )
        if not promoted["decision"]["adopt_candidate"]:
            raise RuntimeError("valid accumulation stage failed deterministic regression")

        stage_spec = {
            "stage": stage_index,
            "seed_ephemeral": seed,
            "seed_commitment": _digest({"seed": seed, "stage": stage_index}),
            "evaluator_commitment": commitment,
            "candidate_id": candidate_id,
            "tool_id": tool_id,
            "scope": scope,
            "input_domain": hidden["input_domain"],
            "output_domain": hidden["output_domain"],
        }
        stage_specs.append(stage_spec)

        retained_after = [
            _retest_stage(root, item, engine_factory=engine_factory) for item in stage_specs
        ]
        all_retained = all(item["score"] == 1.0 for item in retained_after)
        state = json.loads(candidate_path.read_text(encoding="utf-8"))
        stage_reports.append(
            {
                "stage": stage_index,
                "seed_commitment": stage_spec["seed_commitment"],
                "evaluator_commitment": commitment,
                "candidate_id": candidate_id,
                "tool_id": tool_id,
                "scope": scope,
                "input_domain": hidden["input_domain"],
                "output_domain": hidden["output_domain"],
                "baseline_score": sum(item["score"] for item in baseline) / len(baseline),
                "candidate_score": sum(item["score"] for item in candidate_measurements) / len(candidate_measurements),
                "prior_retests_before_promotion": prior_retests,
                "forced_regression_rejected": failed["decision"]["adopt_candidate"] is False,
                "promotion_adopted": promoted["decision"]["adopt_candidate"] is True,
                "negative_evidence_retained": bool(state.get("contradictory_evidence")),
                "retained_after_stage": [
                    {
                        "candidate_id": item["candidate_id"],
                        "scope": item["scope"],
                        "score": item["score"],
                    }
                    for item in retained_after
                ],
                "all_retained_after_stage": all_retained,
                "trial_attempts": [failed.get("trial"), promoted.get("trial")],
                "contamination_events": contamination,
            }
        )

    final_engine = engine_factory(root)
    final_catalog = final_engine._verified_tool_catalog()
    final_retests = [
        _retest_stage(root, stage, engine_factory=engine_factory) for stage in stage_specs
    ]
    report = {
        "schema_version": 1,
        "campaign_id": f"acquired-continual-{_digest([item['evaluator_commitment'] for item in stage_specs])[:16]}",
        "stage_count": len(stage_reports),
        "seed_values_persisted": False,
        "heldout_answers_exposed_to_learner": False,
        "contamination_events": all_contamination,
        "stage_reports": stage_reports,
        "final_catalog": [
            {
                "tool_id": item["tool_id"],
                "candidate_id": item["candidate_id"],
                "scope": item["scope"],
                "program_kind": item.get("program_kind"),
                "input_domain": item.get("input_domain"),
                "output_domain": item.get("output_domain"),
            }
            for item in final_catalog
            if str(item.get("scope", "")).startswith(ACQUIRED_CONTINUAL_SCOPE_PREFIX)
        ],
        "final_retests": [
            {
                "candidate_id": item["candidate_id"],
                "scope": item["scope"],
                "score": item["score"],
            }
            for item in final_retests
        ],
        "evidence_tier": "development",
        "claim_boundary": (
            "This demonstrates bounded continual accumulation and retention across multiple fresh Engine "
            "instances under internally hidden tasks. The evaluator, task generator, grammar, and evidence "
            "remain internal and therefore do not constitute independent production AGI evidence."
        ),
    }
    report["passed"] = (
        not all_contamination
        and len(report["final_catalog"]) == len(stage_reports)
        and all(item["baseline_score"] == 0.0 for item in stage_reports)
        and all(item["candidate_score"] == 1.0 for item in stage_reports)
        and all(item["forced_regression_rejected"] for item in stage_reports)
        and all(item["promotion_adopted"] for item in stage_reports)
        and all(item["negative_evidence_retained"] for item in stage_reports)
        and all(item["all_retained_after_stage"] for item in stage_reports)
        and all(item["score"] == 1.0 for item in report["final_retests"])
    )
    report["digest"] = _digest(report)
    evidence_path = (
        root
        / ".continual"
        / "evidence"
        / "acquired-program-continual"
        / f"{report['campaign_id']}.json"
    )
    _atomic_json(evidence_path, report)
    return report
