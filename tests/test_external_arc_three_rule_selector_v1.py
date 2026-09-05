from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "agi" / "experiments" / "external_arc_three_rule_selector_v1.py"
SPEC = importlib.util.spec_from_file_location("external_arc_three_rule_selector_v1", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
eligible_records = MODULE.eligible_records
matrix_rotate_180_v1 = MODULE.matrix_rotate_180_v1
matrix_transpose_v1 = MODULE.matrix_transpose_v1
nonzero_bounding_box_v1 = MODULE.nonzero_bounding_box_v1
score = MODULE.score
training_scores = MODULE.training_scores
unique_zero_error_rule = MODULE.unique_zero_error_rule
prior_task_ids = MODULE.prior_task_ids


def test_three_frozen_rules_have_expected_behavior() -> None:
    grid = [[1, 2, 0], [3, 4, 0], [0, 0, 0]]
    assert matrix_transpose_v1(grid) == [[1, 3, 0], [2, 4, 0], [0, 0, 0]]
    assert matrix_rotate_180_v1(grid) == [[0, 0, 0], [0, 4, 3], [0, 2, 1]]
    assert nonzero_bounding_box_v1(grid) == [[1, 2], [3, 4]]
    assert nonzero_bounding_box_v1([[0, 0]]) is None


def test_unique_zero_error_winner_requires_exactly_one() -> None:
    training = [{"input": [[1, 2], [3, 4]], "output": [[1, 3], [2, 4]]}]
    scores = training_scores(training)
    assert unique_zero_error_rule(scores) == "matrix_transpose_v1"
    tied = [
        {"rule_id": "a", "zero_training_error": True},
        {"rule_id": "b", "zero_training_error": True},
    ]
    assert unique_zero_error_rule(tied) is None
    assert unique_zero_error_rule([]) is None


def test_score_fails_shape_mismatch_closed() -> None:
    observed = score([[1, 2]], [[1], [2]])
    assert observed["shape_match"] is False
    assert observed["exact_cell_correct"] == 0
    assert observed["exact_grid"] is False


def test_selector_excludes_prior_ids_and_never_needs_test_output() -> None:
    record = {
        "task_id": "fresh-task",
        "sanitized": {
            "train": [{"input": [[1, 2], [3, 4]], "output": [[1, 3], [2, 4]]}],
            "test": [{"input": [[5, 6], [7, 8]]}],
        },
    }
    assert eligible_records([record], set())[0]["task_id"] == "fresh-task"
    assert eligible_records([record], {"fresh-task"}) == []


def test_selector_rejects_nonunique_training_winner() -> None:
    symmetric = {
        "task_id": "ambiguous-task",
        "sanitized": {
            "train": [{"input": [[1]], "output": [[1]]}],
            "test": [{"input": [[1, 2], [3, 4]]}],
        },
    }
    assert eligible_records([symmetric], set()) == []


def test_prior_use_scan_returns_exact_ids_not_ripgrep_help(tmp_path: Path) -> None:
    (tmp_path / "receipt.json").write_text('{"task_id":"1cf80156"}\n')
    catalog = [{"task_id": "1cf80156"}, {"task_id": "deadbeef"}]
    assert prior_task_ids(catalog, tmp_path) == {"1cf80156"}


def test_prior_use_scan_works_without_external_commands(tmp_path: Path) -> None:
    (tmp_path / "receipt.json").write_text('{"task_id":"1cf80156"}\n')
    with patch.dict(os.environ, {"PATH": ""}):
        assert prior_task_ids([{"task_id": "1cf80156"}, {"task_id": "deadbeef"}], tmp_path) == {"1cf80156"}


def test_prior_use_scan_includes_ignored_history_and_chunk_boundaries(tmp_path: Path) -> None:
    history = tmp_path / ".continual" / "runs"
    history.mkdir(parents=True)
    (tmp_path / ".gitignore").write_text(".continual/runs/\n")
    (history / "receipt.bin").write_bytes(b"\x00" * (64 * 1024 - 4) + b"1cf80156")
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "internal").write_text("deadbeef")
    assert prior_task_ids([{"task_id": "1cf80156"}, {"task_id": "deadbeef"}], tmp_path) == {"1cf80156"}


def test_prior_use_scan_read_error_cannot_declare_task_fresh(tmp_path: Path) -> None:
    (tmp_path / "receipt.json").write_text('{"task_id":"1cf80156"}\n')
    with patch.object(Path, "open", side_effect=PermissionError("unreadable prior record")):
        try:
            prior_task_ids([{"task_id": "1cf80156"}], tmp_path)
        except PermissionError:
            pass
        else:
            raise AssertionError("An incomplete prior-use scan must fail closed")


def test_frozen_attempt_is_fail_closed_without_retry() -> None:
    root = Path(__file__).parents[1] / "artifacts"
    stem = "unit-generation29-external-arc-three-rule-selector-v1"
    result = json.loads((root / f"{stem}-result.json").read_text())
    correction = json.loads((root / f"{stem}-selector-correction.json").read_text())
    assert result["unit_verdict"] == "FAIL"
    assert result["failure_condition"] == "selector_prior_use_scan_failed_open"
    assert result["controls"]["prior_task_reuse_rejected"] is False
    assert result["same_unit_retry_performed"] is False
    assert result["candidate_activation"] is False
    assert result["agi_achieved"] is False
    assert correction["repair"]["same_unit_retry_performed"] is False
    assert correction["adjudication"]["unit_verdict"] == "FAIL"
