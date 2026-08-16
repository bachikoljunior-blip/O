from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping, Sequence

from .induction import Demonstration, InductionError
from .regression import compare_snapshots


@dataclass(frozen=True)
class Primitive:
    name: str
    domain: str
    function: Callable[[Any], Any]


@dataclass(frozen=True)
class ComposedSkill:
    skill_id: str
    domain: str
    terms: tuple[str, ...]
    expanded_primitives: tuple[str, ...]
    support_sha256: str


@dataclass(frozen=True)
class CompositionResult:
    skill_id: str
    domain: str
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


def default_primitives() -> tuple[Primitive, ...]:
    return (
        Primitive("reverse", "string", lambda value: str(value)[::-1]),
        Primitive("append_hash", "string", lambda value: str(value) + "#"),
        Primitive("drop_first", "string", lambda value: str(value)[1:]),
        Primitive("duplicate", "string", lambda value: str(value) + str(value)),
        Primitive("times2", "numeric", lambda value: int(value) * 2),
        Primitive("plus3", "numeric", lambda value: int(value) + 3),
        Primitive("negate", "numeric", lambda value: -int(value)),
        Primitive("minus5", "numeric", lambda value: int(value) - 5),
        Primitive("reverse", "sequence", lambda value: list(value)[::-1]),
        Primitive("unique", "sequence", lambda value: list(dict.fromkeys(value))),
        Primitive("sort", "sequence", lambda value: sorted(value)),
        Primitive("drop_first", "sequence", lambda value: list(value)[1:]),
    )


class CompositionalLearner:
    """Synthesize new procedures from reusable primitives and previously learned skills.

    Learned skills may be used as one macro term when learning a later skill. This lets the
    system solve a behavior requiring more primitive operations than the current term budget,
    providing a falsifiable form of skill chunking rather than merely memorizing task IDs.
    Ambiguous or contradictory updates fail closed and leave the previous skill untouched.
    """

    def __init__(self, primitives: Sequence[Primitive] | None = None) -> None:
        values = tuple(primitives or default_primitives())
        index: dict[tuple[str, str], Primitive] = {}
        for primitive in values:
            key = (primitive.domain, primitive.name)
            if key in index:
                raise ValueError(f"duplicate primitive: {primitive.domain}:{primitive.name}")
            index[key] = primitive
        self._primitive_index = index
        self._skills: dict[str, ComposedSkill] = {}

    @property
    def skills(self) -> tuple[ComposedSkill, ...]:
        return tuple(self._skills[key] for key in sorted(self._skills))

    def _primitive_names(self, domain: str) -> tuple[str, ...]:
        return tuple(sorted(name for item_domain, name in self._primitive_index if item_domain == domain))

    def _tokens(self, domain: str, *, allow_macros: bool) -> tuple[str, ...]:
        tokens = [f"primitive:{name}" for name in self._primitive_names(domain)]
        if allow_macros:
            tokens.extend(
                f"skill:{skill.skill_id}"
                for skill in self.skills
                if skill.domain == domain
            )
        return tuple(tokens)

    def _expand(self, domain: str, terms: Sequence[str]) -> tuple[str, ...]:
        expanded: list[str] = []
        for term in terms:
            if term.startswith("primitive:"):
                name = term.removeprefix("primitive:")
                if (domain, name) not in self._primitive_index:
                    raise InductionError(f"unknown primitive {domain}:{name}")
                expanded.append(name)
            elif term.startswith("skill:"):
                skill_id = term.removeprefix("skill:")
                skill = self._skills.get(skill_id)
                if skill is None or skill.domain != domain:
                    raise InductionError(f"unknown macro skill {domain}:{skill_id}")
                expanded.extend(skill.expanded_primitives)
            else:
                raise InductionError(f"invalid composition term: {term}")
        return tuple(expanded)

    def _apply_expanded(self, domain: str, expanded: Sequence[str], value: Any) -> Any:
        current = value
        for name in expanded:
            current = self._primitive_index[(domain, name)].function(current)
        return current

    def learn(
        self,
        skill_id: str,
        domain: str,
        demonstrations: Sequence[Demonstration],
        *,
        max_terms: int = 2,
        allow_macros: bool = True,
    ) -> ComposedSkill:
        if not skill_id:
            raise InductionError("skill_id must be non-empty")
        if len(demonstrations) < 2:
            raise InductionError("at least two demonstrations are required")
        if max_terms < 1:
            raise InductionError("max_terms must be positive")
        tokens = self._tokens(domain, allow_macros=allow_macros)
        if not tokens:
            raise InductionError(f"no primitives available for domain: {domain}")

        matches_by_terms: dict[int, dict[tuple[str, ...], tuple[str, ...]]] = {}
        for term_count in range(1, max_terms + 1):
            matches: dict[tuple[str, ...], tuple[str, ...]] = {}
            for terms in itertools.product(tokens, repeat=term_count):
                try:
                    expanded = self._expand(domain, terms)
                    if expanded in matches:
                        continue
                    if all(
                        self._apply_expanded(domain, expanded, item.input) == item.output
                        for item in demonstrations
                    ):
                        matches[expanded] = tuple(terms)
                except (TypeError, ValueError, OverflowError, InductionError):
                    continue
            if matches:
                matches_by_terms[term_count] = matches
                break

        if not matches_by_terms:
            raise InductionError("no composition matches all demonstrations")
        minimum = min(matches_by_terms)
        matches = matches_by_terms[minimum]
        if len(matches) != 1:
            raise InductionError("demonstrations are ambiguous across multiple minimal compositions")
        expanded, terms = next(iter(matches.items()))
        payload = [{"input": item.input, "output": item.output} for item in demonstrations]
        learned = ComposedSkill(
            skill_id=skill_id,
            domain=domain,
            terms=terms,
            expanded_primitives=expanded,
            support_sha256=_digest(payload),
        )
        self._skills[skill_id] = learned
        return learned

    def apply(self, skill_id: str, value: Any) -> Any:
        skill = self._skills.get(skill_id)
        if skill is None:
            raise InductionError(f"unknown composed skill: {skill_id}")
        return self._apply_expanded(skill.domain, skill.expanded_primitives, value)

    def snapshot(self) -> dict[str, Any]:
        payload = {"schema_version": 1, "skills": [asdict(skill) for skill in self.skills]}
        return {**payload, "sha256": _digest(payload)}

    @classmethod
    def from_snapshot(
        cls,
        value: Mapping[str, Any],
        primitives: Sequence[Primitive] | None = None,
    ) -> "CompositionalLearner":
        if value.get("schema_version") != 1 or not isinstance(value.get("skills"), list):
            raise InductionError("invalid compositional snapshot schema")
        payload = {"schema_version": 1, "skills": value["skills"]}
        if value.get("sha256") != _digest(payload):
            raise InductionError("compositional snapshot digest mismatch")
        learner = cls(primitives)
        for raw in value["skills"]:
            if not isinstance(raw, Mapping):
                raise InductionError("snapshot skill must be an object")
            skill = ComposedSkill(
                skill_id=str(raw.get("skill_id", "")),
                domain=str(raw.get("domain", "")),
                terms=tuple(str(item) for item in raw.get("terms", [])),
                expanded_primitives=tuple(str(item) for item in raw.get("expanded_primitives", [])),
                support_sha256=str(raw.get("support_sha256", "")),
            )
            if not skill.skill_id or len(skill.support_sha256) != 64:
                raise InductionError("invalid composed skill snapshot")
            for primitive in skill.expanded_primitives:
                if (skill.domain, primitive) not in learner._primitive_index:
                    raise InductionError(f"snapshot references unknown primitive {skill.domain}:{primitive}")
            if skill.skill_id in learner._skills:
                raise InductionError(f"duplicate composed skill: {skill.skill_id}")
            learner._skills[skill.skill_id] = skill
        return learner


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


def run_compositional_campaign(seed: str) -> dict[str, Any]:
    """Learn unseen compositions, then reuse an acquired skill as a macro for deeper behavior."""

    if not seed:
        raise ValueError("seed must be non-empty")
    seed_commitment = _digest({"seed": seed, "suite": "compositional-learning-v1"})
    learner = CompositionalLearner()

    hidden: dict[str, tuple[str, tuple[str, ...], list[Any]]] = {
        "text-base": ("string", ("reverse", "append_hash"), ["Abc", "qRSt", "Model7"]),
        "numeric-composed": ("numeric", ("times2", "plus3"), [-4, 5, 11]),
        "sequence-composed": ("sequence", ("unique", "reverse"), [[3, 1, 3, 2], [8, 4, 8, 5], [7, 2, 7, 1]]),
    }
    for skill_id, (domain, expanded, inputs) in hidden.items():
        demos = [
            Demonstration(value, learner._apply_expanded(domain, expanded, value))
            for value in inputs
        ]
        learned = learner.learn(skill_id, domain, demos, max_terms=2, allow_macros=False)
        if learned.expanded_primitives != expanded:
            raise RuntimeError(f"campaign support did not uniquely identify {skill_id}")

    derived_expanded = ("reverse", "append_hash", "duplicate")
    derived_inputs = ["Alpha", "bEtA", "Gamma9"]
    derived_demos = [
        Demonstration(value, learner._apply_expanded("string", derived_expanded, value))
        for value in derived_inputs
    ]
    derived = learner.learn(
        "text-derived",
        "string",
        derived_demos,
        max_terms=2,
        allow_macros=True,
    )
    if derived.expanded_primitives != derived_expanded or "skill:text-base" not in derived.terms:
        raise RuntimeError("derived skill was not synthesized by reusing the prior skill macro")

    snapshot = learner.snapshot()
    learner = CompositionalLearner.from_snapshot(snapshot)
    queries = {
        "text-base": ["HeldOut", "xYz9"],
        "numeric-composed": [19, -13],
        "sequence-composed": [[5, 1, 5, 4, 1], [9, 2, 8, 9, 2]],
        "text-derived": ["Transfer", "NovelQ"],
    }
    expanded_by_skill = {
        **{skill_id: value[1] for skill_id, value in hidden.items()},
        "text-derived": derived_expanded,
    }
    domain_by_skill = {
        **{skill_id: value[0] for skill_id, value in hidden.items()},
        "text-derived": "string",
    }

    results: list[CompositionResult] = []
    baseline: list[dict[str, Any]] = []
    candidate: list[dict[str, Any]] = []
    for skill_id, values in queries.items():
        domain = domain_by_skill[skill_id]
        expanded = expanded_by_skill[skill_id]
        for repeat, query in enumerate(values):
            expected = learner._apply_expanded(domain, expanded, query)
            answer = learner.apply(skill_id, query)
            success = answer == expected
            results.append(CompositionResult(skill_id, domain, repeat, query, expected, answer, success))
            artifact = {"query": query, "expected": expected, "hidden_program": _digest(expanded)}
            baseline.append(_measurement(skill_id, domain, repeat, 0.0, artifact))
            candidate.append(_measurement(skill_id, domain, repeat, 1.0 if success else 0.0, artifact))

    anchor_query = "RetainMe"
    anchor_expected = learner.apply("text-base", anchor_query)
    anchor_artifact = {"query": anchor_query, "expected": anchor_expected, "kind": "retention"}
    baseline.append(_measurement("protected-composition-retention", "string", 0, 1.0, anchor_artifact))
    before = learner.skills
    rejected_bad_update = False
    try:
        learner.learn(
            "text-base",
            "string",
            [Demonstration("A", "impossible-1"), Demonstration("B", "impossible-2")],
            max_terms=2,
        )
    except InductionError:
        rejected_bad_update = True
    retained = learner.skills == before and learner.apply("text-base", anchor_query) == anchor_expected
    candidate.append(
        _measurement(
            "protected-composition-retention",
            "string",
            0,
            1.0 if retained else 0.0,
            anchor_artifact,
        )
    )

    regression = compare_snapshots(
        baseline,
        candidate,
        target_task_ids=list(queries),
        scope="development-compositional-skill-learning",
        candidate_id="compositional-learning-kernel-v1",
    )
    payload = {
        "seed_commitment": seed_commitment,
        "skills": [asdict(skill) for skill in learner.skills],
        "task_results": [asdict(result) for result in results],
        "rejected_bad_update": rejected_bad_update,
        "snapshot_reload_verified": learner.snapshot() == snapshot,
        "regression_decision": regression,
    }
    return {
        **payload,
        "passed": (
            all(result.success for result in results)
            and rejected_bad_update
            and retained
            and bool(regression["adopt_candidate"])
        ),
        "digest": _digest(payload),
        "evidence_tier": "development",
        "claim_boundary": (
            "The primitive set and synthesis depth are bounded. Success demonstrates compositional "
            "skill induction and macro reuse in this development domain, not AGI."
        ),
    }
