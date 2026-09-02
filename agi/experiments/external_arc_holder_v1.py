from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path


SOURCE = Path("/workspace/scratch/3e753b952683/ARC-AGI-upstream")
COMMIT = "399030444e0ab0cc8b4e199870fb20b863846f34"


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def show(path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{COMMIT}:{path}"], cwd=SOURCE)


def task(task_id: str) -> dict:
    matches = []
    for split in ("training", "evaluation"):
        path = f"data/{split}/{task_id}.json"
        probe = subprocess.run(
            ["git", "cat-file", "-e", f"{COMMIT}:{path}"],
            cwd=SOURCE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if probe.returncode == 0:
            matches.append(path)
    if len(matches) != 1:
        raise SystemExit(f"task identity must resolve exactly once: {task_id}")
    return json.loads(show(matches[0]))


parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=("catalog", "commit", "reveal", "license"))
parser.add_argument("--task-id", action="append", default=[])
parser.add_argument("--expected-commitment")
args = parser.parse_args()

if args.mode == "catalog":
    archive = subprocess.check_output(
        ["git", "archive", "--format=tar", COMMIT, "data/training", "data/evaluation"], cwd=SOURCE
    )
    records = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        members = sorted(
            (member for member in bundle.getmembers() if member.isfile() and member.name.endswith(".json")),
            key=lambda member: member.name,
        )
        for member in members:
            path = member.name
            extracted = bundle.extractfile(member)
            if extracted is None:
                raise SystemExit(f"could not read archived task {path}")
            raw = extracted.read()
            value = json.loads(raw)
            records.append({
                "task_id": Path(path).stem,
                "source_split": Path(path).parts[1],
                "source_path": path,
                "source_blob_sha": hashlib.sha1(
                    f"blob {len(raw)}\0".encode() + raw
                ).hexdigest(),
                "sanitized": {
                    "train": value["train"],
                    "test": [{"input": item["input"]} for item in value["test"]],
                },
            })
    print(json.dumps(records, sort_keys=True))
elif args.mode == "commit":
    if not args.task_id:
        raise SystemExit("commit requires --task-id")
    commitments = {}
    for task_id in args.task_id:
        value = task(task_id)
        outputs = [item["output"] for item in value["test"]]
        commitments[task_id] = {
            "test_count": len(outputs),
            "output_commitment_sha256": hashlib.sha256(canonical(outputs)).hexdigest(),
        }
    print(json.dumps(commitments, sort_keys=True))
elif args.mode == "reveal":
    if len(args.task_id) != 1 or not args.expected_commitment:
        raise SystemExit("reveal requires one --task-id and --expected-commitment")
    value = task(args.task_id[0])
    outputs = [item["output"] for item in value["test"]]
    actual = hashlib.sha256(canonical(outputs)).hexdigest()
    if actual != args.expected_commitment:
        raise SystemExit("holder commitment mismatch")
    print(json.dumps({"task_id": args.task_id[0], "outputs": outputs, "commitment": actual}, sort_keys=True))
else:
    license_bytes = show("LICENSE")
    print(json.dumps({
        "path": "LICENSE",
        "sha256": hashlib.sha256(license_bytes).hexdigest(),
        "apache_2_0_marker": b"Apache License" in license_bytes and b"Version 2.0" in license_bytes,
    }, sort_keys=True))
