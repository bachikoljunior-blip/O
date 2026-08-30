from __future__ import annotations

import pytest

from agi.scientific_forecast import (
    ABSENT,
    PRESENT,
    ROUND_TRIP,
    SEEDED_EXPORT,
    classify_durable_export,
    durable_export_matches,
    score_binary_forecast,
    validate_prediction_artifact,
)


@pytest.mark.parametrize(
    ("source", "pattern"),
    [
        ("from pathlib import Path\nPath('x').write_text('y')\n", "attribute_call:write_text"),
        ("from pathlib import Path\nPath('x').write_bytes(b'y')\n", "attribute_call:write_bytes"),
        ("open('x', 'w').write('y')\n", "builtin_open_call:w"),
        ("open('x', mode='ab').write(b'y')\n", "builtin_open_call:ab"),
        ("import json\njson.dump({'x': 1}, sink)\n", "module_dump_call:json.dump"),
        ("import yaml\nyaml.dump({'x': 1}, sink)\n", "module_dump_call:yaml.dump"),
        ("import pickle\npickle.dump({'x': 1}, sink)\n", "module_dump_call:pickle.dump"),
    ],
)
def test_frozen_ast_rule_detects_each_durable_sink(source: str, pattern: str):
    matches = durable_export_matches(source)
    assert [match.pattern for match in matches] == [pattern]
    assert classify_durable_export(source)["label"] == PRESENT


def test_frozen_ast_rule_ignores_noncommitted_and_read_only_surfaces():
    source = """
from pathlib import Path

def save_certificate(value, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    return value

def read_only(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()
"""
    result = classify_durable_export(source)
    assert result["label"] == ABSENT
    assert result["match_count"] == 0
    assert result == classify_durable_export(source)


def test_match_order_and_digest_are_replay_stable():
    source = """
import json
from pathlib import Path
Path("b").write_bytes(b"x")
json.dump({"x": 1}, sink)
Path("a").write_text("x")
"""
    first = classify_durable_export(source)
    second = classify_durable_export(source)
    assert first == second
    assert [item["line"] for item in first["matches"]] == sorted(
        item["line"] for item in first["matches"]
    )


def test_scoring_is_deterministic_for_forecast_and_same_input_baseline():
    forecast = score_binary_forecast(
        forecast_label=ABSENT,
        probability_present=0.28,
        next_experiment_choice=SEEDED_EXPORT,
        reveal_label=ABSENT,
    )
    baseline = score_binary_forecast(
        forecast_label=PRESENT,
        probability_present=0.60,
        next_experiment_choice=ROUND_TRIP,
        reveal_label=ABSENT,
    )
    assert forecast["label_correct"] == 1
    assert forecast["next_experiment_correct"] == 1
    assert forecast["confidence_brier"] == pytest.approx(0.0784)
    assert forecast["composite_score"] == pytest.approx(1.0)
    assert baseline["label_correct"] == 0
    assert baseline["next_experiment_correct"] == 0
    assert baseline["confidence_brier"] == pytest.approx(0.36)
    assert baseline["composite_score"] == pytest.approx(0.0)


def test_scoring_rejects_invalid_probability():
    with pytest.raises(ValueError, match="probability_present"):
        score_binary_forecast(
            forecast_label=PRESENT,
            probability_present=1.1,
            next_experiment_choice=ROUND_TRIP,
            reveal_label=PRESENT,
        )


def test_prediction_artifact_requires_pre_reveal_redaction():
    artifact = {
        "forecast": {
            "label": ABSENT,
            "probability_durable_export_present": 0.28,
            "next_experiment_choice": SEEDED_EXPORT,
        },
        "same_input_baseline": {"extra_context_used": False},
        "pre_reveal_attestation": {
            "primary_source_content_in_artifact": False,
            "clean_g1_outcome_text_in_artifact": False,
            "revealed_label_known": False,
        },
    }
    assert validate_prediction_artifact(artifact) == {"valid": True, "errors": []}
    artifact["pre_reveal_attestation"]["revealed_label_known"] = True
    invalid = validate_prediction_artifact(artifact)
    assert invalid["valid"] is False
    assert invalid["errors"] == ["prediction was not frozen before reveal"]
