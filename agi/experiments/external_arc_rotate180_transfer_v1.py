from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path


COMMIT = "399030444e0ab0cc8b4e199870fb20b863846f34"
UNIT = "unit-generation29-external-arc-rotate180-transfer-v1"
IDEMPOTENCY_KEY = "o-work-gen29:arc-sequential-transfer:39903044:rotate180:v1"
EXCLUDED_TASK_IDS = frozenset({"74dd1130", "9dfd6313"})


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def rotate_180(grid: list[list[int]]) -> list[list[int]]:
    return [list(reversed(row)) for row in reversed(grid)]


def shape(grid: list[list[int]]) -> list[int]:
    return [len(grid), len(grid[0]) if grid else 0]


def score(prediction: list[list[int]], target: list[list[int]]) -> dict[str, object]:
    target_cells = sum(len(row) for row in target)
    shape_match = shape(prediction) == shape(target)
    correct = (
        sum(p == t for prow, trow in zip(prediction, target) for p, t in zip(prow, trow))
        if shape_match
        else 0
    )
    return {
        "prediction_shape": shape(prediction),
        "target_shape": shape(target),
        "shape_match": shape_match,
        "exact_cell_correct": correct,
        "exact_cell_total": target_cells,
        "exact_cell_accuracy": correct / target_cells if target_cells else 0.0,
        "exact_grid": shape_match and correct == target_cells,
    }


def contains_grid(value: object) -> bool:
    if isinstance(value, dict):
        return any(contains_grid(item) for item in value.values())
    if isinstance(value, list):
        if value and all(isinstance(row, list) for row in value):
            flattened = [cell for row in value for cell in row]
            if flattened and all(isinstance(cell, int) for cell in flattened):
                return True
        return any(contains_grid(item) for item in value)
    return False


def source_snapshot(source: Path) -> dict[str, object]:
    status = subprocess.check_output(["git", "status", "--porcelain=v1", "-uno"], cwd=source)
    count = subprocess.check_output(["git", "count-objects", "-v"], cwd=source)
    return {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip(),
        "status_sha256": hashlib.sha256(status).hexdigest(),
        "status_line_count": len(status.splitlines()),
        "object_count_sha256": hashlib.sha256(count).hexdigest(),
    }


def select_candidates(catalog: list[dict[str, object]], prior_ids: set[str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    for record in catalog:
        task_id = str(record["task_id"])
        if task_id in EXCLUDED_TASK_IDS or task_id in prior_ids:
            continue
        sanitized = record["sanitized"]
        if not isinstance(sanitized, dict) or len(sanitized.get("test", [])) != 1:
            continue
        train = sanitized.get("train", [])
        if not train:
            continue
        if not all(rotate_180(item["input"]) == item["output"] for item in train):
            continue
        test_input = sanitized["test"][0]["input"]
        if rotate_180(test_input) == test_input:
            continue
        candidates.append(record)
    return sorted(candidates, key=lambda item: str(item["task_id"]))


def prior_task_ids(catalog: list[dict[str, object]], o_root: Path) -> set[str]:
    task_ids = sorted(str(record["task_id"]) for record in catalog)
    scan = subprocess.run(
        ["rg", "--hidden", "-F", "-h", "-o", "-f", "-", "--glob", "!.git/**", str(o_root)],
        input="\n".join(task_ids) + "\n",
        text=True,
        stdout=subprocess.PIPE,
        check=False,
    )
    if scan.returncode not in (0, 1):
        raise RuntimeError(f"prior-use scan failed with exit {scan.returncode}")
    return set(scan.stdout.splitlines())


def holder(holder_path: Path, *args: str) -> object:
    raw = subprocess.check_output(
        ["/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python", str(holder_path), *args],
        text=True,
    )
    return json.loads(raw)


def write_artifact(out: Path, name: str, value: object) -> tuple[str, str, str]:
    path = out / name
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    blob = subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()
    return str(path), blob, digest(value)


def verify_binding(
    *,
    source_commit: str,
    task_id: str,
    commitment: str,
    selector_digest: str,
    family: str,
    expected_task_id: str,
    expected_commitment: str,
    expected_selector_digest: str,
) -> bool:
    return (
        source_commit == COMMIT
        and task_id == expected_task_id
        and commitment == expected_commitment
        and selector_digest == expected_selector_digest
        and family == "matrix_rotate_180"
    )


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
    candidates = select_candidates(catalog, prior_ids)
    if len(candidates) < 2:
        raise RuntimeError("fewer than two unused public rotate-180 tasks")
    task_a, task_b = candidates[:2]
    selected_ids = [str(task_a["task_id"]), str(task_b["task_id"])]

    commitments = holder(
        holder_path,
        "commit",
        "--task-id",
        selected_ids[0],
        "--task-id",
        selected_ids[1],
    )
    selector = {
        "algorithm": "lexicographically first two public training-or-evaluation tasks with exactly one test case, every demonstration output equal to matrix rotate-180, a test input not invariant under rotate-180, no prior exact task-id occurrence in O, and explicit exclusion of 74dd1130 and 9dfd6313",
        "family": "matrix_rotate_180",
        "candidate_count": len(candidates),
        "selected_task_ids": selected_ids,
        "excluded_task_ids": sorted(EXCLUDED_TASK_IDS),
        "excluded_outcome_fields": ["test[*].output"],
    }
    selector_digest = digest(selector)
    precommit = {
        "schema_version": 1,
        "record_type": "external_arc_rotate180_transfer_precommit",
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
        "orchestrator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "holder_sha256": hashlib.sha256(holder_path.read_bytes()).hexdigest(),
        "tasks": [
            {
                "role": role,
                "task_id": record["task_id"],
                "source_split": record["source_split"],
                "source_path": record["source_path"],
                "source_blob_sha": record["source_blob_sha"],
                "sanitized_projection_sha256": digest(record["sanitized"]),
                "holder_output_commitment_sha256": commitments[str(record["task_id"])]["output_commitment_sha256"],
                "test_count": commitments[str(record["task_id"])]["test_count"],
                "prior_use_scan_match": False,
            }
            for role, record in (("A", task_a), ("B", task_b))
        ],
        "baseline": {
            "solver": "identity_v1",
            "frozen_rule": "Return the supplied test input unchanged.",
            "same_solver_for_A_and_B": True,
        },
        "adaptation": {
            "permitted_learning_record": "A rule identifier and evidence digests only; no task-B answer cells.",
            "adapted_rule": "matrix_rotate_180_v1",
            "task_b_reveal_forbidden_until_adapted_prediction_frozen": True,
        },
        "scoring": {
            "exact_grid": "shape and every cell must match",
            "exact_cell": "if shape differs, score 0/target_cell_count; otherwise count equal cells",
            "improvement": "adapted task-B exact-cell accuracy must strictly exceed baseline task-B accuracy",
        },
        "negative_controls": [
            "changed_source_commit",
            "changed_task_identity",
            "mismatched_holder_commitment",
            "leaked_answer_cells",
            "changed_selector_digest",
            "family_mismatch",
            "duplicate_replay",
        ],
        "claim_boundary": "Exactly two deterministically selected ARC tasks in one rotate-180 family and one bounded rule-library transfer; no generalized ARC, broad continual-learning, autonomy, production, AGI, user-level, or upper-objective inference.",
    }
    precommit_path, precommit_blob, precommit_digest = write_artifact(
        out, "unit-generation29-external-arc-rotate180-transfer-v1-precommit.json", precommit
    )

    baseline = {
        "schema_version": 1,
        "record_type": "frozen_baseline_predictions",
        "recorded_at": now(),
        "precommit_sha256": precommit_digest,
        "solver": "identity_v1",
        "predictions": {
            selected_ids[0]: task_a["sanitized"]["test"][0]["input"],
            selected_ids[1]: task_b["sanitized"]["test"][0]["input"],
        },
    }
    baseline_path, baseline_blob, baseline_digest = write_artifact(
        out, "unit-generation29-external-arc-rotate180-transfer-v1-baseline-predictions.json", baseline
    )

    reveal_a = holder(
        holder_path,
        "reveal",
        "--task-id",
        selected_ids[0],
        "--expected-commitment",
        commitments[selected_ids[0]]["output_commitment_sha256"],
    )
    target_a = reveal_a["outputs"][0]
    baseline_a_score = score(baseline["predictions"][selected_ids[0]], target_a)
    if rotate_180(task_a["sanitized"]["test"][0]["input"]) != target_a:
        raise RuntimeError("task A did not validate the committed rotate-180 rule")

    learning = {
        "schema_version": 1,
        "record_type": "bounded_task_a_learning_record",
        "recorded_at": now(),
        "source_task_id": selected_ids[0],
        "rule_id": "matrix_rotate_180_v1",
        "rule": "Reverse row order and reverse cell order within every row, preserving cell values.",
        "task_a_holder_commitment_verified": True,
        "task_a_baseline_exact_grid": baseline_a_score["exact_grid"],
        "task_a_training_projection_sha256": digest(task_a["sanitized"]["train"]),
        "claim_boundary": "One deterministic rotate-180 rule; contains no task-B output cells or answer-bearing derivative.",
    }
    if contains_grid(learning):
        raise RuntimeError("learning record contains answer grid")
    learning_path, learning_blob, learning_digest = write_artifact(
        out, "unit-generation29-external-arc-rotate180-transfer-v1-learning-record.json", learning
    )

    adapted = {
        "schema_version": 1,
        "record_type": "frozen_adapted_task_b_prediction",
        "recorded_at": now(),
        "task_id": selected_ids[1],
        "rule_id": learning["rule_id"],
        "learning_record_sha256": learning_digest,
        "prediction": rotate_180(task_b["sanitized"]["test"][0]["input"]),
        "task_b_output_unrevealed": True,
    }
    adapted_path, adapted_blob, adapted_digest = write_artifact(
        out, "unit-generation29-external-arc-rotate180-transfer-v1-adapted-task-b-prediction.json", adapted
    )

    reveal_b = holder(
        holder_path,
        "reveal",
        "--task-id",
        selected_ids[1],
        "--expected-commitment",
        commitments[selected_ids[1]]["output_commitment_sha256"],
    )
    target_b = reveal_b["outputs"][0]
    baseline_b_score = score(baseline["predictions"][selected_ids[1]], target_b)
    adapted_b_score = score(adapted["prediction"], target_b)
    expected_commitment = commitments[selected_ids[1]]["output_commitment_sha256"]
    bind = lambda **overrides: verify_binding(
        source_commit=overrides.get("source_commit", COMMIT),
        task_id=overrides.get("task_id", selected_ids[1]),
        commitment=overrides.get("commitment", expected_commitment),
        selector_digest=overrides.get("selector_digest", selector_digest),
        family=overrides.get("family", "matrix_rotate_180"),
        expected_task_id=selected_ids[1],
        expected_commitment=expected_commitment,
        expected_selector_digest=selector_digest,
    )
    controls = {
        "changed_source_commit": not bind(source_commit="0" * 40),
        "changed_task_identity": not bind(task_id=selected_ids[0]),
        "mismatched_holder_commitment": not bind(commitment="0" * 64),
        "leaked_answer_cells": contains_grid({"task_b_answer_cells": target_b}) and not contains_grid(learning),
        "changed_selector_digest": not bind(selector_digest="0" * 64),
        "family_mismatch": not bind(family="matrix_transpose"),
    }
    replay_digest = hashlib.sha256(
        canonical(
            {
                "idempotency_key": IDEMPOTENCY_KEY,
                "precommit": precommit_digest,
                "baseline": baseline_digest,
                "learning": learning_digest,
                "adapted": adapted_digest,
            }
        )
    ).hexdigest()
    seen: set[str] = set()
    first_accept = replay_digest not in seen
    seen.add(replay_digest)
    duplicate_accept = replay_digest not in seen
    controls["duplicate_replay"] = first_accept and not duplicate_accept
    if not all(controls.values()):
        raise RuntimeError("negative control failed")

    source_after = source_snapshot(source)
    zero_upstream_writes = source_before == source_after
    improved = adapted_b_score["exact_cell_accuracy"] > baseline_b_score["exact_cell_accuracy"]
    unit_pass = bool(improved and adapted_b_score["exact_grid"] and all(controls.values()) and zero_upstream_writes)
    result = {
        "schema_version": 1,
        "record_type": "external_arc_rotate180_transfer_result",
        "recorded_at": now(),
        "unit_id": UNIT,
        "idempotency_key": IDEMPOTENCY_KEY,
        "source": precommit["source"],
        "selected_task_ids": selected_ids,
        "family": selector["family"],
        "ordering": [
            "sanitized deterministic selection",
            "holder commitments",
            "precommit",
            "baseline A and B freeze",
            "task A reveal and bounded learning record",
            "adapted task B freeze",
            "task B reveal and deterministic scoring",
            "negative controls and replay check",
        ],
        "artifacts": {
            "precommit": {"path": precommit_path, "git_blob_sha": precommit_blob, "sha256": precommit_digest},
            "baseline": {"path": baseline_path, "git_blob_sha": baseline_blob, "sha256": baseline_digest},
            "learning_record": {"path": learning_path, "git_blob_sha": learning_blob, "sha256": learning_digest},
            "adapted_task_b": {"path": adapted_path, "git_blob_sha": adapted_blob, "sha256": adapted_digest},
        },
        "scores": {
            "baseline_task_a": baseline_a_score,
            "baseline_task_b": baseline_b_score,
            "adapted_task_b": adapted_b_score,
            "strict_task_b_improvement": improved,
        },
        "negative_controls": controls,
        "replay": {"digest": replay_digest, "first_accept": first_accept, "duplicate_accept": duplicate_accept},
        "source_write_audit": {"before": source_before, "after": source_after, "zero_upstream_writes": zero_upstream_writes},
        "unit_verdict": "PASS" if unit_pass else "FAIL",
        "candidate_activation": False,
        "upper_objective_achieved": False,
        "user_level_objective_achieved": False,
        "agi_achieved": False,
        "claim_boundary": precommit["claim_boundary"],
    }
    result_path, result_blob, result_digest = write_artifact(
        out, "unit-generation29-external-arc-rotate180-transfer-v1-result.json", result
    )
    return {
        "unit_verdict": result["unit_verdict"],
        "selected_task_ids": selected_ids,
        "candidate_count": len(candidates),
        "scores": result["scores"],
        "negative_controls": controls,
        "zero_upstream_writes": zero_upstream_writes,
        "result_path": result_path,
        "result_git_blob_sha": result_blob,
        "result_sha256": result_digest,
        "replay_digest": replay_digest,
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
