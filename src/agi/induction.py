from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from .regression import compare_snapshots


class InductionError(ValueError):
    pass


@dataclass(frozen=True)
class Demonstration:
    input: Any
    output: Any


@dataclass(frozen=True)
class LearnedProgram:
    skill_id: str
    domain: str
    program_id: str
    support_sha256: str


@dataclass(frozen=True)
class InductionTaskResult:
    skill_id: str
    domain: str
    repeat_index: int
    query: Any
    expected: Any
    answer: Any
    success: bool
    program_id: str


@dataclass(frozen=True)
class InductionCampaignReport:
    seed_commitment: str
    learned_programs: tuple[LearnedProgram, ...]
    task_results: tuple[InductionTaskResult, ...]
    rejected_corrupt_update: bool
    snapshot_reload_verified: bool
    regression_decision: dict[str, Any]
    digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed_commitment": self.seed_commitment,
            "learned_programs": [asdict(item) for item in self.learned_programs],
            "task_results": [asdict(item) for item in self.task_results],
            "rejected_corrupt_update": self.rejected_corrupt_update,
            "snapshot_reload_verified": self.snapshot_reload_verified,
            "regression_decision": self.regression_decision,
            "passed": (
                bool(self.task_results)
                and all(item.success for item in self.task_results)
                and self.rejected_corrupt_update
                and self.snapshot_reload_verified
                and bool(self.regression_decision.get("adopt_candidate"))
            ),
            "digest": self.digest,
            "evidence_tier": "development",
            "claim_boundary": (
                "This campaign measures held-out transfer and retention inside a deliberately bounded "
                "program hypothesis space. It is development evidence only and is not evidence of AGI."
            ),
        }


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    return value


def _numeric_programs() -> list[tuple[str, Callable[[Any], Any]]]:
    result: list[tuple[str, Callable[[Any], Any]]] = []
    for multiplier in range(-4, 5):
        for offset in range(-12, 13):
            program_id = f"affine:{multiplier}:{offset}"
            result.append(
                (
                    program_id,
                    lambda value, a=multiplier, b=offset: a * int(value) + b,
                )
            )
    return result


def _string_programs() -> list[tuple[str, Callable[[Any], Any]]]:
    return [
        ("identity", lambda value: str(value)),
        ("reverse", lambda value: str(value)[::-1]),
        ("upper", lambda value: str(value).upper()),
        ("lower", lambda value: str(value).lower()),
        ("reverse_upper", lambda value: str(value)[::-1].upper()),
        ("reverse_lower", lambda value: str(value)[::-1].lower()),
        ("duplicate", lambda value: str(value) + str(value)),
        ("reverse_duplicate", lambda value: str(value)[::-1] + str(value)[::-1]),
    ]


def _unique_preserve(values: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[Any] = set()
    for value in values:
        frozen = _freeze(value)
        if frozen not in seen:
            seen.add(frozen)
            result.append(value)
    return result


def _sequence_programs() -> list[tuple[str, Callable[[Any], Any]]]:
    return [
        ("identity", lambda value: list(value)),
        ("reverse", lambda value: list(value)[::-1]),
        ("sorted_asc", lambda value: sorted(value)),
        ("sorted_desc", lambda value: sorted(value, reverse=True)),
        ("unique_preserve", lambda value: _unique_preserve(list(value))),
        ("unique_sorted", lambda value: sorted(set(value))),
        ("unique_sorted_desc", lambda value: sorted(set(value), reverse=True)),
    ]


def _programs(domain: str) -> list[tuple[str, Callable[[Any], Any]]]:
    if domain == "numeric":
        return _numeric_programs()
    if domain == "string":
        return _string_programs()
    if domain == "sequence":
        return _sequence_programs()
    raise InductionError(f"unsupported induction domain: {domain}")


def _resolve_program(domain: str, program_id: str) -> Callable[[Any], Any]:
    matches = [function for name, function in _programs(domain) if name == program_id]
    if len(matches) != 1:
        raise InductionError(f"unknown or duplicate program {domain}:{program_id}")
    return matches[0]


def _support_payload(demonstrations: Sequence[Demonstration]) -> list[dict[str, Any]]:
    return [{"input": item.input, "output": item.output} for item in demonstrations]


class ProgramInductionLearner:
    """Small fail-closed program-synthesis memory used for measurable learning experiments.

    The learner has no task-id handlers. It searches a bounded hypothesis space for a program
    consistent with supplied demonstrations, stores only a uniquely identified program, and can
    reuse it after other skills are learned or after a snapshot reload. A failed or ambiguous
    update never overwrites an already learned skill.
    """

    def __init__(self) -> None:
        self._skills: dict[str, LearnedProgram] = {}

    @property
    def skills(self) -> tuple[LearnedProgram, ...]:
        return tuple(self._skills[key] for key in sorted(self._skills))

    def learn(
        self,
        skill_id: str,
        domain: str,
        demonstrations: Sequence[Demonstration],
    ) -> LearnedProgram:
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise InductionError("skill_id must be a non-empty string")
        if len(demonstrations) < 2:
            raise InductionError("at least two demonstrations are required")
        payload = _support_payload(demonstrations)
        matches: list[str] = []
        for program_id, function in _programs(domain):
            try:
                if all(function(item.input) == item.output for item in demonstrations):
                    matches.append(program_id)
            except (TypeError, ValueError, OverflowError):
                continue
        if not matches:
            raise InductionError("no hypothesis matches all demonstrations")
        if len(matches) != 1:
            raise InductionError("demonstrations are ambiguous across multiple hypotheses")
        learned = LearnedProgram(
            skill_id=skill_id,
            domain=domain,
            program_id=matches[0],
            support_sha256=_digest(payload),
        )
        self._skills[skill_id] = learned
        return learned

    def apply(self, skill_id: str, value: Any) -> Any:
        learned = self._skills.get(skill_id)
        if learned is None:
            raise InductionError(f"unknown learned skill: {skill_id}")
        return _resolve_program(learned.domain, learned.program_id)(value)

    def snapshot(self) -> dict[str, Any]:
        payload = {
            "schema_version": 1,
            "skills": [asdict(item) for item in self.skills],
        }
        return {**payload, "sha256": _digest(payload)}

    @classmethod
    def from_snapshot(cls, value: Mapping[str, Any]) -> "ProgramInductionLearner":
        if value.get("schema_version") != 1 or not isinstance(value.get("skills"), list):
            raise InductionError("invalid induction snapshot schema")
        payload = {"schema_version": 1, "skills": value["skills"]}
        if value.get("sha256") != _digest(payload):
            raise InductionError("induction snapshot digest mismatch")
        learner = cls()
        for raw in value["skills"]:
            if not isinstance(raw, Mapping):
                raise InductionError("snapshot skill entry must be an object")
            learned = LearnedProgram(
                skill_id=str(raw.get("skill_id", "")),
                domain=str(raw.get("domain", "")),
                program_id=str(raw.get("program_id", "")),
                support_sha256=str(raw.get("support_sha256", "")),
            )
            if not learned.skill_id or len(learned.support_sha256) != 64:
                raise InductionError("invalid learned skill snapshot")
            _resolve_program(learned.domain, learned.program_id)
            if learned.skill_id in learner._skills:
                raise InductionError(f"duplicate snapshot skill: {learned.skill_id}")
            learner._skills[learned.skill_id] = learned
        return learner


def _measurement(
    *,
    task_id: str,
    domain: str,
    repeat_index: int,
    score: float,
    artifact: Any,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "criterion": "continual_learning",
        "domain": domain,
        "repeat_index": repeat_index,
        "passed": score == 1.0,
        "score": score,
        "artifact_sha256": _digest(artifact),
    }


def _choose_unique_hidden(
    rng: random.Random,
    domain: str,
    support_inputs: Sequence[Any],
    allowed_ids: Sequence[str],
) -> tuple[str, Callable[[Any], Any], tuple[Demonstration, ...]]:
    candidates = [(name, fn) for name, fn in _programs(domain) if name in set(allowed_ids)]
    rng.shuffle(candidates)
    for name, function in candidates:
        demonstrations = tuple(Demonstration(value, function(value)) for value in support_inputs)
        matches = []
        for candidate_name, candidate_fn in _programs(domain):
            try:
                if all(candidate_fn(item.input) == item.output for item in demonstrations):
                    matches.append(candidate_name)
            except (TypeError, ValueError, OverflowError):
                continue
        if matches == [name] or (len(matches) == 1 and matches[0] == name):
            return name, function, demonstrations
    raise RuntimeError(f"could not construct uniquely identifiable {domain} hidden program")


def run_induction_campaign(seed: str) -> InductionCampaignReport:
    """Measure actual learn-then-transfer behavior on fresh held-out queries.

    Exact hidden programs are selected after seeding, only demonstrations are supplied to the
    learner, and query outputs remain evaluator-side until scoring. Learned skills are then
    serialized/reloaded and re-used after interleaved learning. A deliberately contradictory
    update is rejected to verify that failed learning does not erase an existing skill.
    """

    if not seed:
        raise ValueError("seed must be non-empty")
    seed_commitment = _digest({"seed": seed, "suite": "heldout-induction-v1"})
    rng = random.Random(int(seed_commitment, 16))
    learner = ProgramInductionLearner()

    numeric_support = rng.sample(range(-9, 12), 3)
    numeric_id, numeric_fn, numeric_demos = _choose_unique_hidden(
        rng,
        "numeric",
        numeric_support,
        ["affine:2:3", "affine:-3:5", "affine:4:-7", "affine:-2:-4"],
    )
    learner.learn("numeric-rule", "numeric", numeric_demos)

    string_support = ["AbcD", "qRst", "MixeD"]
    string_id, string_fn, string_demos = _choose_unique_hidden(
        rng,
        "string",
        string_support,
        ["reverse", "upper", "lower", "reverse_upper", "reverse_lower", "duplicate", "reverse_duplicate"],
    )
    learner.learn("string-rule", "string", string_demos)

    sequence_support = [[4, 1, 4, 2], [9, 3, 5, 3], [8, 2, 7, 2, 1]]
    sequence_id, sequence_fn, sequence_demos = _choose_unique_hidden(
        rng,
        "sequence",
        sequence_support,
        ["reverse", "sorted_asc", "sorted_desc", "unique_preserve", "unique_sorted", "unique_sorted_desc"],
    )
    learner.learn("sequence-rule", "sequence", sequence_demos)

    if learner.apply("numeric-rule", numeric_support[0]) != numeric_fn(numeric_support[0]):
        raise RuntimeError("learned numeric program does not reproduce its support")
    if learner.apply("string-rule", string_support[0]) != string_fn(string_support[0]):
        raise RuntimeError("learned string program does not reproduce its support")
    if learner.apply("sequence-rule", sequence_support[0]) != sequence_fn(sequence_support[0]):
        raise RuntimeError("learned sequence program does not reproduce its support")

    snapshot = learner.snapshot()
    reloaded = ProgramInductionLearner.from_snapshot(snapshot)
    snapshot_reload_verified = reloaded.snapshot() == snapshot

    queries = {
        "numeric-rule": [rng.randint(20, 40), rng.randint(41, 70)],
        "string-rule": ["HeLLo7", "ZebraQ"],
        "sequence-rule": [[6, 2, 6, 1, 9], [5, 4, 5, 3, 8, 3]],
    }
    hidden = {
        "numeric-rule": ("numeric", numeric_id, numeric_fn),
        "string-rule": ("string", string_id, string_fn),
        "sequence-rule": ("sequence", sequence_id, sequence_fn),
    }
    results: list[InductionTaskResult] = []
    baseline_measurements: list[dict[str, Any]] = []
    candidate_measurements: list[dict[str, Any]] = []
    for skill_id in ("numeric-rule", "string-rule", "sequence-rule"):
        domain, program_id, function = hidden[skill_id]
        for repeat_index, query in enumerate(queries[skill_id]):
            expected = function(query)
            answer = reloaded.apply(skill_id, query)
            success = answer == expected
            result = InductionTaskResult(
                skill_id=skill_id,
                domain=domain,
                repeat_index=repeat_index,
                query=query,
                expected=expected,
                answer=answer,
                success=success,
                program_id=program_id,
            )
            results.append(result)
            artifact = {"query": query, "expected": expected, "program_commitment": _digest(program_id)}
            baseline_measurements.append(
                _measurement(
                    task_id=skill_id,
                    domain=domain,
                    repeat_index=repeat_index,
                    score=0.0,
                    artifact=artifact,
                )
            )
            candidate_measurements.append(
                _measurement(
                    task_id=skill_id,
                    domain=domain,
                    repeat_index=repeat_index,
                    score=1.0 if success else 0.0,
                    artifact=artifact,
                )
            )

    anchor_query = queries["numeric-rule"][0]
    anchor_expected = numeric_fn(anchor_query)
    anchor_artifact = {"query": anchor_query, "expected": anchor_expected, "kind": "retention-anchor"}
    baseline_measurements.append(
        _measurement(
            task_id="protected-retention-anchor",
            domain="numeric",
            repeat_index=0,
            score=1.0,
            artifact=anchor_artifact,
        )
    )

    rejected_corrupt_update = False
    old_program = reloaded.skills
    try:
        reloaded.learn(
            "numeric-rule",
            "numeric",
            [Demonstration(1, 111), Demonstration(2, -999), Demonstration(3, 7)],
        )
    except InductionError:
        rejected_corrupt_update = True
    retained = (
        reloaded.skills == old_program
        and reloaded.apply("numeric-rule", anchor_query) == anchor_expected
    )
    candidate_measurements.append(
        _measurement(
            task_id="protected-retention-anchor",
            domain="numeric",
            repeat_index=0,
            score=1.0 if retained else 0.0,
            artifact=anchor_artifact,
        )
    )

    regression = compare_snapshots(
        baseline_measurements,
        candidate_measurements,
        target_task_ids=["numeric-rule", "string-rule", "sequence-rule"],
        scope="development-heldout-program-induction",
        candidate_id="program-induction-kernel-v1",
    )
    report_payload = {
        "seed_commitment": seed_commitment,
        "learned_programs": [asdict(item) for item in reloaded.skills],
        "task_results": [asdict(item) for item in results],
        "rejected_corrupt_update": rejected_corrupt_update,
        "snapshot_reload_verified": snapshot_reload_verified,
        "regression_decision": regression,
    }
    return InductionCampaignReport(
        seed_commitment=seed_commitment,
        learned_programs=reloaded.skills,
        task_results=tuple(results),
        rejected_corrupt_update=rejected_corrupt_update,
        snapshot_reload_verified=snapshot_reload_verified,
        regression_decision=regression,
        digest=_digest(report_payload),
    )
