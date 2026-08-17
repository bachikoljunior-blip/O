from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

_ALLOWED_DOMAINS = ("numeric", "string", "sequence", "boolean", "object")
_UNARY_SIGNATURES = {
    "neg": ("numeric", "numeric"),
    "abs": ("numeric", "numeric"),
    "reverse": ("string", "string"),
    "upper": ("string", "string"),
    "lower": ("string", "string"),
    "length_string": ("string", "numeric"),
    "length_sequence": ("sequence", "numeric"),
    "to_string": ("numeric", "string"),
    "chars": ("string", "sequence"),
    "not_boolean": ("boolean", "boolean"),
}
_BINARY_SIGNATURES = {
    "add": ("numeric", "numeric", "numeric"),
    "sub": ("numeric", "numeric", "numeric"),
    "mul": ("numeric", "numeric", "numeric"),
    "concat_string": ("string", "string", "string"),
    "concat_sequence": ("sequence", "sequence", "sequence"),
    "and_boolean": ("boolean", "boolean", "boolean"),
    "or_boolean": ("boolean", "boolean", "boolean"),
    "ge_numeric": ("numeric", "numeric", "boolean"),
}
_FOLD_IDENTITIES = {"add": 0, "mul": 1}
_OBJECT_KEY_LIMIT = 128
_OBJECT_FIELD_LIMIT = 32
_JSON_DEPTH_LIMIT = 6
_JSON_NODE_LIMIT = 256


class AcquiredProgramError(ValueError):
    pass


@dataclass(frozen=True)
class ProgramExample:
    input: Any
    output: Any


@dataclass(frozen=True)
class AcquiredProgram:
    input_domain: str
    output_domain: str
    expression: dict[str, Any]
    support_sha256: str
    max_steps: int = 64
    max_output_length: int = 1024
    effects: tuple[str, ...] = ()

    def validate(self) -> None:
        validate_program_descriptor(asdict(self))

    def apply(self, value: Any) -> Any:
        return execute_program(asdict(self), value)

    def descriptor(self) -> dict[str, Any]:
        return asdict(self)


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _domain_of_value(value: Any) -> str | None:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "numeric"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "sequence"
    if isinstance(value, dict):
        return "object"
    return None


def _is_json_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _validate_json_tree(
    value: Any,
    *,
    depth: int = 0,
    remaining_nodes: list[int] | None = None,
) -> None:
    if remaining_nodes is None:
        remaining_nodes = [_JSON_NODE_LIMIT]
    remaining_nodes[0] -= 1
    if remaining_nodes[0] < 0:
        raise AcquiredProgramError("JSON value exceeds safe node bound")
    if depth > _JSON_DEPTH_LIMIT:
        raise AcquiredProgramError("JSON value exceeds safe depth bound")
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        if not math.isfinite(numeric) or abs(numeric) > 1_000_000_000_000:
            raise AcquiredProgramError("JSON numeric value exceeds safe bound")
        return
    if isinstance(value, (list, tuple)):
        if len(value) > 64:
            raise AcquiredProgramError("JSON sequence exceeds safe length bound")
        for item in value:
            _validate_json_tree(
                item,
                depth=depth + 1,
                remaining_nodes=remaining_nodes,
            )
        return
    if isinstance(value, dict):
        if len(value) > _OBJECT_FIELD_LIMIT:
            raise AcquiredProgramError("JSON object exceeds safe field bound")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > _OBJECT_KEY_LIMIT:
                raise AcquiredProgramError("JSON object keys must be bounded non-empty strings")
            _validate_json_tree(
                item,
                depth=depth + 1,
                remaining_nodes=remaining_nodes,
            )
        return
    raise AcquiredProgramError("value is not finite JSON")


def _validate_constant(value: Any, domain: str) -> None:
    actual = _domain_of_value(value)
    if actual != domain:
        raise AcquiredProgramError(f"constant does not match declared {domain} domain")
    if domain == "numeric":
        numeric = float(value)
        if not math.isfinite(numeric) or abs(numeric) > 1_000_000:
            raise AcquiredProgramError("numeric constant exceeds safe bound")
    elif domain == "string":
        if len(value) > 128:
            raise AcquiredProgramError("string constant exceeds safe bound")
    elif domain == "sequence":
        if len(value) > 64 or not all(_is_json_scalar(item) for item in value):
            raise AcquiredProgramError("sequence constant exceeds safe JSON-scalar bound")
    elif domain == "object":
        _validate_json_tree(value)
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > 512:
            raise AcquiredProgramError("object constant exceeds safe encoded bound")


def _validate_object_key(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > _OBJECT_KEY_LIMIT:
        raise AcquiredProgramError("object key must be a bounded non-empty string")
    return value


def _infer_expression(
    expression: Mapping[str, Any],
    *,
    input_domain: str,
    depth: int = 1,
) -> tuple[str, int, int]:
    if depth > 10:
        raise AcquiredProgramError("program expression depth exceeds safe bound")
    op = expression.get("op")
    if not isinstance(op, str):
        raise AcquiredProgramError("program node requires string op")
    if op == "input":
        if set(expression) != {"op"}:
            raise AcquiredProgramError("input node has unexpected fields")
        return input_domain, 1, depth
    if op == "const":
        if set(expression) != {"op", "domain", "value"}:
            raise AcquiredProgramError("const node fields must be exactly op,domain,value")
        domain = expression.get("domain")
        if domain not in _ALLOWED_DOMAINS:
            raise AcquiredProgramError("const node has invalid domain")
        _validate_constant(expression.get("value"), str(domain))
        return str(domain), 1, depth
    if op == "object_get":
        if set(expression) != {"op", "arg", "key", "domain"}:
            raise AcquiredProgramError(
                "object_get node fields must be exactly op,arg,key,domain"
            )
        arg = expression.get("arg")
        if not isinstance(arg, Mapping):
            raise AcquiredProgramError("object_get requires an object arg")
        _validate_object_key(expression.get("key"))
        output_domain = expression.get("domain")
        if output_domain not in _ALLOWED_DOMAINS:
            raise AcquiredProgramError("object_get has invalid output domain")
        actual, nodes, child_depth = _infer_expression(
            arg,
            input_domain=input_domain,
            depth=depth + 1,
        )
        if actual != "object":
            raise AcquiredProgramError(f"object_get expects object, got {actual}")
        return str(output_domain), nodes + 1, max(depth, child_depth)
    if op == "object_has_key":
        if set(expression) != {"op", "arg", "key"}:
            raise AcquiredProgramError(
                "object_has_key node fields must be exactly op,arg,key"
            )
        arg = expression.get("arg")
        if not isinstance(arg, Mapping):
            raise AcquiredProgramError("object_has_key requires an object arg")
        _validate_object_key(expression.get("key"))
        actual, nodes, child_depth = _infer_expression(
            arg,
            input_domain=input_domain,
            depth=depth + 1,
        )
        if actual != "object":
            raise AcquiredProgramError(f"object_has_key expects object, got {actual}")
        return "boolean", nodes + 1, max(depth, child_depth)
    if op == "fold_numeric":
        if set(expression) != {"op", "arg", "reducer", "initial"}:
            raise AcquiredProgramError(
                "fold_numeric node fields must be exactly op,arg,reducer,initial"
            )
        arg = expression.get("arg")
        reducer = expression.get("reducer")
        initial = expression.get("initial")
        if not isinstance(arg, Mapping):
            raise AcquiredProgramError("fold_numeric requires an object arg")
        if reducer not in _FOLD_IDENTITIES:
            raise AcquiredProgramError("fold_numeric reducer must be add or mul")
        _validate_constant(initial, "numeric")
        actual, nodes, child_depth = _infer_expression(
            arg,
            input_domain=input_domain,
            depth=depth + 1,
        )
        if actual != "sequence":
            raise AcquiredProgramError(f"fold_numeric expects sequence, got {actual}")
        return "numeric", nodes + 1, max(depth, child_depth)
    if op in {"if_nonnegative", "if_boolean"}:
        if set(expression) != {"op", "condition", "then", "else"}:
            raise AcquiredProgramError(
                f"{op} node fields must be exactly op,condition,then,else"
            )
        condition = expression.get("condition")
        then_branch = expression.get("then")
        else_branch = expression.get("else")
        if not all(
            isinstance(value, Mapping)
            for value in (condition, then_branch, else_branch)
        ):
            raise AcquiredProgramError(
                f"{op} requires object condition/then/else nodes"
            )
        condition_domain, condition_nodes, condition_depth = _infer_expression(
            condition,
            input_domain=input_domain,
            depth=depth + 1,
        )
        required_condition = "numeric" if op == "if_nonnegative" else "boolean"
        if condition_domain != required_condition:
            raise AcquiredProgramError(
                f"{op} condition expects {required_condition}, got {condition_domain}"
            )
        then_domain, then_nodes, then_depth = _infer_expression(
            then_branch,
            input_domain=input_domain,
            depth=depth + 1,
        )
        else_domain, else_nodes, else_depth = _infer_expression(
            else_branch,
            input_domain=input_domain,
            depth=depth + 1,
        )
        if then_domain != else_domain:
            raise AcquiredProgramError(
                f"{op} branches must share a domain; got {then_domain},{else_domain}"
            )
        return (
            then_domain,
            condition_nodes + then_nodes + else_nodes + 1,
            max(depth, condition_depth, then_depth, else_depth),
        )
    if op in _UNARY_SIGNATURES:
        if set(expression) != {"op", "arg"} or not isinstance(
            expression.get("arg"), Mapping
        ):
            raise AcquiredProgramError(f"{op} node requires exactly one arg")
        required, output = _UNARY_SIGNATURES[op]
        actual, nodes, child_depth = _infer_expression(
            expression["arg"], input_domain=input_domain, depth=depth + 1
        )
        if actual != required:
            raise AcquiredProgramError(f"{op} expects {required}, got {actual}")
        return output, nodes + 1, max(depth, child_depth)
    if op in _BINARY_SIGNATURES:
        if set(expression) != {"op", "left", "right"}:
            raise AcquiredProgramError(f"{op} node fields must be exactly op,left,right")
        left = expression.get("left")
        right = expression.get("right")
        if not isinstance(left, Mapping) or not isinstance(right, Mapping):
            raise AcquiredProgramError(f"{op} requires object left/right nodes")
        required_left, required_right, output = _BINARY_SIGNATURES[op]
        left_domain, left_nodes, left_depth = _infer_expression(
            left, input_domain=input_domain, depth=depth + 1
        )
        right_domain, right_nodes, right_depth = _infer_expression(
            right, input_domain=input_domain, depth=depth + 1
        )
        if left_domain != required_left or right_domain != required_right:
            raise AcquiredProgramError(
                f"{op} expects {required_left},{required_right}; got {left_domain},{right_domain}"
            )
        return output, left_nodes + right_nodes + 1, max(depth, left_depth, right_depth)
    raise AcquiredProgramError(f"unknown acquired-program op: {op}")


def validate_program_descriptor(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AcquiredProgramError("program descriptor must be an object")
    input_domain = value.get("input_domain")
    output_domain = value.get("output_domain")
    if input_domain not in _ALLOWED_DOMAINS or output_domain not in _ALLOWED_DOMAINS:
        raise AcquiredProgramError("program descriptor has invalid input/output domain")
    effects = value.get("effects", ())
    if not isinstance(effects, (list, tuple)) or effects:
        raise AcquiredProgramError("acquired programs must declare no side effects")
    max_steps = value.get("max_steps", 64)
    max_output_length = value.get("max_output_length", 1024)
    if (
        not isinstance(max_steps, int)
        or isinstance(max_steps, bool)
        or not 1 <= max_steps <= 128
    ):
        raise AcquiredProgramError("max_steps must be an integer from 1 through 128")
    if (
        not isinstance(max_output_length, int)
        or isinstance(max_output_length, bool)
        or not 1 <= max_output_length <= 4096
    ):
        raise AcquiredProgramError("max_output_length must be an integer from 1 through 4096")
    expression = value.get("expression")
    if not isinstance(expression, Mapping):
        raise AcquiredProgramError("program descriptor requires expression object")
    inferred, nodes, depth = _infer_expression(
        expression, input_domain=str(input_domain)
    )
    if inferred != output_domain:
        raise AcquiredProgramError(
            f"program expression returns {inferred}, not declared {output_domain}"
        )
    if nodes > max_steps:
        raise AcquiredProgramError("program node count exceeds max_steps")
    return {
        "input_domain": str(input_domain),
        "output_domain": str(output_domain),
        "nodes": nodes,
        "depth": depth,
        "max_steps": max_steps,
        "max_output_length": max_output_length,
        "effects": [],
    }


def _check_runtime_value(value: Any, domain: str, max_output_length: int) -> Any:
    if _domain_of_value(value) != domain:
        raise AcquiredProgramError(f"program produced invalid {domain} value")
    if domain == "numeric":
        numeric = float(value)
        if not math.isfinite(numeric) or abs(numeric) > 1_000_000_000_000:
            raise AcquiredProgramError("program numeric value exceeds runtime bound")
    elif domain == "string" and len(value) > max_output_length:
        raise AcquiredProgramError("program string output exceeds runtime bound")
    elif domain == "sequence":
        if len(value) > max_output_length:
            raise AcquiredProgramError("program sequence output exceeds runtime bound")
        _validate_json_tree(value)
    elif domain == "object":
        _validate_json_tree(value)
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > max_output_length:
            raise AcquiredProgramError("program object output exceeds runtime bound")
    return value


def execute_program(descriptor: Mapping[str, Any], value: Any) -> Any:
    meta = validate_program_descriptor(descriptor)
    input_domain = meta["input_domain"]
    if _domain_of_value(value) != input_domain:
        raise AcquiredProgramError(f"program expected {input_domain} input")
    remaining = int(meta["max_steps"])
    max_output_length = int(meta["max_output_length"])

    def run(node: Mapping[str, Any]) -> tuple[Any, str]:
        nonlocal remaining
        remaining -= 1
        if remaining < 0:
            raise AcquiredProgramError("program exceeded deterministic step budget")
        op = str(node["op"])
        if op == "input":
            return (
                _check_runtime_value(value, input_domain, max_output_length),
                input_domain,
            )
        if op == "const":
            domain = str(node["domain"])
            return (
                _check_runtime_value(node["value"], domain, max_output_length),
                domain,
            )
        if op == "object_get":
            source, domain = run(node["arg"])
            if domain != "object":
                raise AcquiredProgramError("program type changed after validation")
            key = str(node["key"])
            if key not in source:
                raise AcquiredProgramError("object_get key is absent")
            output_domain = str(node["domain"])
            return (
                _check_runtime_value(
                    source[key], output_domain, max_output_length
                ),
                output_domain,
            )
        if op == "object_has_key":
            source, domain = run(node["arg"])
            if domain != "object":
                raise AcquiredProgramError("program type changed after validation")
            return bool(str(node["key"]) in source), "boolean"
        if op == "fold_numeric":
            sequence, domain = run(node["arg"])
            if domain != "sequence":
                raise AcquiredProgramError("program type changed after validation")
            reducer = str(node["reducer"])
            accumulator = _check_runtime_value(
                node["initial"], "numeric", max_output_length
            )
            for item in sequence:
                remaining -= 1
                if remaining < 0:
                    raise AcquiredProgramError("program exceeded deterministic step budget")
                item = _check_runtime_value(item, "numeric", max_output_length)
                if reducer == "add":
                    accumulator = accumulator + item
                elif reducer == "mul":
                    accumulator = accumulator * item
                else:
                    raise AcquiredProgramError(reducer)
                accumulator = _check_runtime_value(
                    accumulator, "numeric", max_output_length
                )
            return accumulator, "numeric"
        if op in {"if_nonnegative", "if_boolean"}:
            condition, condition_domain = run(node["condition"])
            required_condition = "numeric" if op == "if_nonnegative" else "boolean"
            if condition_domain != required_condition:
                raise AcquiredProgramError("program type changed after validation")
            if op == "if_nonnegative":
                choose_then = condition >= 0
            else:
                choose_then = bool(condition)
            selected = node["then"] if choose_then else node["else"]
            result, result_domain = run(selected)
            return (
                _check_runtime_value(
                    result, result_domain, max_output_length
                ),
                result_domain,
            )
        if op in _UNARY_SIGNATURES:
            arg, domain = run(node["arg"])
            required, output_domain = _UNARY_SIGNATURES[op]
            if domain != required:
                raise AcquiredProgramError("program type changed after validation")
            if op == "neg":
                result = -arg
            elif op == "abs":
                result = abs(arg)
            elif op == "reverse":
                result = arg[::-1]
            elif op == "upper":
                result = arg.upper()
            elif op == "lower":
                result = arg.lower()
            elif op in {"length_string", "length_sequence"}:
                result = len(arg)
            elif op == "to_string":
                if isinstance(arg, float) and arg.is_integer():
                    result = str(int(arg))
                else:
                    result = str(arg)
            elif op == "chars":
                result = list(arg)
            elif op == "not_boolean":
                result = not arg
            else:
                raise AcquiredProgramError(op)
            return (
                _check_runtime_value(
                    result, output_domain, max_output_length
                ),
                output_domain,
            )
        left, left_domain = run(node["left"])
        right, right_domain = run(node["right"])
        required_left, required_right, output_domain = _BINARY_SIGNATURES[op]
        if left_domain != required_left or right_domain != required_right:
            raise AcquiredProgramError("program type changed after validation")
        if op == "add":
            result = left + right
        elif op == "sub":
            result = left - right
        elif op == "mul":
            result = left * right
        elif op == "concat_string":
            result = left + right
        elif op == "concat_sequence":
            result = list(left) + list(right)
        elif op == "and_boolean":
            result = bool(left and right)
        elif op == "or_boolean":
            result = bool(left or right)
        elif op == "ge_numeric":
            result = bool(left >= right)
        else:
            raise AcquiredProgramError(op)
        return (
            _check_runtime_value(result, output_domain, max_output_length),
            output_domain,
        )

    answer, answer_domain = run(descriptor["expression"])
    if answer_domain != meta["output_domain"]:
        raise AcquiredProgramError("program output domain changed after validation")
    return answer


def _behavior_signature(values: Sequence[Any]) -> str:
    return json.dumps(
        values,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _candidate_outputs(
    expression: Mapping[str, Any],
    input_domain: str,
    examples: Sequence[ProgramExample],
) -> list[Any] | None:
    descriptor = {
        "input_domain": input_domain,
        "output_domain": _infer_expression(
            expression, input_domain=input_domain
        )[0],
        "expression": dict(expression),
        "effects": [],
        "max_steps": 64,
        "max_output_length": 1024,
    }
    try:
        return [execute_program(descriptor, item.input) for item in examples]
    except (AcquiredProgramError, TypeError, ValueError, OverflowError):
        return None


def _collect_object_keys(value: Any, keys: set[str], *, depth: int = 0) -> None:
    if depth > _JSON_DEPTH_LIMIT:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key and len(key) <= _OBJECT_KEY_LIMIT:
                keys.add(key)
            _collect_object_keys(item, keys, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_object_keys(item, keys, depth=depth + 1)


def synthesize_program(
    *,
    input_domain: str,
    output_domain: str,
    examples: Sequence[ProgramExample],
    max_nodes: int = 7,
    allow_sequence_folds: bool = True,
    allow_conditionals: bool = True,
    allow_structured_ops: bool = True,
) -> AcquiredProgram:
    """Deterministically synthesize a pure typed expression from demonstrations.

    The search is over a bounded typed expression grammar rather than a catalogue of named task
    families. Behavioral deduplication keeps one minimal expression per observed behavior. Numeric
    sequence folds, finite object projection/presence, boolean predicates, and data-dependent
    conditionals are generic bounded kernels admitted only through the same exact-scope Candidate
    regression gate. Intermediate typed expressions remain available even when their domain differs
    from the requested final output, allowing already admitted kernels to compose without arbitrary
    host code. The resulting program remains inactive until continual regression verifies its exact
    scope on repeated held-out measurements.
    """

    if input_domain not in _ALLOWED_DOMAINS or output_domain not in _ALLOWED_DOMAINS:
        raise AcquiredProgramError("unsupported synthesis domain")
    if (
        not isinstance(max_nodes, int)
        or isinstance(max_nodes, bool)
        or not 1 <= max_nodes <= 9
    ):
        raise AcquiredProgramError("max_nodes must be an integer from 1 through 9")
    if not isinstance(allow_sequence_folds, bool):
        raise AcquiredProgramError("allow_sequence_folds must be boolean")
    if not isinstance(allow_conditionals, bool):
        raise AcquiredProgramError("allow_conditionals must be boolean")
    if not isinstance(allow_structured_ops, bool):
        raise AcquiredProgramError("allow_structured_ops must be boolean")
    if len(examples) < 3:
        raise AcquiredProgramError("at least three demonstrations are required")
    for item in examples:
        if (
            _domain_of_value(item.input) != input_domain
            or _domain_of_value(item.output) != output_domain
        ):
            raise AcquiredProgramError(
                "demonstration domains do not match synthesis request"
            )
        if input_domain in {"sequence", "object"}:
            _validate_json_tree(item.input)
        if output_domain in {"sequence", "object"}:
            _validate_json_tree(item.output)
    expected = [item.output for item in examples]

    by_cost: dict[int, dict[str, list[dict[str, Any]]]] = {
        cost: {domain: [] for domain in _ALLOWED_DOMAINS}
        for cost in range(1, max_nodes + 1)
    }
    seen: dict[str, set[str]] = {domain: set() for domain in _ALLOWED_DOMAINS}

    def add(
        cost: int,
        domain: str,
        expression: dict[str, Any],
    ) -> AcquiredProgram | None:
        outputs = _candidate_outputs(expression, input_domain, examples)
        if outputs is None:
            return None
        signature = _behavior_signature(outputs)
        if signature in seen[domain]:
            return None
        seen[domain].add(signature)
        by_cost[cost][domain].append(expression)
        if domain == output_domain and outputs == expected:
            support = [
                {"input": item.input, "output": item.output}
                for item in examples
            ]
            program = AcquiredProgram(
                input_domain=input_domain,
                output_domain=output_domain,
                expression=expression,
                support_sha256=_digest(support),
                max_steps=max(16, max_nodes * 2),
                max_output_length=1024,
                effects=(),
            )
            program.validate()
            return program
        return None

    match = add(1, input_domain, {"op": "input"})
    if match:
        return match
    base_constants: tuple[tuple[str, Any], ...] = tuple(
        [("numeric", value) for value in range(-5, 6)]
        + [
            ("string", ""),
            ("sequence", []),
            ("boolean", False),
            ("boolean", True),
            ("object", {}),
        ]
    )
    for domain, constant in base_constants:
        match = add(
            1,
            domain,
            {"op": "const", "domain": domain, "value": constant},
        )
        if match:
            return match

    object_keys: tuple[str, ...] = ()
    if allow_structured_ops:
        discovered: set[str] = set()
        for item in examples:
            _collect_object_keys(item.input, discovered)
        object_keys = tuple(sorted(discovered)[:16])

    if (
        allow_sequence_folds
        and input_domain == "sequence"
        and max_nodes >= 2
    ):
        for reducer, initial in _FOLD_IDENTITIES.items():
            match = add(
                2,
                "numeric",
                {
                    "op": "fold_numeric",
                    "arg": {"op": "input"},
                    "reducer": reducer,
                    "initial": initial,
                },
            )
            if match:
                return match

    if (
        allow_structured_ops
        and input_domain == "object"
        and max_nodes >= 2
    ):
        for key in object_keys:
            match = add(
                2,
                "boolean",
                {"op": "object_has_key", "arg": {"op": "input"}, "key": key},
            )
            if match:
                return match
            for domain in _ALLOWED_DOMAINS:
                match = add(
                    2,
                    domain,
                    {
                        "op": "object_get",
                        "arg": {"op": "input"},
                        "key": key,
                        "domain": domain,
                    },
                )
                if match:
                    return match

    for cost in range(2, max_nodes + 1):
        for op, (required, output) in _UNARY_SIGNATURES.items():
            for arg in list(by_cost[cost - 1][required]):
                match = add(cost, output, {"op": op, "arg": arg})
                if match:
                    return match
        for op, (
            left_required,
            right_required,
            output,
        ) in _BINARY_SIGNATURES.items():
            for left_cost in range(1, cost - 1):
                right_cost = cost - 1 - left_cost
                if right_cost < 1:
                    continue
                left_values = list(
                    by_cost[left_cost][left_required]
                )[:128]
                right_values = list(
                    by_cost[right_cost][right_required]
                )[:128]
                for left in left_values:
                    for right in right_values:
                        match = add(
                            cost,
                            output,
                            {"op": op, "left": left, "right": right},
                        )
                        if match:
                            return match
        if allow_structured_ops and object_keys:
            for arg in list(by_cost[cost - 1]["object"])[:32]:
                for key in object_keys:
                    match = add(
                        cost,
                        "boolean",
                        {"op": "object_has_key", "arg": arg, "key": key},
                    )
                    if match:
                        return match
                    for domain in _ALLOWED_DOMAINS:
                        match = add(
                            cost,
                            domain,
                            {
                                "op": "object_get",
                                "arg": arg,
                                "key": key,
                                "domain": domain,
                            },
                        )
                        if match:
                            return match
        if allow_conditionals and cost >= 4:
            condition_specs: list[tuple[str, str]] = [
                ("if_nonnegative", "numeric")
            ]
            if allow_structured_ops:
                condition_specs.append(("if_boolean", "boolean"))
            for conditional_op, condition_domain in condition_specs:
                for condition_cost in range(1, cost - 2):
                    for then_cost in range(
                        1, cost - condition_cost - 1
                    ):
                        else_cost = (
                            cost
                            - 1
                            - condition_cost
                            - then_cost
                        )
                        if else_cost < 1:
                            continue
                        conditions = list(
                            by_cost[condition_cost][condition_domain]
                        )[:64]
                        for branch_domain in _ALLOWED_DOMAINS:
                            then_values = list(
                                by_cost[then_cost][branch_domain]
                            )[:64]
                            else_values = list(
                                by_cost[else_cost][branch_domain]
                            )[:64]
                            for condition in conditions:
                                for then_branch in then_values:
                                    for else_branch in else_values:
                                        match = add(
                                            cost,
                                            branch_domain,
                                            {
                                                "op": conditional_op,
                                                "condition": condition,
                                                "then": then_branch,
                                                "else": else_branch,
                                            },
                                        )
                                        if match:
                                            return match
        for domain in _ALLOWED_DOMAINS:
            if len(by_cost[cost][domain]) > 256:
                by_cost[cost][domain] = by_cost[cost][domain][
                    :256
                ]
    raise AcquiredProgramError(
        "no bounded pure typed program matches all demonstrations"
    )
