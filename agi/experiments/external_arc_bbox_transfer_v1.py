from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path


COMMIT = "399030444e0ab0cc8b4e199870fb20b863846f34"
UNIT = "unit-generation29-external-arc-nonzero-bounding-box-transfer-v1"
IDEMPOTENCY_KEY = "o-work-gen29:arc-sequential-transfer:39903044:minimal-nonzero-bbox:v1"
EXCLUDED_TASK_IDS = frozenset({"74dd1130", "9dfd6313", "3c9b0459", "6150a2bd"})


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def minimal_nonzero_bbox(grid: list[list[int]]) -> list[list[int]] | None:
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
        sanitized = record.get("sanitized")
        if not isinstance(sanitized, dict) or len(sanitized.get("test", [])) != 1:
            continue
        training = sanitized.get("train", [])
        if not training:
            continue
        if not all(
            minimal_nonzero_bbox(pair["input"]) is not None
            and minimal_nonzero_bbox(pair["input"]) == pair["output"]
            for pair in training
        ):
            continue
        test_input = sanitized["test"][0]["input"]
        prediction = minimal_nonzero_bbox(test_input)
        if prediction is None or prediction == test_input:
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
    selector = {
        "algorithm": "lexicographically first two public training-or-evaluation tasks with exactly one test case, every demonstration output equal to the minimal rectangular crop containing every nonzero input cell, a nonempty test foreground, a test input changed by the crop, no prior exact task-id occurrence in O, and explicit exclusion of the prior transpose and rotate-180 task IDs",
        "family": "minimal_nonzero_bounding_box",
        "candidate_count": len(candidates),
        "candidate_task_ids": [str(record["task_id"]) for record in candidates],
        "selected_task_ids": [str(record["task_id"]) for record in candidates[:2]] if len(candidates) >= 2 else [],
        "excluded_task_ids": sorted(EXCLUDED_TASK_IDS),
        "excluded_outcome_fields": ["test[*].output"],
    }
    precommit = {
        "schema_version": 1,
        "record_type": "external_arc_bbox_transfer_precommit",
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
        "selector_sha256": digest(selector),
        "orchestrator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "holder_sha256": hashlib.sha256(holder_path.read_bytes()).hexdigest(),
        "candidate_provenance": [
            {
                "task_id": record["task_id"],
                "source_split": record["source_split"],
                "source_path": record["source_path"],
                "source_blob_sha": record["source_blob_sha"],
                "sanitized_projection_sha256": digest(record["sanitized"]),
            }
            for record in candidates
        ],
        "answer_access": {
            "holder_commit_called": False,
            "holder_reveal_called": False,
            "test_output_cells_observed": False,
        },
        "failure_condition": "fewer_than_two_eligible_tasks" if len(candidates) < 2 else None,
        "claim_boundary": "One bounded structural ARC selector. Fewer than two eligible tasks is a unit failure without substitution, answer reveal, Candidate activation, production routing, or broader inference.",
    }
    precommit_path, precommit_blob, precommit_digest = write_artifact(
        out, "unit-generation29-external-arc-nonzero-bounding-box-transfer-v1-precommit.json", precommit
    )
    if len(candidates) >= 2:
        raise RuntimeError("positive execution path is intentionally not entered by this frozen corpus result")
    source_after = source_snapshot(source)
    zero_upstream_writes = source_before == source_after
    controls = {
        "insufficient_candidate_count_rejected": len(candidates) < 2,
        "no_task_substitution": selector["selected_task_ids"] == [],
        "no_holder_commit": precommit["answer_access"]["holder_commit_called"] is False,
        "no_answer_reveal": precommit["answer_access"]["holder_reveal_called"] is False,
        "no_answer_cells_observed": precommit["answer_access"]["test_output_cells_observed"] is False,
        "zero_upstream_writes": zero_upstream_writes,
    }
    result = {
        "schema_version": 1,
        "record_type": "external_arc_bbox_transfer_result",
        "recorded_at": now(),
        "unit_id": UNIT,
        "idempotency_key": IDEMPOTENCY_KEY,
        "source": precommit["source"],
        "family": selector["family"],
        "selector_sha256": precommit["selector_sha256"],
        "candidate_count": len(candidates),
        "candidate_task_ids": selector["candidate_task_ids"],
        "selected_task_ids": [],
        "failure_condition": "fewer_than_two_eligible_tasks",
        "failure_observed": True,
        "answer_reveal_attempted": False,
        "baseline_frozen": False,
        "learning_record_created": False,
        "task_b_prediction_frozen": False,
        "scoring_performed": False,
        "controls": controls,
        "source_write_audit": {"before": source_before, "after": source_after, "zero_upstream_writes": zero_upstream_writes},
        "artifacts": {"precommit": {"path": precommit_path, "git_blob_sha": precommit_blob, "sha256": precommit_digest}},
        "unit_verdict": "FAIL",
        "task_completion_verdict": "FAIL",
        "upper_objective_verdict": "FAIL",
        "user_level_verdict": "FAIL",
        "candidate_activation": False,
        "production_routing": False,
        "agi_achieved": False,
        "claim_boundary": precommit["claim_boundary"],
        "next_action": "Independently Task Evaluate this exact fail-closed bounded result, preserve the answer-withholding evidence, and continue the unchanged lifecycle without substituting another task into this frozen unit.",
    }
    result_path, result_blob, result_digest = write_artifact(
        out, "unit-generation29-external-arc-nonzero-bounding-box-transfer-v1-result.json", result
    )
    return {
        "unit_verdict": "FAIL",
        "failure_condition": result["failure_condition"],
        "candidate_count": len(candidates),
        "candidate_task_ids": result["candidate_task_ids"],
        "answer_reveal_attempted": False,
        "controls": controls,
        "zero_upstream_writes": zero_upstream_writes,
        "precommit_path": precommit_path,
        "precommit_git_blob_sha": precommit_blob,
        "precommit_sha256": precommit_digest,
        "result_path": result_path,
        "result_git_blob_sha": result_blob,
        "result_sha256": result_digest,
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
