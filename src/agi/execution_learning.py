from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from continual.candidate_regression import record_candidate_regression
from continual.learned_tools import LearnedToolRegistry, learned_tool_candidate_payload

from .compositional import CompositionalLearner, default_primitives
from .induction import Demonstration, InductionError


EXECUTION_SCOPE = "agi/learned-tool-execution/string-transform-v1"


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _apply_program(domain: str, program: Sequence[str], value: Any) -> Any:
    index = {(item.domain, item.name): item for item in default_primitives()}
    current = value
    for name in program:
        current = index[(domain, name)].function(current)
    return current


def _measurement(task_id: str, repeat: int, score: float, artifact: Any) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "criterion": "self_improvement",
        "domain": "string",
        "repeat_index": repeat,
        "passed": score == 1.0,
        "score": score,
        "artifact_sha256": _digest(artifact),
    }


def run_learned_tool_execution_campaign(root: Path, seed: str) -> dict[str, Any]:
    """Promote a learned macro only after repeated before/after execution evidence.

    The target behavior needs three primitive operations. With a fixed two-term synthesis budget it
    is not learnable from raw primitives. After a two-step base skill is learned, the same two-term
    budget can acquire the deeper behavior by using that base as a macro. The resulting tool is
    written as an inactive Candidate, trialed on evaluator-held queries, passed through the
    deterministic protected-baseline regression gate, and only then becomes callable from the
    scope-gated LearnedToolRegistry.
    """

    if not isinstance(seed, str) or not seed:
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    token = _digest({"seed": seed, "campaign": "learned-tool-execution-v1"})[:16]
    candidate_id = f"learned-tool-{token}"
    tool_id = f"composed-string-{token}"

    base_program = ("reverse", "append_hash")
    target_program = base_program + ("duplicate",)
    base_demos = (
        Demonstration("Abc", _apply_program("string", base_program, "Abc")),
        Demonstration("qRs", _apply_program("string", base_program, "qRs")),
        Demonstration("Model7", _apply_program("string", base_program, "Model7")),
    )
    target_training_inputs = ("Alpha", "bEtA", "Gamma9")
    target_demos = tuple(
        Demonstration(value, _apply_program("string", target_program, value))
        for value in target_training_inputs
    )

    probe = CompositionalLearner()
    pre_learning_solved = True
    try:
        probe.learn(
            "target-before-learning",
            "string",
            target_demos,
            max_terms=2,
            allow_macros=False,
        )
    except InductionError:
        pre_learning_solved = False
    if pre_learning_solved:
        raise RuntimeError("target unexpectedly fits the raw two-term primitive budget")

    learner = CompositionalLearner()
    base = learner.learn(
        "base-macro",
        "string",
        base_demos,
        max_terms=2,
        allow_macros=False,
    )
    learned = learner.learn(
        "derived-tool",
        "string",
        target_demos,
        max_terms=2,
        allow_macros=True,
    )
    if learned.expanded_primitives != target_program or "skill:base-macro" not in learned.terms:
        raise RuntimeError("deeper behavior was not acquired through learned macro reuse")

    candidate_dir = root / ".continual" / "candidates" / candidate_id
    candidate_path = candidate_dir / "candidate.json"
    candidate = learned_tool_candidate_payload(
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=EXECUTION_SCOPE,
        domain="string",
        expanded_primitives=learned.expanded_primitives,
        support_sha256=learned.support_sha256,
        description="Apply a learned string transformation acquired from demonstrations.",
    )
    candidate["supporting_evidence"] = [
        {
            "type": "learned_macro_support",
            "base_support_sha256": base.support_sha256,
            "derived_support_sha256": learned.support_sha256,
            "term_budget": 2,
        }
    ]
    if candidate_path.exists():
        existing = json.loads(candidate_path.read_text(encoding="utf-8"))
        stable_existing = dict(existing)
        for key in (
            "status",
            "scope_states",
            "verified_scope_states",
            "regression_decision_refs",
            "supporting_evidence",
            "contradictory_evidence",
        ):
            stable_existing.pop(key, None)
        stable_candidate = dict(candidate)
        for key in (
            "status",
            "scope_states",
            "verified_scope_states",
            "regression_decision_refs",
            "supporting_evidence",
            "contradictory_evidence",
        ):
            stable_candidate.pop(key, None)
        if stable_existing != stable_candidate:
            raise ValueError("existing learned-tool candidate conflicts with this campaign")
    else:
        _atomic_json(candidate_path, candidate)

    registry_before = LearnedToolRegistry(root)
    inactive_before_regression = registry_before.descriptors(EXECUTION_SCOPE) == ()
    if not inactive_before_regression:
        raise RuntimeError("new learned tool became callable before deterministic regression promotion")

    heldout_queries = ("Transfer", "NovelQ")
    baseline: list[dict[str, Any]] = []
    trial: list[dict[str, Any]] = []
    for repeat, query in enumerate(heldout_queries):
        expected = _apply_program("string", target_program, query)
        answer = learner.apply("derived-tool", query)
        success = answer == expected
        artifact = {
            "query_sha256": _digest(query),
            "expected_sha256": _digest(expected),
            "term_budget": 2,
            "pre_learning_raw_solution": False,
        }
        baseline.append(_measurement("withheld-execution-transfer", repeat, 0.0, artifact))
        trial.append(_measurement("withheld-execution-transfer", repeat, 1.0 if success else 0.0, artifact))

    protected_query = "RetainMe"
    protected_expected = learner.apply("base-macro", protected_query)
    protected_artifact = {
        "query_sha256": _digest(protected_query),
        "expected_sha256": _digest(protected_expected),
        "kind": "protected-base-retention",
    }
    baseline.append(_measurement("protected-base-retention", 0, 1.0, protected_artifact))
    trial.append(
        _measurement(
            "protected-base-retention",
            0,
            1.0 if learner.apply("base-macro", protected_query) == protected_expected else 0.0,
            protected_artifact,
        )
    )

    baseline_path = candidate_dir / "regression-input" / "baseline.json"
    trial_path = candidate_dir / "regression-input" / "candidate.json"
    _atomic_json(baseline_path, baseline)
    _atomic_json(trial_path, trial)
    promotion = record_candidate_regression(
        root,
        candidate_id=candidate_id,
        scope=EXECUTION_SCOPE,
        baseline_path=baseline_path,
        candidate_path=trial_path,
        target_task_ids=["withheld-execution-transfer"],
    )

    registry = LearnedToolRegistry(root)
    descriptors = registry.descriptors(EXECUTION_SCOPE)
    if len(descriptors) != 1 or descriptors[0]["tool_id"] != tool_id:
        raise RuntimeError("verified learned tool was not exposed for its exact scope")
    post_activation_results = []
    for query in heldout_queries:
        expected = _apply_program("string", target_program, query)
        answer = registry.apply(scope=EXECUTION_SCOPE, tool_id=tool_id, value=query)
        post_activation_results.append(
            {
                "query": query,
                "expected": expected,
                "answer": answer,
                "success": answer == expected,
            }
        )

    payload = {
        "candidate_id": candidate_id,
        "tool_id": tool_id,
        "scope": EXECUTION_SCOPE,
        "term_budget": 2,
        "pre_learning_solved": pre_learning_solved,
        "macro_reused": "skill:base-macro" in learned.terms,
        "inactive_before_regression": inactive_before_regression,
        "promotion": promotion,
        "available_tool": descriptors[0],
        "post_activation_results": post_activation_results,
    }
    return {
        **payload,
        "passed": (
            not pre_learning_solved
            and payload["macro_reused"]
            and inactive_before_regression
            and bool(promotion["decision"]["adopt_candidate"])
            and all(item["success"] for item in post_activation_results)
        ),
        "digest": _digest(payload),
        "evidence_tier": "development",
        "claim_boundary": (
            "This demonstrates scoped capability growth and deterministic activation of one bounded "
            "learned tool. It does not establish open-ended tool acquisition, broad transfer, or AGI."
        ),
    }
