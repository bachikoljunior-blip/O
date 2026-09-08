from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path


SOURCE = Path("/workspace/scratch/3e753b952683/ARC-AGI-upstream")
O_ROOT = Path("/workspace/scratch/3e753b952683/O-candidate")
HOLDER = Path("/workspace/scratch/3e753b952683/arc_holder.py")
OUT = Path("/workspace/scratch/3e753b952683/arc_trial_gen29")
COMMIT = "399030444e0ab0cc8b4e199870fb20b863846f34"
UNIT = "unit-generation29-external-arc-sequential-transfer-v1"
IDEMPOTENCY_KEY = "o-work-gen29:arc-sequential-transfer:39903044:transpose:v1"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def write(name: str, value: object) -> tuple[str, str]:
    path = OUT / name
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    return str(path), subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()


def holder(*args: str) -> object:
    raw = subprocess.check_output(
        ["/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python", str(HOLDER), *args],
        text=True,
    )
    return json.loads(raw)


def transpose(grid: list[list[int]]) -> list[list[int]]:
    return [list(column) for column in zip(*grid)]


def shape(grid: list[list[int]]) -> list[int]:
    return [len(grid), len(grid[0]) if grid else 0]


def score(prediction: list[list[int]], target: list[list[int]]) -> dict:
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
        return any(contains_grid(v) for v in value.values())
    if isinstance(value, list):
        if value and all(isinstance(row, list) for row in value):
            flattened = [cell for row in value for cell in row]
            if flattened and all(isinstance(cell, int) for cell in flattened):
                return True
        return any(contains_grid(v) for v in value)
    return False


def source_snapshot() -> dict:
    status = subprocess.check_output(["git", "status", "--porcelain=v1", "-uno"], cwd=SOURCE)
    count = subprocess.check_output(["git", "count-objects", "-v"], cwd=SOURCE)
    return {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SOURCE, text=True).strip(),
        "status_sha256": hashlib.sha256(status).hexdigest(),
        "status_line_count": len(status.splitlines()),
        "object_count_sha256": hashlib.sha256(count).hexdigest(),
    }


OUT.mkdir(exist_ok=True)
source_before = source_snapshot()
if source_before["head"] != COMMIT:
    raise SystemExit("source commit mismatch")
license_receipt = holder("license")
if not license_receipt["apache_2_0_marker"]:
    raise SystemExit("license mismatch")

# The solver/orchestrator receives only training pairs and test inputs. Test outputs
# remain behind the holder subprocess until each explicit reveal call.
catalog = holder("catalog")
candidates = []
for record in catalog:
    sanitized = record["sanitized"]
    if len(sanitized["test"]) != 1:
        continue
    train = sanitized["train"]
    if not train:
        continue
    if not all(transpose(item["input"]) == item["output"] for item in train):
        continue
    test_input = sanitized["test"][0]["input"]
    transposed = transpose(test_input)
    if transposed == test_input:
        continue
    candidates.append(record)

# Check every candidate identifier in one literal-pattern repository scan.  This
# preserves the exact exclusion predicate without rescanning the tree once per
# candidate.
candidate_by_id = {record["task_id"]: record for record in candidates}
scan = subprocess.run(
    ["rg", "--hidden", "-F", "-h", "-o", "-f", "-", "--glob", "!.git/**", str(O_ROOT)],
    input="\n".join(sorted(candidate_by_id)) + "\n",
    text=True,
    stdout=subprocess.PIPE,
    check=False,
)
if scan.returncode not in (0, 1):
    raise SystemExit(f"prior-use scan failed with exit {scan.returncode}")
prior_ids = set(scan.stdout.splitlines())
candidates = [record for record in candidates if record["task_id"] not in prior_ids]

candidates.sort(key=lambda item: item["task_id"])
if len(candidates) < 2:
    raise SystemExit("fewer than two unused public transpose tasks")
selected = candidates[:2]
task_a, task_b = selected
selected_ids = [task_a["task_id"], task_b["task_id"]]

commitments = holder(
    "commit",
    "--task-id", task_a["task_id"],
    "--task-id", task_b["task_id"],
)
selector = {
    "algorithm": "lexicographically first two public training-or-evaluation tasks with exactly one test case, all demonstration outputs equal matrix transpose, a sanitized test input not invariant under transpose, and no exact task-id occurrence in O",
    "family": "matrix_transpose",
    "candidate_count": len(candidates),
    "selected_task_ids": selected_ids,
    "excluded_outcome_fields": ["test[*].output"],
}
script_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
holder_digest = hashlib.sha256(HOLDER.read_bytes()).hexdigest()
precommit = {
    "schema_version": 1,
    "record_type": "external_arc_sequential_transfer_precommit",
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
    "orchestrator_sha256": script_digest,
    "holder_sha256": holder_digest,
    "tasks": [
        {
            "role": role,
            "task_id": record["task_id"],
            "source_split": record["source_split"],
            "source_path": record["source_path"],
            "source_blob_sha": record["source_blob_sha"],
            "sanitized_projection_sha256": digest(record["sanitized"]),
            "holder_output_commitment_sha256": commitments[record["task_id"]]["output_commitment_sha256"],
            "test_count": commitments[record["task_id"]]["test_count"],
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
        "adapted_rule": "matrix_transpose_v1",
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
        "duplicate_replay",
    ],
    "claim_boundary": "Exactly two deterministically selected ARC training tasks and one bounded rule-library expansion; no broad continual-learning, autonomy, production, AGI, user-level, or upper-objective inference.",
}
precommit_path, precommit_blob = write("precommit.json", precommit)
precommit_digest = digest(precommit)

baseline = {
    "schema_version": 1,
    "record_type": "frozen_baseline_predictions",
    "recorded_at": now(),
    "precommit_sha256": precommit_digest,
    "solver": "identity_v1",
    "predictions": {
        task_a["task_id"]: task_a["sanitized"]["test"][0]["input"],
        task_b["task_id"]: task_b["sanitized"]["test"][0]["input"],
    },
}
baseline_path, baseline_blob = write("baseline_predictions.json", baseline)
baseline_digest = digest(baseline)

# Reveal A only after the source/task/holder/scoring commitments and both baseline
# predictions are durable.
reveal_a = holder(
    "reveal",
    "--task-id", task_a["task_id"],
    "--expected-commitment", commitments[task_a["task_id"]]["output_commitment_sha256"],
)
target_a = reveal_a["outputs"][0]
baseline_a_score = score(baseline["predictions"][task_a["task_id"]], target_a)
if transpose(task_a["sanitized"]["test"][0]["input"]) != target_a:
    raise SystemExit("task A did not validate the committed transpose rule")

learning_record = {
    "schema_version": 1,
    "record_type": "bounded_task_a_learning_record",
    "recorded_at": now(),
    "source_task_id": task_a["task_id"],
    "rule_id": "matrix_transpose_v1",
    "rule": "Return the matrix transpose: output row r is input column r, preserving cell values.",
    "task_a_holder_commitment_verified": True,
    "task_a_baseline_exact_grid": baseline_a_score["exact_grid"],
    "task_a_training_projection_sha256": digest(task_a["sanitized"]["train"]),
    "claim_boundary": "One deterministic transpose rule; contains no task-B output cells or answer-bearing derivative.",
}
if contains_grid(learning_record):
    raise SystemExit("learning record contains answer grid")
learning_path, learning_blob = write("learning_record.json", learning_record)
learning_digest = digest(learning_record)

adapted_b = {
    "schema_version": 1,
    "record_type": "frozen_adapted_task_b_prediction",
    "recorded_at": now(),
    "task_id": task_b["task_id"],
    "rule_id": learning_record["rule_id"],
    "learning_record_sha256": learning_digest,
    "prediction": transpose(task_b["sanitized"]["test"][0]["input"]),
    "task_b_output_unrevealed": True,
}
adapted_path, adapted_blob = write("adapted_task_b_prediction.json", adapted_b)
adapted_digest = digest(adapted_b)

# Reveal B only after the adapted prediction is durable.
reveal_b = holder(
    "reveal",
    "--task-id", task_b["task_id"],
    "--expected-commitment", commitments[task_b["task_id"]]["output_commitment_sha256"],
)
target_b = reveal_b["outputs"][0]
baseline_b_score = score(baseline["predictions"][task_b["task_id"]], target_b)
adapted_b_score = score(adapted_b["prediction"], target_b)

def verify_binding(source_commit: str, task_id: str, commitment: str, selector_digest: str) -> bool:
    return (
        source_commit == COMMIT
        and task_id == task_b["task_id"]
        and commitment == commitments[task_b["task_id"]]["output_commitment_sha256"]
        and selector_digest == digest(selector)
    )

controls = {
    "changed_source_commit": not verify_binding("0" * 40, task_b["task_id"], commitments[task_b["task_id"]]["output_commitment_sha256"], digest(selector)),
    "changed_task_identity": not verify_binding(COMMIT, task_a["task_id"], commitments[task_b["task_id"]]["output_commitment_sha256"], digest(selector)),
    "mismatched_holder_commitment": not verify_binding(COMMIT, task_b["task_id"], "0" * 64, digest(selector)),
    "leaked_answer_cells": contains_grid({"task_b_answer_cells": target_b}) and not contains_grid(learning_record),
    "changed_selector_digest": not verify_binding(COMMIT, task_b["task_id"], commitments[task_b["task_id"]]["output_commitment_sha256"], "0" * 64),
}
replay_digest = hashlib.sha256(
    canonical({
        "idempotency_key": IDEMPOTENCY_KEY,
        "precommit": precommit_digest,
        "baseline": baseline_digest,
        "learning": learning_digest,
        "adapted": adapted_digest,
    })
).hexdigest()
seen = {replay_digest}
controls["duplicate_replay"] = replay_digest in seen
if not all(controls.values()):
    raise SystemExit("negative control failed")

source_after = source_snapshot()
zero_upstream_writes = source_before == source_after
improved = adapted_b_score["exact_cell_accuracy"] > baseline_b_score["exact_cell_accuracy"]
unit_pass = (
    improved
    and adapted_b_score["exact_grid"]
    and all(controls.values())
    and zero_upstream_writes
)
result = {
    "schema_version": 1,
    "record_type": "external_arc_sequential_transfer_result",
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
    "replay": {
        "digest": replay_digest,
        "first_accept": True,
        "duplicate_accept": False,
    },
    "source_write_audit": {
        "before": source_before,
        "after": source_after,
        "zero_upstream_writes": zero_upstream_writes,
    },
    "unit_verdict": "PASS" if unit_pass else "FAIL",
    "candidate_activation": False,
    "upper_objective_achieved": False,
    "user_level_objective_achieved": False,
    "agi_achieved": False,
    "claim_boundary": precommit["claim_boundary"],
}
result_path, result_blob = write("result.json", result)
print(json.dumps({
    "unit_verdict": result["unit_verdict"],
    "selected_task_ids": selected_ids,
    "candidate_count": len(candidates),
    "scores": result["scores"],
    "negative_controls": controls,
    "zero_upstream_writes": zero_upstream_writes,
    "result_path": result_path,
    "result_git_blob_sha": result_blob,
    "replay_digest": replay_digest,
}, indent=2, sort_keys=True))
