from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from continual.candidate_regression import record_candidate_regression
from continual.learned_tools import (
    LearnedToolError,
    LearnedToolRegistry,
    typed_learned_tool_candidate_payload,
)

from .induction import Demonstration, InductionError
from .typed_composition import TypedCompositionalLearner, default_typed_primitives


TYPED_EXECUTION_SCOPE = "agi/learned-tool-execution/typed-text-to-count-v1"


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


def _apply_program(input_domain: str, program: Sequence[str], value: Any) -> tuple[str, Any]:
    index = {item.name: item for item in default_typed_primitives()}
    current_domain = input_domain
    current = value
    for name in program:
        primitive = index[name]
        if primitive.input_domain != current_domain:
            raise ValueError(
                f"ill-typed reference program: expected {current_domain}, got {primitive.input_domain}"
            )
        current = primitive.function(current)
        current_domain = primitive.output_domain
    return current_domain, current


def _measurement(task_id: str, repeat: int, score: float, artifact: Any) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "criterion": "self_improvement",
        "domain": "typed:string-to-numeric",
        "repeat_index": repeat,
        "passed": score == 1.0,
        "score": score,
        "artifact_sha256": _digest(artifact),
    }


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


def run_typed_learned_tool_execution_campaign(root: Path, seed: str) -> dict[str, Any]:
    """Learn, regress, activate, and execute a typed string-to-numeric tool.

    The target requires five primitive operations and crosses string -> sequence -> numeric. Raw
    synthesis is constrained to two terms and cannot solve it. Recursive learned macros make the
    deeper behavior reachable under that same budget. The declarative typed program remains
    unavailable until deterministic exact-scope regression passes; the real continual registry then
    revalidates the type chain and executes it mechanically.
    """

    if not isinstance(seed, str) or not seed:
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    token = _digest({"seed": seed, "campaign": "typed-learned-tool-execution-v1"})[:16]
    candidate_id = f"typed-learned-tool-{token}"
    tool_id = f"typed-unique-count-{token}"

    learner = TypedCompositionalLearner()
    base_program = ("string.reverse", "string.append_hash")
    chars_program = base_program + ("string.to_chars",)
    unique_program = chars_program + ("sequence.unique",)
    target_program = unique_program + ("sequence.length",)

    base_inputs = ("Abc", "ModelM", "LevelX")
    base = learner.learn(
        "base",
        "string",
        tuple(
            Demonstration(value, _apply_program("string", base_program, value)[1])
            for value in base_inputs
        ),
        max_terms=2,
        allow_macros=False,
    )
    chars_inputs = ("One", "Two7", "Three")
    chars = learner.learn(
        "chars",
        "string",
        tuple(
            Demonstration(value, _apply_program("string", chars_program, value)[1])
            for value in chars_inputs
        ),
        max_terms=2,
        allow_macros=True,
    )
    repeated_inputs = ("Aba", "ModelM", "LevelX", "Mississippi")
    unique = learner.learn(
        "unique",
        "string",
        tuple(
            Demonstration(value, _apply_program("string", unique_program, value)[1])
            for value in repeated_inputs
        ),
        max_terms=2,
        allow_macros=True,
    )
    target_demos = tuple(
        Demonstration(value, _apply_program("string", target_program, value)[1])
        for value in repeated_inputs
    )

    raw_probe = TypedCompositionalLearner()
    pre_learning_solved = True
    try:
        raw_probe.learn(
            "target-before-learning",
            "string",
            target_demos,
            max_terms=2,
            allow_macros=False,
        )
    except InductionError:
        pre_learning_solved = False
    if pre_learning_solved:
        raise RuntimeError("typed target unexpectedly fits the raw two-term budget")

    learned = learner.learn(
        "unique-count",
        "string",
        target_demos,
        max_terms=2,
        allow_macros=True,
    )
    if (
        learned.output_domain != "numeric"
        or learned.expanded_primitives != target_program
        or "skill:unique" not in learned.terms
    ):
        raise RuntimeError("typed target was not acquired by recursive learned-macro reuse")

    candidate_dir = root / ".continual" / "candidates" / candidate_id
    candidate_path = candidate_dir / "candidate.json"
    candidate = typed_learned_tool_candidate_payload(
        candidate_id=candidate_id,
        tool_id=tool_id,
        scope=TYPED_EXECUTION_SCOPE,
        input_domain="string",
        output_domain="numeric",
        expanded_primitives=learned.expanded_primitives,
        support_sha256=learned.support_sha256,
        description="Count unique characters after an acquired text normalization procedure.",
    )
    candidate["supporting_evidence"] = [
        {
            "type": "recursive_typed_macro_support",
            "term_budget": 2,
            "expanded_depth": len(target_program),
            "base_support_sha256": base.support_sha256,
            "chars_support_sha256": chars.support_sha256,
            "unique_support_sha256": unique.support_sha256,
            "derived_support_sha256": learned.support_sha256,
        }
    ]
    if candidate_path.exists():
        existing = json.loads(candidate_path.read_text(encoding="utf-8"))
        if not isinstance(existing, Mapping) or _stable_candidate(existing) != _stable_candidate(candidate):
            raise ValueError("existing typed learned-tool candidate conflicts with this campaign")
    else:
        _atomic_json(candidate_path, candidate)

    registry_before = LearnedToolRegistry(root)
    inactive_before_regression = registry_before.descriptors(TYPED_EXECUTION_SCOPE) == ()
    if not inactive_before_regression:
        raise RuntimeError("typed learned tool became callable before deterministic regression")

    heldout_queries = ("Bookkeeper", "Tennessee", "Assessment")
    baseline: list[dict[str, Any]] = []
    trial: list[dict[str, Any]] = []
    learned_answers: list[dict[str, Any]] = []
    for repeat, query in enumerate(heldout_queries):
        output_domain, expected = _apply_program("string", target_program, query)
        answer = learner.apply("unique-count", query)
        success = output_domain == "numeric" and answer == expected
        artifact = {
            "query_sha256": _digest(query),
            "expected_sha256": _digest(expected),
            "input_domain": "string",
            "output_domain": output_domain,
            "expanded_depth": len(target_program),
            "term_budget": 2,
            "pre_learning_raw_solution": False,
        }
        baseline.append(_measurement("withheld-typed-execution-transfer", repeat, 0.0, artifact))
        trial.append(
            _measurement(
                "withheld-typed-execution-transfer",
                repeat,
                1.0 if success else 0.0,
                artifact,
            )
        )
        learned_answers.append(
            {"query": query, "expected": expected, "answer": answer, "success": success}
        )

    protected_query = "RetainMe"
    protected_expected = learner.apply("base", protected_query)
    protected_artifact = {
        "query_sha256": _digest(protected_query),
        "expected_sha256": _digest(protected_expected),
        "kind": "protected-typed-base-retention",
    }
    baseline.append(_measurement("protected-typed-base-retention", 0, 1.0, protected_artifact))
    trial.append(
        _measurement(
            "protected-typed-base-retention",
            0,
            1.0 if learner.apply("base", protected_query) == protected_expected else 0.0,
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
        scope=TYPED_EXECUTION_SCOPE,
        baseline_path=baseline_path,
        candidate_path=trial_path,
        target_task_ids=["withheld-typed-execution-transfer"],
    )

    registry = LearnedToolRegistry(root)
    descriptors = registry.descriptors(TYPED_EXECUTION_SCOPE)
    if len(descriptors) != 1 or descriptors[0]["tool_id"] != tool_id:
        raise RuntimeError("verified typed learned tool was not exposed for its exact scope")
    runtime_results: list[dict[str, Any]] = []
    for query in heldout_queries:
        expected = _apply_program("string", target_program, query)[1]
        answer = registry.apply(scope=TYPED_EXECUTION_SCOPE, tool_id=tool_id, value=query)
        runtime_results.append(
            {
                "query": query,
                "expected": expected,
                "answer": answer,
                "output_is_numeric": isinstance(answer, int) and not isinstance(answer, bool),
                "success": answer == expected,
            }
        )

    rejected_wrong_type = False
    try:
        registry.apply(scope=TYPED_EXECUTION_SCOPE, tool_id=tool_id, value=["not", "text"])
    except LearnedToolError:
        rejected_wrong_type = True

    payload = {
        "candidate_id": candidate_id,
        "tool_id": tool_id,
        "scope": TYPED_EXECUTION_SCOPE,
        "input_domain": "string",
        "output_domain": "numeric",
        "term_budget": 2,
        "expanded_depth": len(target_program),
        "pre_learning_solved": pre_learning_solved,
        "macro_chain": ["skill:base", "skill:chars", "skill:unique"],
        "inactive_before_regression": inactive_before_regression,
        "promotion": promotion,
        "available_tool": descriptors[0],
        "learned_answers": learned_answers,
        "runtime_results": runtime_results,
        "rejected_wrong_type": rejected_wrong_type,
    }
    return {
        **payload,
        "passed": (
            not pre_learning_solved
            and inactive_before_regression
            and bool(promotion["decision"]["adopt_candidate"])
            and all(item["success"] for item in learned_answers)
            and all(item["success"] and item["output_is_numeric"] for item in runtime_results)
            and rejected_wrong_type
        ),
        "digest": _digest(payload),
        "evidence_tier": "development",
        "claim_boundary": (
            "This demonstrates typed cross-domain capability growth reaching the real scoped execution "
            "path after deterministic regression. The primitive graph and task family remain bounded; "
            "the result is not open-ended learning or AGI evidence."
        ),
    }
