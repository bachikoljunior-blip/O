#!/usr/bin/env python3
"""Prospectively fixed, read-only audit of exact production Git history."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from continual.work_session import WorkSessionError, verified_work_request


def run(repo: Path, *argv: str) -> dict:
    started = time.time()
    proc = subprocess.run(
        list(argv), cwd=repo, text=False, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=20, check=False,
    )
    return {
        "argv": list(argv),
        "exit_code": proc.returncode,
        "stdout_utf8": proc.stdout.decode("utf-8", "replace"),
        "stderr_utf8": proc.stderr.decode("utf-8", "replace"),
        "duration_seconds": round(time.time() - started, 6),
    }


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    started = time.time()
    records: list[dict] = []

    for case in protocol["historical_cases"]:
        spec = f'{case["commit_sha"]}:{case["path"]}'
        resolve = run(repo, "git", "rev-parse", spec)
        read = run(repo, "git", "cat-file", "blob", spec)
        data = read["stdout_utf8"].encode("utf-8") if read["exit_code"] == 0 else b""
        # Git-tracked JSON here is UTF-8; raw subprocess bytes are recovered exactly.
        if read["exit_code"] == 0:
            exact = subprocess.run(
                ["git", "cat-file", "blob", spec], cwd=repo,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=False,
            ).stdout
        else:
            exact = b""
        current_spec = f'{protocol["source_main_sha"]}:{case["path"]}'
        current = run(repo, "git", "rev-parse", current_spec)
        observed_blob = resolve["stdout_utf8"].strip()
        record = {
            "id": case["id"], "commit_sha": case["commit_sha"],
            "path": case["path"], "resolve": resolve, "read": read,
            "observed_blob_sha": observed_blob,
            "observed_raw_sha256": hashlib.sha256(exact).hexdigest(),
            "current_identity": {
                "commit_sha": protocol["source_main_sha"],
                "blob_sha": current["stdout_utf8"].strip(),
                "resolve": current,
            },
        }
        record["pass"] = bool(
            resolve["exit_code"] == 0 and read["exit_code"] == 0
            and observed_blob == case["expected_blob_sha"]
            and blob_sha(exact) == case["expected_blob_sha"]
            and record["observed_raw_sha256"] == case["expected_raw_sha256"]
            and current["exit_code"] == 0
        )
        records.append(record)

    negative = run(repo, "git", "cat-file", "-e", f'{protocol["nonresolving_commit_sha"]}^{{commit}}')
    negative["pass"] = negative["exit_code"] != 0

    request_id = protocol["current_validation_control"]["work_invocation_id"]
    valid_error = None
    try:
        valid_request = verified_work_request(repo, request_id)
        valid_pass = valid_request["request_digest"] == protocol["current_validation_control"]["request_digest"]
    except WorkSessionError as exc:
        valid_pass = False
        valid_error = str(exc)

    tampered_error = None
    tampered_rejected = False
    with tempfile.TemporaryDirectory(prefix="g34-current-digest-control-", dir=repo) as tmp:
        tmp_root = Path(tmp)
        source = repo / ".continual" / "work-model" / "invocations" / request_id / "request.json"
        target = tmp_root / ".continual" / "work-model" / "invocations" / request_id / "request.json"
        target.parent.mkdir(parents=True)
        shutil.copyfile(source, target)
        body = json.loads(target.read_text(encoding="utf-8"))
        body["request_digest"] = "0" * 64
        target.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            verified_work_request(tmp_root, request_id)
        except WorkSessionError as exc:
            tampered_rejected = True
            tampered_error = str(exc)

    elapsed = time.time() - started
    result = {
        "schema_version": 1,
        "kind": "production_remote_history_resolution_audit_not_prompt_comparison",
        "attempt": 1,
        "protocol_sha256": hashlib.sha256(args.protocol.read_bytes()).hexdigest(),
        "source_main_sha": protocol["source_main_sha"],
        "historical_cases": records,
        "nonresolving_control": negative,
        "current_validation_control": {
            "validator": "continual.work_session.verified_work_request",
            "valid_current_request_accepted": valid_pass,
            "valid_error": valid_error,
            "malformed_request_digest_rejected": tampered_rejected,
            "malformed_error": tampered_error,
        },
        "wall_seconds": round(elapsed, 6),
    }
    result["verdict"] = "PASS" if (
        all(item["pass"] for item in records)
        and negative["pass"] and valid_pass and tampered_rejected
        and elapsed < protocol["limits"]["wall_seconds_max"]
    ) else "FAIL"
    result["claim_boundary"] = protocol["claim_boundary"]
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
