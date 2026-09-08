from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import asdict, dataclass
from typing import Any, Sequence

from .compositional import ComposedSkill, CompositionalLearner, CompositionResult, Primitive, default_primitives
from .induction import Demonstration, InductionError
from .regression import compare_snapshots


@dataclass(frozen=True)
class EvaluatorCompositionTask:
    """A hidden evaluator-owned composition with public demonstrations and withheld query answers."""

    skill_id: str
    domain: str
    training_inputs: tuple[Any, ...]
    query_inputs: tuple[Any, ...]
    _hidden_program: tuple[str, ...]

    @property
    def program_commitment(self) -> str:
        return _digest(
            {
                "skill_id": self.skill_id,
                "domain": self.domain,
                "program": self._hidden_program,
                "training_inputs": self.training_inputs,
                "query_inputs": self.query_inputs,
            }
        )

    def demonstrations(self) -> tuple[Demonstration, ...]:
        return tuple(
            Demonstration(value, _apply_program(self.domain, self._hidden_program, value))
            for value in self.training_inputs
        )

    def expected(self, query: Any) -> Any:
        return _apply_program(self.domain, self._hidden_program, query)


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _primitive_index(primitives: Sequence[Primitive] | None = None) -> dict[tuple[str, str], Primitive]:
    return {(item.domain, item.name): item for item in tuple(primitives or default_primitives())}


def _names_for_domain(domain: str) -> tuple[str, ...]:
    return tuple(sorted(name for item_domain, name in _primitive_index() if item_domain == domain))


def _apply_program(domain: str, program: Sequence[str], value: Any) -> Any:
    index = _primitive_index()
    current = value
    for name in program:
        primitive = index.get((domain, name))
        if primitive is None:
            raise InductionError(f"unknown evaluator primitive {domain}:{name}")
        current = primitive.function(current)
    return current


def _outputs(domain: str, program: Sequence[str], inputs: Sequence[Any]) -> tuple[Any, ...]:
    return tuple(_apply_program(domain, program, value) for value in inputs)


def _unique_two_step_programs(domain: str, training_inputs: Sequence[Any]) -> tuple[tuple[str, str], ...]:
    """Return non-trivial programs uniquely identified by demonstrations at depth two.

    The evaluator computes this set independently from the learner. A program is eligible only when
    no one-step primitive has the same training behavior and no other two-step primitive composition
    fits the same demonstrations. This prevents a seed from accidentally selecting an ambiguous task.
    """

    names = _names_for_domain(domain)
    one_step = tuple((name,) for name in names)
    two_step = tuple(itertools.product(names, repeat=2))
    eligible: list[tuple[str, str]] = []
    for program in two_step:
        if program[0] == program[1]:
            continue
        target = _outputs(domain, program, training_inputs)
        if any(_outputs(domain, candidate, training_inputs) == target for candidate in one_step):
            continue
        matches = [candidate for candidate in two_step if _outputs(domain, candidate, training_inputs) == target]
        if matches == [program]:
            eligible.append(program)
    if not eligible:
        raise RuntimeError(f"no uniquely identifiable evaluator programs for domain: {domain}")
    return tuple(eligible)


def _seeded_index(seed: str, label: str, count: int) -> int:
    if count < 1:
        raise ValueError("count must be positive")
    digest = hashlib.sha256(f"compositional-learning-v2\0{seed}\0{label}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % count


def _select_program(seed: str, label: str, domain: str, training_inputs: Sequence[Any]) -> tuple[str, str]:
    choices = _unique_two_step_programs(domain, training_inputs)
    return choices[_seeded_index(seed, label, len(choices))]


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


def _build_tasks(seed: str) -> tuple[EvaluatorCompositionTask, ...]:
    text_training = ("Abc", "qRSt", "Model7")
    numeric_training = (-4, 5, 11)
    sequence_training = ([3, 1, 3, 2], [8, 4, 8, 5], [7, 2, 7, 1])

    text_program = _select_program(seed, "text-base", "string", text_training)
    numeric_program = _select_program(seed, "numeric-composed", "numeric", numeric_training)
    sequence_program = _select_program(seed, "sequence-composed", "sequence", sequence_training)

    return (
        EvaluatorCompositionTask(
            "text-base",
            "string",
            text_training,
            ("HeldOut", "xYz9"),
            text_program,
        ),
        EvaluatorCompositionTask(
            "numeric-composed",
            "numeric",
            numeric_training,
            (19, -13),
            numeric_program,
        ),
        EvaluatorCompositionTask(
            "sequence-composed",
            "sequence",
            sequence_training,
            ([5, 1, 5, 4, 1], [9, 2, 8, 9, 2]),
            sequence_program,
        ),
        EvaluatorCompositionTask(
            "text-derived",
            "string",
            ("Alpha", "bEtA", "Gamma9"),
            ("Transfer", "NovelQ"),
            text_program + ("append_hash",),
        ),
    )


def run_varied_compositional_campaign(seed: str) -> dict[str, Any]:
    """Run seed-varied evaluator-owned compositions with answers withheld until scoring.

    The learner receives only demonstrations for each selected task. Program structures are selected
    from the evaluator's uniquely identifiable depth-two space using the fresh seed, and held-out
    expected answers are computed only by the evaluator after the learner has committed to a skill.
    The derived string task is one operation deeper than the selected base task and must reuse the
    learned base skill as a macro under the same two-term synthesis budget.
    """

    if not seed:
        raise ValueError("seed must be non-empty")
    seed_commitment = _digest({"seed": seed, "suite": "compositional-learning-v2"})
    tasks = _build_tasks(seed)
    learner = CompositionalLearner()

    for task in tasks[:3]:
        learned = learner.learn(
            task.skill_id,
            task.domain,
            task.demonstrations(),
            max_terms=2,
            allow_macros=False,
        )
        if _digest(learned.expanded_primitives) != _digest(task._hidden_program):
            raise RuntimeError(f"demonstrations did not uniquely identify {task.skill_id}")

    derived_task = tasks[3]
    derived = learner.learn(
        derived_task.skill_id,
        derived_task.domain,
        derived_task.demonstrations(),
        max_terms=2,
        allow_macros=True,
    )
    if _digest(derived.expanded_primitives) != _digest(derived_task._hidden_program):
        raise RuntimeError("derived demonstrations did not uniquely identify the hidden composition")
    if "skill:text-base" not in derived.terms:
        raise RuntimeError("derived skill did not reuse the seed-selected base skill macro")

    snapshot = learner.snapshot()
    learner = CompositionalLearner.from_snapshot(snapshot)

    results: list[CompositionResult] = []
    baseline: list[dict[str, Any]] = []
    candidate: list[dict[str, Any]] = []
    for task in tasks:
        for repeat, query in enumerate(task.query_inputs):
            expected = task.expected(query)
            answer = learner.apply(task.skill_id, query)
            success = answer == expected
            results.append(
                CompositionResult(
                    task.skill_id,
                    task.domain,
                    repeat,
                    query,
                    expected,
                    answer,
                    success,
                )
            )
            artifact = {
                "query": query,
                "expected_sha256": _digest(expected),
                "program_commitment": task.program_commitment,
            }
            baseline.append(_measurement(task.skill_id, task.domain, repeat, 0.0, artifact))
            candidate.append(_measurement(task.skill_id, task.domain, repeat, 1.0 if success else 0.0, artifact))

    anchor_query = "RetainMe"
    anchor_expected = learner.apply("text-base", anchor_query)
    anchor_artifact = {"query": anchor_query, "expected_sha256": _digest(anchor_expected), "kind": "retention"}
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
        target_task_ids=[task.skill_id for task in tasks],
        scope="development-seed-varied-compositional-learning",
        candidate_id="seed-varied-compositional-learning-kernel-v2",
    )
    payload = {
        "seed_commitment": seed_commitment,
        "program_commitments": {task.skill_id: task.program_commitment for task in tasks},
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
            "The evaluator varies hidden bounded compositions by seed and withholds held-out answers, "
            "but the primitive set and search depth remain bounded. Success is development evidence "
            "for generalization and skill reuse, not AGI."
        ),
    }
