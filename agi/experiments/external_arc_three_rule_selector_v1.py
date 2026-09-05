from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable


COMMIT = "399030444e0ab0cc8b4e199870fb20b863846f34"
UNIT = "unit-generation29-external-arc-three-rule-selector-v1"
IDEMPOTENCY_KEY = "o-work-gen29:arc-three-rule-selector:39903044:v1"
EXCLUDED_TASK_IDS = frozenset({"74dd1130", "9dfd6313", "3c9b0459", "6150a2bd"})
Grid = list[list[int]]
Rule = Callable[[Grid], Grid | None]


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def matrix_transpose_v1(grid: Grid) -> Grid:
    return [list(column) for column in zip(*grid)]


def matrix_rotate_180_v1(grid: Grid) -> Grid:
    return [list(reversed(row)) for row in reversed(grid)]


def nonzero_bounding_box_v1(grid: Grid) -> Grid | None:
    points = [
        (row_index, column_index)
        for row_index, row in enumerate(grid)
        for column_index, value in enumerate(row)
        if value != 0
    ]
    if not points:
        return None
    row_start = min(point[0] for point in points)
    row_stop = max(point[0] for point in points) + 1
    column_start = min(point[1] for point in points)
    column_stop = max(point[1] for point in points) + 1
    return [row[column_start:column_stop] for row in grid[row_start:row_stop]]


RULES: tuple[tuple[str, Rule, str], ...] = (
    (
        "matrix_transpose_v1",
        matrix_transpose_v1,
        "Return the matrix transpose, preserving every cell value.",
    ),
    (
        "matrix_rotate_180_v1",
        matrix_rotate_180_v1,
        "Reverse row order and reverse cell order within every row.",
    ),
    (
        "nonzero_bounding_box_v1",
        nonzero_bounding_box_v1,
        "Return the minimal rectangular crop containing every nonzero input cell; fail on an all-zero input.",
    ),
)


def rule_manifest() -> list[dict[str, str]]:
    return [
        {
            "rule_id": rule_id,
            "specification": specification,
            "implementation_source_sha256": hashlib.sha256(inspect.getsource(rule).encode()).hexdigest(),
        }
        for rule_id, rule, specification in RULES
    ]


def shape(grid: Grid) -> list[int]:
    return [len(grid), len(grid[0]) if grid else 0]


def score(prediction: Grid | None, target: Grid) -> dict[str, object]:
    total = sum(len(row) for row in target)
    prediction_shape = shape(prediction) if prediction is not None else None
    shape_match = prediction is not None and prediction_shape == shape(target)
    correct = (
        sum(p == t for prow, trow in zip(prediction, target) for p, t in zip(prow, trow))
        if shape_match and prediction is not None
        else 0
    )
    return {
        "prediction_shape": prediction_shape,
        "target_shape": shape(target),
        "shape_match": shape_match,
        "exact_cell_correct": correct,
        "exact_cell_total": total,
        "exact_cell_accuracy": correct / total if total else 0.0,
        "exact_grid": bool(shape_match and correct == total),
    }


def training_scores(training: list[dict[str, Grid]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for rule_id, rule, _ in RULES:
        pair_scores = [score(rule(pair["input"]), pair["output"]) for pair in training]
        rows.append(
            {
                "rule_id": rule_id,
                "training_pair_count": len(training),
                "zero_training_error": bool(training) and all(item["exact_grid"] for item in pair_scores),
                "pair_scores": pair_scores,
            }
        )
    return rows


def unique_zero_error_rule(scores: list[dict[str, object]]) -> str | None:
    winners = [str(item["rule_id"]) for item in scores if item["zero_training_error"] is True]
    return winners[0] if len(winners) == 1 else None


def prior_task_ids(catalog: list[dict[str, object]], o_root: Path) -> set[str]:
    task_ids = sorted(str(record["task_id"]) for record in catalog)
    scan = subprocess.run(
        [
            "rg",
            "--hidden",
            "-F",
            "--no-filename",
            "-o",
            "-f",
            "-",
            "--glob",
            "!.git/**",
            str(o_root),
        ],
        input="\n".join(task_ids) + "\n",
        text=True,
        stdout=subprocess.PIPE,
        check=False,
    )
    if scan.returncode not in (0, 1):
        raise RuntimeError(f"prior-use scan failed with exit {scan.returncode}")
    return set(scan.stdout.splitlines())


def eligible_records(catalog: list[dict[str, object]], prior_ids: set[str]) -> list[dict[str, object]]:
    eligible: list[dict[str, object]] = []
    for record in catalog:
        task_id = str(record["task_id"])
        if task_id in EXCLUDED_TASK_IDS or task_id in prior_ids:
            continue
        sanitized = record.get("sanitized")
        if not isinstance(sanitized, dict) or len(sanitized.get("test", [])) != 1:
            continue
        training = sanitized.get("train", [])
        if not training:
            continue
        scores = training_scores(training)
        winner = unique_zero_error_rule(scores)
        if winner is None:
            continue
        test_input = sanitized["test"][0]["input"]
        rule = {rule_id: implementation for rule_id, implementation, _ in RULES}[winner]
        prediction = rule(test_input)
        if prediction is None or prediction == test_input:
            continue
        eligible.append({**record, "training_scores": scores, "winner": winner})
    return sorted(eligible, key=lambda item: str(item["task_id"]))


def source_snapshot(source: Path) -> dict[str, object]:
    status = subprocess.check_output(["git", "status", "--porcelain=v1", "-uno"], cwd=source)
    count = subprocess.check_output(["git", "count-objects", "-v"], cwd=source)
    return {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip(),
        "status_sha256": hashlib.sha256(status).hexdigest(),
        "status_line_count": len(status.splitlines()),
        "object_count_sha256": hashlib.sha256(count).hexdigest(),
    }


def holder(holder_path: Path, *args: str) -> object:
    raw = subprocess.check_output(
        ["/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python", str(holder_path), *args],
        text=True,
    )
    return json.loads(raw)


def write_artifact(out: Path, name: str, value: object) -> dict[str, str]:
    path = out / name
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    parsed = json.loads(path.read_text())
    if parsed != value:
        raise RuntimeError(f"artifact readback mismatch: {name}")
    return {
        "path": str(path),
        "git_blob_sha": subprocess.check_output(["git", "hash-object", str(path)], text=True).strip(),
        "sha256": digest(value),
    }


def verify_binding(
    *,
    source_commit: str,
    task_id: str,
    selector_sha256: str,
    rule_manifest_sha256: str,
    holder_commitment: str,
    expected_task_id: str,
    expected_selector_sha256: str,
    expected_rule_manifest_sha256: str,
    expected_holder_commitment: str,
    prior_task_reuse: bool = False,
    leaked_heldout_cells: bool = False,
) -> bool:
    return (
        source_commit == COMMIT
        and task_id == expected_task_id
        and selector_sha256 == expected_selector_sha256
        and rule_manifest_sha256 == expected_rule_manifest_sha256
        and holder_commitment == expected_holder_commitment
        and not prior_task_reuse
        and not leaked_heldout_cells
    )


def replay_once(ledger: set[str], key: str, effect_digest: str) -> bool:
    identity = digest({"idempotency_key": key, "effect_digest": effect_digest})
    if identity in ledger:
        return False
    ledger.add(identity)
    return True


def execute(source: Path, o_root: Path, holder_path: Path, out: Path) -> dict[str, object]:
    out.mkdir(parents=True, exist_ok=True)
    source_before = source_snapshot(source)
    if source_before["head"] != COMMIT:
        raise RuntimeError("source commit mismatch")
    license_receipt = holder(holder_path, "license")
    if not isinstance(license_receipt, dict) or not license_receipt["apache_2_0_marker"]:
        raise RuntimeError("license mismatch")
    catalog = holder(holder_path, "catalog")
    if not isinstance(catalog, list):
        raise RuntimeError("holder catalog is not a list")

    prior_ids = prior_task_ids(catalog, o_root)
    candidates = eligible_records(catalog, prior_ids)
    selected = candidates[0] if candidates else None
    rules = rule_manifest()
    rules_sha256 = digest(rules)
    selector = {
        "algorithm": "lexicographically first public training-or-evaluation task with exactly one test case, no prior exact task-id occurrence in O, explicit exclusion of 74dd1130, 9dfd6313, 3c9b0459, and 6150a2bd, exactly one of the three fixed rules exact on every training pair, and a selected-rule test prediction distinct from identity",
        "answer_excluding_inputs": ["train[*].input", "train[*].output", "test[0].input"],
        "excluded_inputs": ["test[0].output"],
        "candidate_count": len(candidates),
        "candidate_task_ids": [str(record["task_id"]) for record in candidates],
        "selected_task_id": str(selected["task_id"]) if selected else None,
        "excluded_task_ids": sorted(EXCLUDED_TASK_IDS),
    }
    selector_sha256 = digest(selector)
    commitment = (
        holder(holder_path, "commit", "--task-id", str(selected["task_id"]))
        if selected is not None
        else {}
    )
    selected_id = str(selected["task_id"]) if selected else None
    selected_commitment = (
        commitment[selected_id]["output_commitment_sha256"] if selected_id is not None else None
    )

    precommit = {
        "schema_version": 1,
        "record_type": "external_arc_three_rule_selector_precommit",
        "recorded_at": now(),
        "unit_id": UNIT,
        "idempotency_key": IDEMPOTENCY_KEY,
        "source": {
            "repository": "fchollet/ARC-AGI",
            "commit": COMMIT,
            "license_path": license_receipt["path"],
            "license_sha256": license_receipt["sha256"],
            "license": "Apache-2.0",
        },
        "selector": selector,
        "selector_sha256": selector_sha256,
        "rule_manifest": rules,
        "rule_manifest_sha256": rules_sha256,
        "orchestrator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "holder_sha256": hashlib.sha256(holder_path.read_bytes()).hexdigest(),
        "selected_task": None
        if selected is None
        else {
            "task_id": selected_id,
            "source_split": selected["source_split"],
            "source_path": selected["source_path"],
            "source_blob_sha": selected["source_blob_sha"],
            "sanitized_projection_sha256": digest(selected["sanitized"]),
            "holder_output_commitment_sha256": selected_commitment,
            "prior_use_scan_match": selected_id in prior_ids,
        },
        "scorer": {
            "exact_grid": "shape and every cell must match",
            "exact_cell": "shape mismatch scores zero correct cells; otherwise count equal cells",
            "winner": "exactly one fixed rule must have exact_grid true on every training pair",
        },
        "causal_order": [
            "source/license/rules/selector/task identity frozen",
            "training scores and unique winner frozen",
            "identity baseline and selected-rule held-out prediction frozen and exact-read back",
            "held-out output revealed by commitment",
            "prediction scored and replayed",
        ],
        "answer_access": {"holder_reveal_called": False, "test_output_cells_observed": False},
        "failure_condition": "no_eligible_unique_training_winner" if selected is None else None,
        "claim_boundary": "Exactly one answer-excluding ARC task selection event over three fixed rules; no generalized ARC, production, Candidate activation, AGI, user-level, task-level, or upper-objective inference.",
    }
    artifacts: dict[str, dict[str, str]] = {}
    artifacts["precommit"] = write_artifact(
        out, "unit-generation29-external-arc-three-rule-selector-v1-precommit.json", precommit
    )

    if selected is None:
        source_after = source_snapshot(source)
        result = {
            "schema_version": 1,
            "record_type": "external_arc_three_rule_selector_result",
            "recorded_at": now(),
            "unit_id": UNIT,
            "idempotency_key": IDEMPOTENCY_KEY,
            "unit_verdict": "FAIL",
            "failure_condition": "no_eligible_unique_training_winner",
            "candidate_count": 0,
            "answer_reveal_attempted": False,
            "source_write_audit": {"before": source_before, "after": source_after, "zero_upstream_writes": source_before == source_after},
            "task_completion_verdict": "FAIL",
            "user_level_verdict": "FAIL",
            "upper_objective_verdict": "FAIL",
            "candidate_activation": False,
            "production_routing": False,
            "agi_achieved": False,
            "claim_boundary": precommit["claim_boundary"],
        }
        artifacts["result"] = write_artifact(
            out, "unit-generation29-external-arc-three-rule-selector-v1-result.json", result
        )
        return {"result": result, "artifacts": artifacts}

    scores_artifact = {
        "schema_version": 1,
        "record_type": "frozen_three_rule_training_scores",
        "recorded_at": now(),
        "unit_id": UNIT,
        "task_id": selected_id,
        "selector_sha256": selector_sha256,
        "rule_manifest_sha256": rules_sha256,
        "scores": selected["training_scores"],
        "zero_error_winners": [
            item["rule_id"] for item in selected["training_scores"] if item["zero_training_error"] is True
        ],
        "unique_zero_error_winner": selected["winner"],
        "heldout_output_unavailable": True,
    }
    artifacts["training_scores"] = write_artifact(
        out, "unit-generation29-external-arc-three-rule-selector-v1-training-scores.json", scores_artifact
    )
    rule_by_id = {rule_id: rule for rule_id, rule, _ in RULES}
    test_input = selected["sanitized"]["test"][0]["input"]
    baseline = {
        "schema_version": 1,
        "record_type": "frozen_identity_baseline_prediction",
        "recorded_at": now(),
        "unit_id": UNIT,
        "task_id": selected_id,
        "solver": "identity_v1",
        "prediction": test_input,
        "heldout_output_unavailable": True,
    }
    artifacts["baseline"] = write_artifact(
        out, "unit-generation29-external-arc-three-rule-selector-v1-identity-baseline.json", baseline
    )
    selected_prediction = {
        "schema_version": 1,
        "record_type": "frozen_selected_rule_prediction",
        "recorded_at": now(),
        "unit_id": UNIT,
        "task_id": selected_id,
        "rule_id": selected["winner"],
        "selector_sha256": selector_sha256,
        "rule_manifest_sha256": rules_sha256,
        "training_scores_sha256": artifacts["training_scores"]["sha256"],
        "prediction": rule_by_id[str(selected["winner"])](test_input),
        "heldout_output_unavailable": True,
    }
    artifacts["selected_prediction"] = write_artifact(
        out, "unit-generation29-external-arc-three-rule-selector-v1-selected-prediction.json", selected_prediction
    )
    causal_receipt = {
        "schema_version": 1,
        "record_type": "pre_reveal_causal_order_receipt",
        "recorded_at": now(),
        "unit_id": UNIT,
        "task_id": selected_id,
        "holder_commitment_sha256": selected_commitment,
        "precommit": artifacts["precommit"],
        "training_scores": artifacts["training_scores"],
        "identity_baseline": artifacts["baseline"],
        "selected_prediction": artifacts["selected_prediction"],
        "all_exact_read_back_before_reveal": True,
        "holder_reveal_called": False,
        "test_output_cells_observed": False,
    }
    artifacts["causal_receipt"] = write_artifact(
        out, "unit-generation29-external-arc-three-rule-selector-v1-causal-order.json", causal_receipt
    )

    reveal = holder(
        holder_path,
        "reveal",
        "--task-id",
        selected_id,
        "--expected-commitment",
        str(selected_commitment),
    )
    target = reveal["outputs"][0]
    baseline_score = score(baseline["prediction"], target)
    selected_score = score(selected_prediction["prediction"], target)
    bind = lambda **changes: verify_binding(
        source_commit=str(changes.get("source_commit", COMMIT)),
        task_id=str(changes.get("task_id", selected_id)),
        selector_sha256=str(changes.get("selector_sha256", selector_sha256)),
        rule_manifest_sha256=str(changes.get("rule_manifest_sha256", rules_sha256)),
        holder_commitment=str(changes.get("holder_commitment", selected_commitment)),
        expected_task_id=selected_id,
        expected_selector_sha256=selector_sha256,
        expected_rule_manifest_sha256=rules_sha256,
        expected_holder_commitment=str(selected_commitment),
        prior_task_reuse=bool(changes.get("prior_task_reuse", False)),
        leaked_heldout_cells=bool(changes.get("leaked_heldout_cells", False)),
    )
    artificial_tie = [
        {"rule_id": "matrix_transpose_v1", "zero_training_error": True},
        {"rule_id": "matrix_rotate_180_v1", "zero_training_error": True},
        {"rule_id": "nonzero_bounding_box_v1", "zero_training_error": False},
    ]
    effect_digest = digest(
        {
            "task_id": selected_id,
            "rule_id": selected["winner"],
            "prediction_sha256": artifacts["selected_prediction"]["sha256"],
            "target_commitment_sha256": selected_commitment,
        }
    )
    replay_ledger: set[str] = set()
    first_replay_accepted = replay_once(replay_ledger, IDEMPOTENCY_KEY, effect_digest)
    duplicate_replay_accepted = replay_once(replay_ledger, IDEMPOTENCY_KEY, effect_digest)
    replay_prediction = rule_by_id[str(selected["winner"])](test_input)
    controls = {
        "changed_source_commit_rejected": not bind(source_commit="0" * 40),
        "changed_selector_rejected": not bind(selector_sha256="0" * 64),
        "prior_task_reuse_rejected": not bind(prior_task_reuse=True),
        "changed_hypothesis_implementation_rejected": not bind(rule_manifest_sha256="0" * 64),
        "non_unique_training_winner_rejected": unique_zero_error_rule(artificial_tie) is None,
        "leaked_heldout_cells_rejected": not bind(leaked_heldout_cells=True),
        "holder_mismatch_rejected": not bind(holder_commitment="0" * 64),
        "duplicate_replay_rejected": first_replay_accepted and not duplicate_replay_accepted,
    }
    source_after = source_snapshot(source)
    zero_upstream_writes = source_before == source_after
    unit_pass = (
        all(controls.values())
        and selected_score["exact_grid"] is True
        and float(selected_score["exact_cell_accuracy"]) > float(baseline_score["exact_cell_accuracy"])
        and replay_prediction == selected_prediction["prediction"]
        and zero_upstream_writes
    )
    result = {
        "schema_version": 1,
        "record_type": "external_arc_three_rule_selector_result",
        "recorded_at": now(),
        "unit_id": UNIT,
        "idempotency_key": IDEMPOTENCY_KEY,
        "source": precommit["source"],
        "selector_sha256": selector_sha256,
        "rule_manifest_sha256": rules_sha256,
        "task_id": selected_id,
        "selected_rule_id": selected["winner"],
        "training_zero_error_winner_count": len(scores_artifact["zero_error_winners"]),
        "holder_commitment_verified": reveal["commitment"] == selected_commitment,
        "pre_reveal_exact_readback": True,
        "baseline_score": baseline_score,
        "selected_rule_score": selected_score,
        "strict_improvement": float(selected_score["exact_cell_accuracy"]) > float(baseline_score["exact_cell_accuracy"]),
        "deterministic_replay_matches": replay_prediction == selected_prediction["prediction"],
        "effect_digest": effect_digest,
        "first_replay_accepted": first_replay_accepted,
        "duplicate_replay_accepted": duplicate_replay_accepted,
        "controls": controls,
        "source_write_audit": {"before": source_before, "after": source_after, "zero_upstream_writes": zero_upstream_writes},
        "artifacts": artifacts,
        "unit_verdict": "PASS" if unit_pass else "FAIL",
        "unit_completion_verdict": "PASS" if unit_pass else "FAIL",
        "task_completion_verdict": "FAIL",
        "user_level_verdict": "FAIL",
        "upper_objective_verdict": "FAIL",
        "candidate_activation": False,
        "production_routing": False,
        "agi_achieved": False,
        "claim_boundary": precommit["claim_boundary"],
        "next_action": "Independently Task Evaluate this exact selector event, then continue post-result Candidate Evaluate, Consolidate Episode, Learn, Root, and materially different safe work while the user objective and AGI remain open.",
    }
    artifacts["result"] = write_artifact(
        out, "unit-generation29-external-arc-three-rule-selector-v1-result.json", result
    )
    return {
        "unit_verdict": result["unit_verdict"],
        "task_id": selected_id,
        "selected_rule_id": selected["winner"],
        "candidate_count": len(candidates),
        "training_zero_error_winner_count": result["training_zero_error_winner_count"],
        "baseline_score": baseline_score,
        "selected_rule_score": selected_score,
        "controls": controls,
        "deterministic_replay_matches": result["deterministic_replay_matches"],
        "zero_upstream_writes": zero_upstream_writes,
        "artifacts": artifacts,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--o-root", type=Path, required=True)
    parser.add_argument("--holder", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(execute(args.source, args.o_root, args.holder, args.out), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
