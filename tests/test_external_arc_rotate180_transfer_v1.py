from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "agi" / "experiments" / "external_arc_rotate180_transfer_v1.py"
SPEC = importlib.util.spec_from_file_location("external_arc_rotate180_transfer_v1", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
EXCLUDED_TASK_IDS = MODULE.EXCLUDED_TASK_IDS
contains_grid = MODULE.contains_grid
rotate_180 = MODULE.rotate_180
score = MODULE.score
select_candidates = MODULE.select_candidates
verify_binding = MODULE.verify_binding
digest = MODULE.digest


def record(task_id: str, train_input: list[list[int]], train_output: list[list[int]], test_input: list[list[int]]) -> dict:
    return {
        "task_id": task_id,
        "sanitized": {
            "train": [{"input": train_input, "output": train_output}],
            "test": [{"input": test_input}],
        },
    }


def test_rotate_180_and_exact_score() -> None:
    grid = [[1, 2, 3], [4, 5, 6]]
    expected = [[6, 5, 4], [3, 2, 1]]
    assert rotate_180(grid) == expected
    assert score(rotate_180(grid), expected)["exact_grid"] is True
    assert score(grid, expected)["exact_grid"] is False


def test_selector_is_lexicographic_and_fails_closed() -> None:
    valid_a = record("bbbbbbbb", [[1, 2], [3, 4]], [[4, 3], [2, 1]], [[1, 2], [3, 4]])
    valid_b = record("aaaaaaaa", [[0, 1, 2]], [[2, 1, 0]], [[4, 5, 6]])
    invariant = record("cccccccc", [[1, 2]], [[2, 1]], [[7, 7]])
    wrong_family = record("dddddddd", [[1, 2]], [[1, 2]], [[3, 4]])
    excluded = record(next(iter(EXCLUDED_TASK_IDS)), [[1, 2]], [[2, 1]], [[3, 4]])
    selected = select_candidates([valid_a, invariant, wrong_family, valid_b, excluded], prior_ids=set())
    assert [item["task_id"] for item in selected] == ["aaaaaaaa", "bbbbbbbb"]
    assert select_candidates([valid_a, valid_b], prior_ids={"aaaaaaaa"})[0]["task_id"] == "bbbbbbbb"


def test_grid_leakage_detection() -> None:
    assert contains_grid({"answer": [[1, 2], [3, 4]]}) is True
    assert contains_grid({"rule_id": "matrix_rotate_180_v1", "digests": ["abc"]}) is False


def test_binding_controls_fail_closed() -> None:
    base = {
        "source_commit": "399030444e0ab0cc8b4e199870fb20b863846f34",
        "task_id": "task-b",
        "commitment": "c" * 64,
        "selector_digest": "s" * 64,
        "family": "matrix_rotate_180",
        "expected_task_id": "task-b",
        "expected_commitment": "c" * 64,
        "expected_selector_digest": "s" * 64,
    }
    assert verify_binding(**base) is True
    for key, changed in (
        ("source_commit", "0" * 40),
        ("task_id", "task-a"),
        ("commitment", "0" * 64),
        ("selector_digest", "0" * 64),
        ("family", "matrix_transpose"),
    ):
        probe = dict(base)
        probe[key] = changed
        assert verify_binding(**probe) is False


def test_frozen_artifact_chain_is_exact_and_bounded() -> None:
    root = Path(__file__).parents[1] / "artifacts"
    stem = "unit-generation29-external-arc-rotate180-transfer-v1"

    def read(suffix: str) -> dict:
        return json.loads((root / f"{stem}-{suffix}.json").read_text())

    precommit = read("precommit")
    baseline = read("baseline-predictions")
    learning = read("learning-record")
    adapted = read("adapted-task-b-prediction")
    result = read("result")

    assert precommit["selector"]["selected_task_ids"] == ["3c9b0459", "6150a2bd"]
    assert [task["source_blob_sha"] for task in precommit["tasks"]] == [
        "bc5aef84b55ae79464c77627e426d5faac414a65",
        "88b20f5b281380120bde8719c4022beafa20486a",
    ]
    assert baseline["precommit_sha256"] == digest(precommit)
    assert adapted["learning_record_sha256"] == digest(learning)
    assert learning["source_task_id"] == "3c9b0459"
    assert learning["rule_id"] == "matrix_rotate_180_v1"
    assert contains_grid(learning) is False
    assert adapted["task_id"] == "6150a2bd"
    assert adapted["task_b_output_unrevealed"] is True
    assert result["scores"]["baseline_task_a"]["exact_grid"] is False
    assert result["scores"]["baseline_task_b"]["exact_grid"] is False
    assert result["scores"]["adapted_task_b"]["exact_grid"] is True
    assert result["scores"]["strict_task_b_improvement"] is True
    assert all(result["negative_controls"].values())
    assert result["replay"] == {
        "digest": result["replay"]["digest"],
        "duplicate_accept": False,
        "first_accept": True,
    }
    assert result["source_write_audit"]["before"] == result["source_write_audit"]["after"]
    assert result["unit_verdict"] == "PASS"
    assert result["candidate_activation"] is False
    assert result["upper_objective_achieved"] is False
    assert result["user_level_objective_achieved"] is False
    assert result["agi_achieved"] is False
    assert [
        precommit["recorded_at"],
        baseline["recorded_at"],
        learning["recorded_at"],
        adapted["recorded_at"],
        result["recorded_at"],
    ] == sorted(
        [
            precommit["recorded_at"],
            baseline["recorded_at"],
            learning["recorded_at"],
            adapted["recorded_at"],
            result["recorded_at"],
        ]
    )
