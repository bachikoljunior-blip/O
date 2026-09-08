from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "agi" / "experiments" / "external_arc_bbox_transfer_v1.py"
SPEC = importlib.util.spec_from_file_location("external_arc_bbox_transfer_v1", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
EXCLUDED_TASK_IDS = MODULE.EXCLUDED_TASK_IDS
minimal_nonzero_bbox = MODULE.minimal_nonzero_bbox
select_candidates = MODULE.select_candidates


def record(task_id: str, train_input: list[list[int]], train_output: list[list[int]], test_input: list[list[int]]) -> dict:
    return {
        "task_id": task_id,
        "source_split": "training",
        "source_path": f"data/training/{task_id}.json",
        "source_blob_sha": "a" * 40,
        "sanitized": {
            "train": [{"input": train_input, "output": train_output}],
            "test": [{"input": test_input}],
        },
    }


def test_minimal_nonzero_bbox_preserves_values_and_relative_positions() -> None:
    grid = [[0, 0, 0, 0], [0, 2, 0, 3], [0, 0, 4, 0], [0, 0, 0, 0]]
    assert minimal_nonzero_bbox(grid) == [[2, 0, 3], [0, 4, 0]]
    assert minimal_nonzero_bbox([[0, 0], [0, 0]]) is None


def test_selector_is_lexicographic_and_requires_nonempty_changed_crop() -> None:
    valid_b = record("bbbbbbbb", [[0, 1, 0]], [[1]], [[0, 2, 0]])
    valid_a = record("aaaaaaaa", [[0, 3, 0]], [[3]], [[0, 4, 0]])
    wrong_family = record("cccccccc", [[0, 1, 0]], [[0, 1]], [[0, 2, 0]])
    empty = record("dddddddd", [[0]], [[0]], [[0]])
    invariant = record("eeeeeeee", [[1]], [[1]], [[2]])
    excluded = record(next(iter(EXCLUDED_TASK_IDS)), [[0, 1, 0]], [[1]], [[0, 2, 0]])
    selected = select_candidates([valid_b, wrong_family, empty, invariant, valid_a, excluded], prior_ids=set())
    assert [item["task_id"] for item in selected] == ["aaaaaaaa", "bbbbbbbb"]
    assert [item["task_id"] for item in select_candidates([valid_a, valid_b], {"aaaaaaaa"})] == ["bbbbbbbb"]


def test_selector_fails_closed_when_fewer_than_two_candidates() -> None:
    only = record("aaaaaaaa", [[0, 5, 0]], [[5]], [[0, 6, 0]])
    candidates = select_candidates([only], prior_ids=set())
    assert len(candidates) == 1
    assert len(candidates) < 2


def test_frozen_corpus_result_fails_without_answer_reveal_or_substitution() -> None:
    root = Path(__file__).parents[1] / "artifacts"
    stem = "unit-generation29-external-arc-nonzero-bounding-box-transfer-v1"
    precommit = json.loads((root / f"{stem}-precommit.json").read_text())
    result = json.loads((root / f"{stem}-result.json").read_text())

    assert precommit["selector"]["candidate_count"] == 1
    assert precommit["selector"]["candidate_task_ids"] == ["1cf80156"]
    assert precommit["selector"]["selected_task_ids"] == []
    assert precommit["answer_access"] == {
        "holder_commit_called": False,
        "holder_reveal_called": False,
        "test_output_cells_observed": False,
    }
    assert result["failure_condition"] == "fewer_than_two_eligible_tasks"
    assert result["failure_observed"] is True
    assert result["unit_verdict"] == "FAIL"
    assert result["selected_task_ids"] == []
    assert result["answer_reveal_attempted"] is False
    assert result["learning_record_created"] is False
    assert result["task_b_prediction_frozen"] is False
    assert result["scoring_performed"] is False
    assert all(result["controls"].values())
    assert result["source_write_audit"]["before"] == result["source_write_audit"]["after"]
    assert result["candidate_activation"] is False
    assert result["production_routing"] is False
    assert result["upper_objective_verdict"] == "FAIL"
    assert result["user_level_verdict"] == "FAIL"
    assert result["agi_achieved"] is False
