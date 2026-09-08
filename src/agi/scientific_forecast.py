from __future__ import annotations

import argparse
import ast
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PRESENT = "DURABLE_EXPORT_PRESENT"
ABSENT = "DURABLE_EXPORT_ABSENT"
ROUND_TRIP = "round_trip_replay_integrity_probe"
SEEDED_EXPORT = "seeded_end_to_end_durable_export_probe"


@dataclass(frozen=True, order=True)
class DurableSinkMatch:
    line: int
    column: int
    pattern: str


def canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _qualified_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _literal_open_mode(call: ast.Call) -> str | None:
    mode_node: ast.AST | None = call.args[1] if len(call.args) >= 2 else None
    for keyword in call.keywords:
        if keyword.arg == "mode":
            mode_node = keyword.value
    if isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str):
        return mode_node.value
    return None


def durable_export_matches(source: str) -> tuple[DurableSinkMatch, ...]:
    """Apply the trial's frozen AST rule without executing the supplied source."""

    tree = ast.parse(source)
    matches: list[DurableSinkMatch] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        pattern: str | None = None
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"write_text", "write_bytes"}:
            pattern = f"attribute_call:{node.func.attr}"
        elif isinstance(node.func, ast.Name) and node.func.id == "open":
            mode = _literal_open_mode(node)
            if mode is not None and any(flag in mode for flag in "wax"):
                pattern = f"builtin_open_call:{mode}"
        else:
            qualified = _qualified_name(node.func)
            if qualified in {"json.dump", "yaml.dump", "pickle.dump"}:
                pattern = f"module_dump_call:{qualified}"

        if pattern is not None:
            matches.append(
                DurableSinkMatch(
                    line=node.lineno,
                    column=node.col_offset,
                    pattern=pattern,
                )
            )

    return tuple(sorted(matches))


def classify_durable_export(source: str) -> dict[str, Any]:
    matches = durable_export_matches(source)
    value = {
        "label": PRESENT if matches else ABSENT,
        "match_count": len(matches),
        "matches": [asdict(match) for match in matches],
    }
    value["digest"] = canonical_digest(value)
    return value


def expected_next_experiment(label: str) -> str:
    if label == PRESENT:
        return ROUND_TRIP
    if label == ABSENT:
        return SEEDED_EXPORT
    raise ValueError(f"unsupported reveal label: {label}")


def score_binary_forecast(
    *,
    forecast_label: str,
    probability_present: float,
    next_experiment_choice: str,
    reveal_label: str,
) -> dict[str, Any]:
    if forecast_label not in {PRESENT, ABSENT}:
        raise ValueError(f"unsupported forecast label: {forecast_label}")
    if reveal_label not in {PRESENT, ABSENT}:
        raise ValueError(f"unsupported reveal label: {reveal_label}")
    if not 0.0 <= probability_present <= 1.0:
        raise ValueError("probability_present must be in [0, 1]")
    if next_experiment_choice not in {ROUND_TRIP, SEEDED_EXPORT}:
        raise ValueError(f"unsupported next experiment: {next_experiment_choice}")

    truth = 1.0 if reveal_label == PRESENT else 0.0
    label_correct = int(forecast_label == reveal_label)
    experiment_correct = int(next_experiment_choice == expected_next_experiment(reveal_label))
    value = {
        "forecast_label": forecast_label,
        "reveal_label": reveal_label,
        "label_correct": label_correct,
        "probability_present": probability_present,
        "confidence_brier": (probability_present - truth) ** 2,
        "next_experiment_choice": next_experiment_choice,
        "expected_next_experiment": expected_next_experiment(reveal_label),
        "next_experiment_correct": experiment_correct,
        "composite_score": 0.7 * label_correct + 0.3 * experiment_correct,
    }
    value["digest"] = canonical_digest(value)
    return value


def validate_prediction_artifact(value: dict[str, Any]) -> dict[str, Any]:
    forecast = value.get("forecast", {})
    baseline = value.get("same_input_baseline", {})
    attest = value.get("pre_reveal_attestation", {})
    errors: list[str] = []

    if forecast.get("label") not in {PRESENT, ABSENT}:
        errors.append("forecast label is invalid")
    probability = forecast.get("probability_durable_export_present")
    if not isinstance(probability, (int, float)) or not 0.0 <= probability <= 1.0:
        errors.append("forecast probability is invalid")
    if forecast.get("next_experiment_choice") not in {ROUND_TRIP, SEEDED_EXPORT}:
        errors.append("forecast next experiment is invalid")
    if baseline.get("extra_context_used") is not False:
        errors.append("baseline must use no extra context")
    if attest.get("primary_source_content_in_artifact") is not False:
        errors.append("prediction artifact contains primary source content")
    if attest.get("clean_g1_outcome_text_in_artifact") is not False:
        errors.append("prediction artifact contains clean_g1 outcome text")
    if attest.get("revealed_label_known") is not False:
        errors.append("prediction was not frozen before reveal")

    return {"valid": not errors, "errors": errors}


def _main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic held-out scientific forecast utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)
    classify = subparsers.add_parser("classify")
    classify.add_argument("source", type=Path)
    args = parser.parse_args()

    if args.command == "classify":
        result = classify_durable_export(args.source.read_text(encoding="utf-8"))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(_main())
