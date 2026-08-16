from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Callable, Sequence

from .induction import Demonstration, InductionError
from .regression import compare_snapshots


@dataclass(frozen=True)
class TypedPrimitive:
    name: str
    input_domain: str
    output_domain: str
    function: Callable[[Any], Any]


@dataclass(frozen=True)
class TypedSkill:
    skill_id: str
    input_domain: str
    output_domain: str
    terms: tuple[str, ...]
    expanded_primitives: tuple[str, ...]
    support_sha256: str


@dataclass(frozen=True)
class TypedCompositionResult:
    skill_id: str
    repeat_index: int
    query: Any
    expected: Any
    answer: Any
    success: bool


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def default_typed_primitives() -> tuple[TypedPrimitive, ...]:
    return (
        TypedPrimitive("string.reverse", "string", "string", lambda value: str(value)[::-1]),
        TypedPrimitive("string.append_hash", "string", "string", lambda value: str(value) + "#"),
        TypedPrimitive("string.to_chars", "string", "sequence", lambda value: list(str(value))),
        TypedPrimitive("string.length", "string", "numeric", lambda value: len(str(value))),
        TypedPrimitive("sequence.reverse", "sequence", "sequence", lambda value: list(value)[::-1]),
        TypedPrimitive("sequence.unique", "sequence", "sequence", lambda value: list(dict.fromkeys(value))),
        TypedPrimitive("sequence.length", "sequence", "numeric", lambda value: len(list(value))),
        TypedPrimitive("sequence.join", "sequence", "string", lambda value: "".join(str(item) for item in value)),
        TypedPrimitive("numeric.times2", "numeric", "numeric", lambda value: int(value) * 2),
        TypedPrimitive("numeric.plus3", "numeric", "numeric", lambda value: int(value) + 3),
        TypedPrimitive("numeric.to_string", "numeric", "string", lambda value: str(int(value))),
    )


class TypedCompositionalLearner:
    """Synthesize well-typed procedures and reuse earlier skills across domain boundaries.

    Each primitive and learned macro has an explicit input/output domain. Search enumerates only
    well-typed term chains. A macro counts as one search term even when it expands to several
    primitives, so an acquired skill can make a deeper cross-domain behavior reachable without
    raising the current term budget. Updates fail closed if demonstrations are ambiguous or do not
    identify a unique minimal expanded program.
    """

    def __init__(self, primitives: Sequence[TypedPrimitive] | None = None) -> None:
        values = tuple(primitives or default_typed_primitives())
        index: dict[str, TypedPrimitive] = {}
        for primitive in values:
            if not primitive.name or primitive.name in index:
                raise ValueError(f"duplicate or empty typed primitive: {primitive.name}")
            if not primitive.input_domain or not primitive.output_domain:
                raise ValueError(f"typed primitive domains must be non-empty: {primitive.name}")
            index[primitive.name] = primitive
        self._primitive_index = index
        self._skills: dict[str, TypedSkill] = {}

    @property
    def skills(self) -> tuple[TypedSkill, ...]:
        return tuple(self._skills[key] for key in sorted(self._skills))

    def _apply_expanded(self, input_domain: str, expanded: Sequence[str], value: Any) -> tuple[str, Any]:
        current_domain = input_domain
        current = value
        for primitive_name in expanded:
            primitive = self._primitive_index.get(primitive_name)
            if primitive is None:
                raise InductionError(f"unknown typed primitive: {primitive_name}")
            if primitive.input_domain != current_domain:
                raise InductionError(
                    f"ill-typed primitive chain: expected {current_domain}, got {primitive.input_domain}"
                )
            current = primitive.function(current)
            current_domain = primitive.output_domain
        return current_domain, current

    def _choices(
        self,
        current_domain: str,
        *,
        allow_macros: bool,
        exclude_skill_id: str,
    ) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
        choices: list[tuple[str, str, tuple[str, ...]]] = []
        for primitive in sorted(self._primitive_index.values(), key=lambda item: item.name):
            if primitive.input_domain == current_domain:
                choices.append(
                    (
                        f"primitive:{primitive.name}",
                        primitive.output_domain,
                        (primitive.name,),
                    )
                )
        if allow_macros:
            for skill in self.skills:
                if skill.skill_id == exclude_skill_id or skill.input_domain != current_domain:
                    continue
                choices.append(
                    (
                        f"skill:{skill.skill_id}",
                        skill.output_domain,
                        skill.expanded_primitives,
                    )
                )
        return tuple(choices)

    def _term_chains(
        self,
        input_domain: str,
        term_count: int,
        *,
        allow_macros: bool,
        exclude_skill_id: str,
    ) -> tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...]:
        results: list[tuple[tuple[str, ...], tuple[str, ...], str]] = []

        def visit(
            current_domain: str,
            remaining: int,
            terms: tuple[str, ...],
            expanded: tuple[str, ...],
        ) -> None:
            if remaining == 0:
                results.append((terms, expanded, current_domain))
                return
            for term, output_domain, term_expanded in self._choices(
                current_domain,
                allow_macros=allow_macros,
                exclude_skill_id=exclude_skill_id,
            ):
                visit(
                    output_domain,
                    remaining - 1,
                    terms + (term,),
                    expanded + term_expanded,
                )

        visit(input_domain, term_count, (), ())
        return tuple(results)

    def learn(
        self,
        skill_id: str,
        input_domain: str,
        demonstrations: Sequence[Demonstration],
        *,
        max_terms: int = 2,
        allow_macros: bool = True,
    ) -> TypedSkill:
        if not skill_id:
            raise InductionError("skill_id must be non-empty")
        if not input_domain:
            raise InductionError("input_domain must be non-empty")
        if len(demonstrations) < 2:
            raise InductionError("at least two demonstrations are required")
        if max_terms < 1:
            raise InductionError("max_terms must be positive")

        selected: dict[tuple[tuple[str, ...], str], tuple[str, ...]] | None = None
        for term_count in range(1, max_terms + 1):
            matches: dict[tuple[tuple[str, ...], str], tuple[str, ...]] = {}
            for terms, expanded, output_domain in self._term_chains(
                input_domain,
                term_count,
                allow_macros=allow_macros,
                exclude_skill_id=skill_id,
            ):
                key = (expanded, output_domain)
                if key in matches:
                    continue
                try:
                    if all(
                        self._apply_expanded(input_domain, expanded, item.input)[1] == item.output
                        for item in demonstrations
                    ):
                        matches[key] = terms
                except (TypeError, ValueError, OverflowError, InductionError):
                    continue
            if matches:
                selected = matches
                break

        if not selected:
            raise InductionError("no well-typed composition matches all demonstrations")
        if len(selected) != 1:
            raise InductionError("demonstrations are ambiguous across multiple minimal typed programs")
        (expanded, output_domain), terms = next(iter(selected.items()))
        support = [
            {"input": item.input, "output": item.output}
            for item in demonstrations
        ]
        learned = TypedSkill(
            skill_id=skill_id,
            input_domain=input_domain,
            output_domain=output_domain,
            terms=terms,
            expanded_primitives=expanded,
            support_sha256=_digest(support),
        )
        self._skills[skill_id] = learned
        return learned

    def apply(self, skill_id: str, value: Any) -> Any:
        skill = self._skills.get(skill_id)
        if skill is None:
            raise InductionError(f"unknown typed skill: {skill_id}")
        output_domain, answer = self._apply_expanded(
            skill.input_domain,
            skill.expanded_primitives,
            value,
        )
        if output_domain != skill.output_domain:
            raise InductionError("stored typed skill output domain does not match expanded program")
        return answer


def _measurement(task_id: str, domain: str, repeat: int, score: float, artifact: Any) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "criterion": "self_improvement",
        "domain": domain,
        "repeat_index": repeat,
        "passed": score == 1.0,
        "score": score,
        "artifact_sha256": _digest(artifact),
    }


def run_typed_cross_domain_campaign(seed: str) -> dict[str, Any]:
    """Measure whether learned macros expand solvable depth across type boundaries."""

    if not seed:
        raise ValueError("seed must be non-empty")
    seed_commitment = _digest({"seed": seed, "suite": "typed-cross-domain-composition-v1"})
    learner = TypedCompositionalLearner()

    base_program = ("string.reverse", "string.append_hash")
    base_inputs = ("Abc", "qRSt", "Model7")
    base_demos = tuple(
        Demonstration(value, learner._apply_expanded("string", base_program, value)[1])
        for value in base_inputs
    )
    base = learner.learn("text-base", "string", base_demos, max_terms=2, allow_macros=False)
    if base.expanded_primitives != base_program:
        raise RuntimeError("base typed skill was not uniquely identified")

    targets = {
        "text-to-length": (
            base_program + ("string.length",),
            ("Alpha", "bEtA", "Gamma9"),
            ("HeldOut", "xYz9"),
        ),
        "text-to-chars": (
            base_program + ("string.to_chars",),
            ("One", "Two7", "Three"),
            ("Transfer", "NovelQ"),
        ),
    }

    raw_budget_failures: dict[str, bool] = {}
    for skill_id, (program, train_inputs, _) in targets.items():
        demos = tuple(
            Demonstration(value, learner._apply_expanded("string", program, value)[1])
            for value in train_inputs
        )
        probe = TypedCompositionalLearner()
        try:
            probe.learn(skill_id, "string", demos, max_terms=2, allow_macros=False)
            raw_budget_failures[skill_id] = False
        except InductionError:
            raw_budget_failures[skill_id] = True
        learned = learner.learn(skill_id, "string", demos, max_terms=2, allow_macros=True)
        if learned.expanded_primitives != program or "skill:text-base" not in learned.terms:
            raise RuntimeError(f"{skill_id} did not reuse text-base across the type boundary")

    deep_program = base_program + ("string.to_chars", "sequence.unique")
    deep_train = ("Aba", "ModelM", "LevelX")
    deep_demos = tuple(
        Demonstration(value, learner._apply_expanded("string", deep_program, value)[1])
        for value in deep_train
    )
    raw_deep_probe = TypedCompositionalLearner()
    try:
        raw_deep_probe.learn(
            "text-to-unique-chars",
            "string",
            deep_demos,
            max_terms=2,
            allow_macros=False,
        )
        raw_budget_failures["text-to-unique-chars"] = False
    except InductionError:
        raw_budget_failures["text-to-unique-chars"] = True
    deep = learner.learn(
        "text-to-unique-chars",
        "string",
        deep_demos,
        max_terms=2,
        allow_macros=True,
    )
    if deep.expanded_primitives != deep_program or "skill:text-to-chars" not in deep.terms:
        raise RuntimeError("recursive typed macro reuse did not expand solvable program depth")

    queries = {
        "text-to-length": targets["text-to-length"][2],
        "text-to-chars": targets["text-to-chars"][2],
        "text-to-unique-chars": ("Mississippi", "BananaBand"),
    }
    expected_programs = {
        "text-to-length": targets["text-to-length"][0],
        "text-to-chars": targets["text-to-chars"][0],
        "text-to-unique-chars": deep_program,
    }
    results: list[TypedCompositionResult] = []
    baseline: list[dict[str, Any]] = []
    candidate: list[dict[str, Any]] = []
    for skill_id, values in queries.items():
        skill = next(item for item in learner.skills if item.skill_id == skill_id)
        program = expected_programs[skill_id]
        for repeat, query in enumerate(values):
            expected = learner._apply_expanded("string", program, query)[1]
            answer = learner.apply(skill_id, query)
            success = answer == expected
            results.append(TypedCompositionResult(skill_id, repeat, query, expected, answer, success))
            artifact = {
                "query_sha256": _digest(query),
                "expected_sha256": _digest(expected),
                "output_domain": skill.output_domain,
                "expanded_depth": len(program),
            }
            baseline.append(_measurement(skill_id, skill.output_domain, repeat, 0.0, artifact))
            candidate.append(_measurement(skill_id, skill.output_domain, repeat, 1.0 if success else 0.0, artifact))

    protected_query = "RetainMe"
    protected_expected = learner.apply("text-base", protected_query)
    protected_artifact = {
        "query_sha256": _digest(protected_query),
        "expected_sha256": _digest(protected_expected),
        "kind": "typed-base-retention",
    }
    baseline.append(_measurement("protected-typed-base", "string", 0, 1.0, protected_artifact))
    before = learner.skills
    rejected_bad_update = False
    try:
        learner.learn(
            "text-base",
            "string",
            [Demonstration("A", object()), Demonstration("B", object())],
            max_terms=2,
        )
    except InductionError:
        rejected_bad_update = True
    retained = learner.skills == before and learner.apply("text-base", protected_query) == protected_expected
    candidate.append(
        _measurement(
            "protected-typed-base",
            "string",
            0,
            1.0 if retained else 0.0,
            protected_artifact,
        )
    )

    regression = compare_snapshots(
        baseline,
        candidate,
        target_task_ids=list(queries),
        scope="development-typed-cross-domain-composition",
        candidate_id="typed-cross-domain-composition-v1",
    )
    payload = {
        "seed_commitment": seed_commitment,
        "skills": [asdict(skill) for skill in learner.skills],
        "task_results": [asdict(result) for result in results],
        "raw_budget_failures": raw_budget_failures,
        "rejected_bad_update": rejected_bad_update,
        "protected_retention": retained,
        "regression_decision": regression,
    }
    return {
        **payload,
        "passed": (
            all(raw_budget_failures.values())
            and all(result.success for result in results)
            and rejected_bad_update
            and retained
            and bool(regression["adopt_candidate"])
        ),
        "digest": _digest(payload),
        "evidence_tier": "development",
        "claim_boundary": (
            "This demonstrates well-typed cross-domain composition and recursive learned-macro reuse "
            "under a bounded search budget. The primitive vocabulary is still handwritten and finite, "
            "so the result is capability-development evidence, not open-ended learning or AGI."
        ),
    }
