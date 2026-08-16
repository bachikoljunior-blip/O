from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .induction import Demonstration, InductionError
from .regression import compare_snapshots


@dataclass(frozen=True)
class AcquiredTool:
    tool_id: str
    domain: str
    family: str
    parameters: dict[str, Any]
    support_sha256: str


@dataclass(frozen=True)
class ParameterizedTaskResult:
    task_id: str
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


def _safe_apply(domain: str, family: str, parameters: Mapping[str, Any], value: Any) -> Any:
    if domain == "numeric" and family == "affine":
        a = int(parameters["a"])
        b = int(parameters["b"])
        return a * int(value) + b
    if domain == "numeric" and family == "square_offset":
        c = int(parameters["c"])
        x = int(value)
        return x * x + c
    if domain == "string" and family == "reverse_suffix":
        suffix = str(parameters["suffix"])
        return str(value)[::-1] + suffix
    if domain == "string" and family == "upper_prefix":
        prefix = str(parameters["prefix"])
        return prefix + str(value).upper()
    if domain == "sequence" and family == "rotate_left":
        values = list(value)
        if not values:
            return []
        k = int(parameters["k"]) % len(values)
        return values[k:] + values[:k]
    if domain == "sequence" and family == "append_literal":
        return list(value) + [parameters["literal"]]
    raise InductionError(f"unsupported parameterized tool family: {domain}:{family}")


def _validate_parameters(domain: str, family: str, parameters: Mapping[str, Any]) -> None:
    if domain == "numeric" and family == "affine":
        if set(parameters) != {"a", "b"}:
            raise InductionError("affine parameters must be exactly a,b")
        if not all(isinstance(parameters[key], int) and not isinstance(parameters[key], bool) for key in ("a", "b")):
            raise InductionError("affine parameters must be integers")
        if abs(int(parameters["a"])) > 32 or abs(int(parameters["b"])) > 256:
            raise InductionError("affine parameters exceed safe bounds")
        return
    if domain == "numeric" and family == "square_offset":
        if set(parameters) != {"c"} or not isinstance(parameters.get("c"), int) or isinstance(parameters.get("c"), bool):
            raise InductionError("square_offset requires integer c")
        if abs(int(parameters["c"])) > 256:
            raise InductionError("square_offset parameter exceeds safe bound")
        return
    if domain == "string" and family in {"reverse_suffix", "upper_prefix"}:
        key = "suffix" if family == "reverse_suffix" else "prefix"
        if set(parameters) != {key} or not isinstance(parameters.get(key), str):
            raise InductionError(f"{family} requires string {key}")
        if len(str(parameters[key])) > 32:
            raise InductionError(f"{family} literal exceeds safe bound")
        return
    if domain == "sequence" and family == "rotate_left":
        if set(parameters) != {"k"} or not isinstance(parameters.get("k"), int) or isinstance(parameters.get("k"), bool):
            raise InductionError("rotate_left requires integer k")
        if not 0 <= int(parameters["k"]) <= 16:
            raise InductionError("rotate_left k exceeds safe bound")
        return
    if domain == "sequence" and family == "append_literal":
        if set(parameters) != {"literal"}:
            raise InductionError("append_literal requires one literal parameter")
        literal = parameters.get("literal")
        if not isinstance(literal, (str, int, float, bool)) and literal is not None:
            raise InductionError("append_literal literal must be a JSON scalar")
        return
    raise InductionError(f"unsupported parameterized tool family: {domain}:{family}")


class ParameterizedToolLearner:
    """Acquire exact safe tool parameters from demonstrations without arbitrary code execution.

    The family catalogue is finite and explicit, but the exact affine coefficients, string literals,
    rotations, and appended values are inferred from demonstrations rather than pre-enumerated as
    named primitives. A tool is installed only when exactly one family/parameterization matches all
    demonstrations. Failed or ambiguous updates leave prior tools unchanged.
    """

    def __init__(self) -> None:
        self._tools: dict[str, AcquiredTool] = {}

    @property
    def tools(self) -> tuple[AcquiredTool, ...]:
        return tuple(self._tools[key] for key in sorted(self._tools))

    def _numeric_candidates(self, demonstrations: Sequence[Demonstration]) -> list[tuple[str, dict[str, Any]]]:
        values = [(int(item.input), int(item.output)) for item in demonstrations]
        candidates: list[tuple[str, dict[str, Any]]] = []
        distinct = [(x, y) for x, y in values]
        pair = next(((a, b) for i, a in enumerate(distinct) for b in distinct[i + 1 :] if a[0] != b[0]), None)
        if pair is not None:
            (x1, y1), (x2, y2) = pair
            numerator = y2 - y1
            denominator = x2 - x1
            if denominator != 0 and numerator % denominator == 0:
                a = numerator // denominator
                b = y1 - a * x1
                params = {"a": a, "b": b}
                try:
                    _validate_parameters("numeric", "affine", params)
                    candidates.append(("affine", params))
                except InductionError:
                    pass
        offsets = {y - x * x for x, y in values}
        if len(offsets) == 1:
            params = {"c": offsets.pop()}
            try:
                _validate_parameters("numeric", "square_offset", params)
                candidates.append(("square_offset", params))
            except InductionError:
                pass
        return candidates

    def _string_candidates(self, demonstrations: Sequence[Demonstration]) -> list[tuple[str, dict[str, Any]]]:
        candidates: list[tuple[str, dict[str, Any]]] = []
        reverse_suffixes: list[str] = []
        reverse_valid = True
        upper_prefixes: list[str] = []
        upper_valid = True
        for item in demonstrations:
            source = str(item.input)
            output = item.output
            if not isinstance(output, str):
                reverse_valid = False
                upper_valid = False
                continue
            reversed_source = source[::-1]
            if output.startswith(reversed_source):
                reverse_suffixes.append(output[len(reversed_source) :])
            else:
                reverse_valid = False
            upper_source = source.upper()
            if output.endswith(upper_source):
                upper_prefixes.append(output[: len(output) - len(upper_source)] if upper_source else output)
            else:
                upper_valid = False
        if reverse_valid and reverse_suffixes and len(set(reverse_suffixes)) == 1:
            params = {"suffix": reverse_suffixes[0]}
            _validate_parameters("string", "reverse_suffix", params)
            candidates.append(("reverse_suffix", params))
        if upper_valid and upper_prefixes and len(set(upper_prefixes)) == 1:
            params = {"prefix": upper_prefixes[0]}
            _validate_parameters("string", "upper_prefix", params)
            candidates.append(("upper_prefix", params))
        return candidates

    def _sequence_candidates(self, demonstrations: Sequence[Demonstration]) -> list[tuple[str, dict[str, Any]]]:
        candidates: list[tuple[str, dict[str, Any]]] = []
        for k in range(0, 17):
            if all(_safe_apply("sequence", "rotate_left", {"k": k}, item.input) == item.output for item in demonstrations):
                candidates.append(("rotate_left", {"k": k}))
        literals: list[Any] = []
        append_valid = True
        for item in demonstrations:
            source = list(item.input)
            output = item.output
            if not isinstance(output, list) or len(output) != len(source) + 1 or output[:-1] != source:
                append_valid = False
                break
            literals.append(output[-1])
        if append_valid and literals and all(value == literals[0] for value in literals):
            params = {"literal": literals[0]}
            _validate_parameters("sequence", "append_literal", params)
            candidates.append(("append_literal", params))
        return candidates

    def learn(
        self,
        tool_id: str,
        domain: str,
        demonstrations: Sequence[Demonstration],
    ) -> AcquiredTool:
        if not tool_id:
            raise InductionError("tool_id must be non-empty")
        if len(demonstrations) < 3:
            raise InductionError("at least three demonstrations are required")
        try:
            if domain == "numeric":
                candidates = self._numeric_candidates(demonstrations)
            elif domain == "string":
                candidates = self._string_candidates(demonstrations)
            elif domain == "sequence":
                candidates = self._sequence_candidates(demonstrations)
            else:
                raise InductionError(f"unsupported parameterized tool domain: {domain}")
        except (TypeError, ValueError, OverflowError) as exc:
            raise InductionError("demonstrations are invalid for the requested domain") from exc

        matches: list[tuple[str, dict[str, Any]]] = []
        for family, parameters in candidates:
            try:
                _validate_parameters(domain, family, parameters)
                if all(_safe_apply(domain, family, parameters, item.input) == item.output for item in demonstrations):
                    key = (family, _digest(parameters))
                    if not any(existing_family == key[0] and _digest(existing_params) == key[1] for existing_family, existing_params in matches):
                        matches.append((family, parameters))
            except (TypeError, ValueError, OverflowError, InductionError):
                continue
        if not matches:
            raise InductionError("no safe parameterized tool matches all demonstrations")
        if len(matches) != 1:
            raise InductionError("demonstrations are ambiguous across safe parameterized tool families")

        family, parameters = matches[0]
        support = [{"input": item.input, "output": item.output} for item in demonstrations]
        learned = AcquiredTool(tool_id, domain, family, dict(parameters), _digest(support))
        self._tools[tool_id] = learned
        return learned

    def apply(self, tool_id: str, value: Any) -> Any:
        tool = self._tools.get(tool_id)
        if tool is None:
            raise InductionError(f"unknown acquired tool: {tool_id}")
        _validate_parameters(tool.domain, tool.family, tool.parameters)
        return _safe_apply(tool.domain, tool.family, tool.parameters, value)

    def snapshot(self) -> dict[str, Any]:
        return {"schema_version": 1, "tools": [asdict(item) for item in self.tools]}

    @classmethod
    def from_snapshot(cls, value: Mapping[str, Any]) -> "ParameterizedToolLearner":
        if value.get("schema_version") != 1 or not isinstance(value.get("tools"), list):
            raise InductionError("invalid parameterized tool snapshot")
        learner = cls()
        for raw in value["tools"]:
            if not isinstance(raw, Mapping):
                raise InductionError("invalid acquired tool snapshot entry")
            tool = AcquiredTool(
                tool_id=str(raw.get("tool_id", "")),
                domain=str(raw.get("domain", "")),
                family=str(raw.get("family", "")),
                parameters=dict(raw.get("parameters", {})),
                support_sha256=str(raw.get("support_sha256", "")),
            )
            if not tool.tool_id or len(tool.support_sha256) != 64:
                raise InductionError("invalid acquired tool identity or support digest")
            _validate_parameters(tool.domain, tool.family, tool.parameters)
            if tool.tool_id in learner._tools:
                raise InductionError("duplicate acquired tool in snapshot")
            learner._tools[tool.tool_id] = tool
        return learner


def _seeded_index(seed: str, label: str, count: int) -> int:
    digest = hashlib.sha256(f"parameterized-tools-v1\0{seed}\0{label}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % count


def _hidden_spec(seed: str, domain: str) -> tuple[str, dict[str, Any]]:
    if domain == "numeric":
        families = ("affine", "square_offset")
        family = families[_seeded_index(seed, "numeric-family", len(families))]
        if family == "affine":
            a = (2, 3, -2)[_seeded_index(seed, "numeric-param-a", 3)]
            b = (-5, 4, 7)[_seeded_index(seed, "numeric-param-b", 3)]
            return family, {"a": a, "b": b}
        return family, {"c": (-4, 3, 9)[_seeded_index(seed, "numeric-param-b", 3)]}
    if domain == "string":
        families = ("reverse_suffix", "upper_prefix")
        family = families[_seeded_index(seed, "string-family", len(families))]
        literal = ("!", "::x", "-q7")[_seeded_index(seed, "string-param", 3)]
        return (family, {"suffix": literal}) if family == "reverse_suffix" else (family, {"prefix": literal})
    if domain == "sequence":
        families = ("rotate_left", "append_literal")
        family = families[_seeded_index(seed, "sequence-family", len(families))]
        option = _seeded_index(seed, "sequence-param", 3)
        if family == "rotate_left":
            return family, {"k": (1, 2, 3)[option]}
        return family, {"literal": (0, 9, -1)[option]}
    raise ValueError(domain)


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


def run_parameterized_tool_campaign(seed: str) -> dict[str, Any]:
    """Acquire evaluator-hidden tool parameters and score only after learner commitment."""

    if not seed:
        raise ValueError("seed must be non-empty")
    learner = ParameterizedToolLearner()
    task_inputs = {
        "numeric": ((-3, 2, 7), (11, -8)),
        "string": (("Abc", "Model7", "qRSt"), ("HeldOut", "NovelQ")),
        "sequence": (([1, 2, 3, 4, 5], [8, 4, 7, 1, 9, 2], [3, 9, 1, 8, 2, 7, 5]), ([5, 2, 8, 1, 7, 3], [9, 1, 6, 2, 8, 4, 7])),
    }
    hidden: dict[str, tuple[str, dict[str, Any]]] = {domain: _hidden_spec(seed, domain) for domain in task_inputs}
    commitments = {
        domain: _digest({"domain": domain, "family": family, "parameters": parameters})
        for domain, (family, parameters) in hidden.items()
    }

    for domain, (training, _) in task_inputs.items():
        family, parameters = hidden[domain]
        demonstrations = tuple(
            Demonstration(value, _safe_apply(domain, family, parameters, value))
            for value in training
        )
        learner.learn(f"{domain}-tool", domain, demonstrations)

    snapshot = learner.snapshot()
    learner = ParameterizedToolLearner.from_snapshot(snapshot)
    results: list[ParameterizedTaskResult] = []
    baseline: list[dict[str, Any]] = []
    candidate: list[dict[str, Any]] = []
    for domain, (_, queries) in task_inputs.items():
        family, parameters = hidden[domain]
        for repeat, query in enumerate(queries):
            expected = _safe_apply(domain, family, parameters, query)
            answer = learner.apply(f"{domain}-tool", query)
            success = answer == expected
            results.append(ParameterizedTaskResult(f"{domain}-tool", domain, repeat, query, expected, answer, success))
            artifact = {
                "query_sha256": _digest(query),
                "expected_sha256": _digest(expected),
                "hidden_program_commitment": commitments[domain],
            }
            baseline.append(_measurement(f"{domain}-tool", domain, repeat, 0.0, artifact))
            candidate.append(_measurement(f"{domain}-tool", domain, repeat, 1.0 if success else 0.0, artifact))

    anchor_query = 13
    anchor_expected = learner.apply("numeric-tool", anchor_query)
    anchor_artifact = {
        "query_sha256": _digest(anchor_query),
        "expected_sha256": _digest(anchor_expected),
        "kind": "parameterized-tool-retention",
    }
    baseline.append(_measurement("protected-parameterized-retention", "numeric", 0, 1.0, anchor_artifact))
    before = learner.tools
    rejected_bad_update = False
    try:
        learner.learn(
            "numeric-tool",
            "numeric",
            [Demonstration(1, 1), Demonstration(2, 2), Demonstration(3, 999)],
        )
    except InductionError:
        rejected_bad_update = True
    retained = learner.tools == before and learner.apply("numeric-tool", anchor_query) == anchor_expected
    candidate.append(
        _measurement(
            "protected-parameterized-retention",
            "numeric",
            0,
            1.0 if retained else 0.0,
            anchor_artifact,
        )
    )

    regression = compare_snapshots(
        baseline,
        candidate,
        target_task_ids=["numeric-tool", "string-tool", "sequence-tool"],
        scope="development-parameterized-tool-acquisition",
        candidate_id="parameterized-tool-acquisition-v1",
    )
    payload = {
        "seed_commitment": _digest({"seed": seed, "suite": "parameterized-tools-v1"}),
        "hidden_program_commitments": commitments,
        "acquired_tools": [asdict(item) for item in learner.tools],
        "task_results": [asdict(item) for item in results],
        "snapshot_reload_verified": learner.snapshot() == snapshot,
        "rejected_bad_update": rejected_bad_update,
        "protected_retention": retained,
        "regression_decision": regression,
    }
    return {
        **payload,
        "passed": (
            all(item.success for item in results)
            and payload["snapshot_reload_verified"]
            and rejected_bad_update
            and retained
            and bool(regression["adopt_candidate"])
        ),
        "digest": _digest(payload),
        "evidence_tier": "development",
        "claim_boundary": (
            "Exact tool parameters and literals are acquired from demonstrations and vary by evaluator seed, "
            "but execution is restricted to a small audited family catalogue. This is evidence of bounded "
            "parameterized tool acquisition, not open-ended code synthesis or AGI."
        ),
    }
