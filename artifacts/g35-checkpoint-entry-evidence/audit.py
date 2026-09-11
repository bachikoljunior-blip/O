"""Read-only checkpoint evidence; fixture mutations never touch the real Run.

Run from repository root with PYTHONPATH=src. Output must be a new scratch path.
The exact commit arguments make historical/current record sets reproducible.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import runpy
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from continual.work_checkpoint_integrity import verify_work_checkpoint_integrity

ROOT = Path.cwd()
STATE = "agi/WORK_EXECUTION_STATE.json"


def stamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def hashes(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((root / ".continual").rglob("*")) if p.is_file()
    }


def summary(manifest):
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return {"file_count": len(manifest), "path_sha256_manifest_digest": hashlib.sha256(raw).hexdigest()}


def probe(root, *, state=None, state_path=Path(STATE)):
    before = hashes(root)
    report = verify_work_checkpoint_integrity(root, state=state, state_path=state_path)
    after = hashes(root)
    assert before == after, "validator mutated native records"
    assert all(isinstance(i.get(k), str) and i[k] for i in report["issues"] for k in ("code", "path", "message"))
    return {"report": report, "native_before": summary(before), "native_after": summary(after), "native_unchanged": True}


def materialize_references(commit, destination):
    """Copy only validator inputs from exact Git objects; never fill missing refs."""
    state_raw = git("show", commit + ":" + STATE)
    state = json.loads(state_raw)
    tree = {}
    for row in git("ls-tree", "-r", commit, "--", ".continual", STATE).decode().splitlines():
        meta, path = row.split("\t", 1)
        tree[path] = meta.split()[2]
    exact, primary = state["exact_continuation"], state["primary_native_run"]
    refs = {STATE, exact["run_snapshot_ref"]}
    if exact.get("pending_request_ref"):
        refs.add(exact["pending_request_ref"])
    if exact.get("pending_native_invocation_id"):
        refs.add(f'.continual/runs/{primary["run_id"]}/invocations/{exact["pending_native_invocation_id"]}.json')
    ids = {re.match(r"invoke-[0-9a-f]{24}", x).group() for x in primary["answered_invocations"]}
    if exact.get("completed_work_invocation_id"):
        ids.add(exact["completed_work_invocation_id"])
    for invocation in ids:
        refs.update(f".continual/work-model/invocations/{invocation}/{name}.json" for name in ("request", "response"))
    snapshot = json.loads(git("show", commit + ":" + exact["run_snapshot_ref"]))
    if snapshot.get("phase") == "unit_pending":
        refs.add(f'.continual/runs/{primary["run_id"]}/execution-units/{snapshot["current_unit"]}.json')
    bindings = []
    for ref in sorted(refs):
        blob = tree.get(ref)
        bindings.append({"path": ref, "git_blob_sha": blob, "present": blob is not None})
        if blob is not None:
            target = destination / ref
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(git("cat-file", "blob", blob))
    result = probe(destination, state=state)
    return {"commit_sha": commit, "state_git_blob_sha": tree[STATE], "lease_generation": state["lease_generation"], "exact_reference_materialization": bindings, **result}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--published-commit", required=True)
    ap.add_argument("--historical-commit", required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    assert not args.output.exists(), "refuse to overwrite retained evidence"
    started = stamp()
    primary_before = hashes(ROOT)
    helpers = runpy.run_path(str(ROOT / "tests/test_work_checkpoint_integrity.py"))
    controls = []
    cases = {
        "valid": set(),
        "missing_pending_request": {"PENDING_WORK_REQUEST_INVALID"},
        "missing_claimed_response": {"COMPLETED_WORK_INVOCATION_INVALID"},
        "invalid_request_digest": {"COMPLETED_WORK_INVOCATION_INVALID"},
        "invalid_response_digest": {"COMPLETED_WORK_INVOCATION_INVALID"},
        "malformed_request": {"COMPLETED_WORK_INVOCATION_INVALID"},
        "malformed_response": {"COMPLETED_WORK_INVOCATION_INVALID"},
        "malformed_state": {"STATE_MALFORMED"},
        "inconsistent_run_binding": {"WORK_RUN_BINDING_MISMATCH"},
    }
    with tempfile.TemporaryDirectory(prefix="g35-checkpoint-probe-", dir=ROOT.parent) as temp:
        for case, expected in cases.items():
            root, state, invocation = helpers["_completed_checkpoint"](Path(temp) / case)
            directory = root / ".continual/work-model/invocations" / invocation
            if case == "missing_pending_request":
                missing = "invoke-111111111111111111111111"
                state["exact_continuation"].update(pending_work_invocation_id=missing, pending_request_ref=f".continual/work-model/invocations/{missing}/request.json")
            elif case == "missing_claimed_response":
                (directory / "response.json").unlink()
            elif case in {"invalid_request_digest", "invalid_response_digest"}:
                name = "request" if case == "invalid_request_digest" else "response"
                path = directory / (name + ".json")
                value = json.loads(path.read_text())
                value[name + "_digest"] = "0" * 64
                path.write_text(json.dumps(value))
            elif case in {"malformed_request", "malformed_response"}:
                name = case.removeprefix("malformed_")
                (directory / (name + ".json")).write_text("{")
            elif case == "malformed_state":
                path = root / STATE
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{")
            elif case == "inconsistent_run_binding":
                other = "run-different"
                state["active_run_id"] = state["primary_native_run"]["run_id"] = other
                path = root / state["exact_continuation"]["run_snapshot_ref"]
                snapshot = json.loads(path.read_text())
                snapshot["run_id"] = other
                path.write_text(json.dumps(snapshot))
            result = probe(root, state=None if case == "malformed_state" else state)
            actual = {i["code"] for i in result["report"]["issues"]}
            assert actual == expected, (case, actual, expected)
            assert result["report"]["valid"] == (case == "valid")
            controls.append({"case": case, "expected_codes": sorted(expected), "passed": True, **result})
        published = materialize_references(args.published_commit, Path(temp) / "published")
        assert published["report"]["valid"]
        historical = materialize_references(args.historical_commit, Path(temp) / "historical")
        historic_pairs = {(i["code"], i.get("invocation_id")) for i in historical["report"]["issues"]}
        assert ("PENDING_WORK_REQUEST_INVALID", "invoke-37609d6e1890c250c8ada58b") in historic_pairs
        assert ("COMPLETED_WORK_INVOCATION_INVALID", "invoke-3e7b66bfc42ae092653c4668") in historic_pairs
    primary_after = hashes(ROOT)
    assert primary_after == primary_before, "audit changed real primary records"
    refs = ["src/continual/work_checkpoint_integrity.py", "src/continual/work_session.py", "tests/test_work_checkpoint_integrity.py", "tests/test_primary_work_publication.py", ".continual/runs/run-work-recovery-gen9-durability-repair/artifacts/entry.json", "artifacts/g35-checkpoint-entry-evidence/audit.py"]
    result = {"schema_version": 1, "started_at": started, "finished_at": stamp(), "command": ["python", "artifacts/g35-checkpoint-entry-evidence/audit.py", "--published-commit", args.published_commit, "--historical-commit", args.historical_commit, "--output", str(args.output)], "pythonpath": "src", "source_bindings": [{"path": p, "git_blob_sha": git("hash-object", p).decode().strip(), "sha256_raw": hashlib.sha256((ROOT / p).read_bytes()).hexdigest()} for p in refs], "controls": controls, "published_checkpoint": published, "historical_checkpoint": historical, "real_native_before": summary(primary_before), "real_native_after": summary(primary_after), "real_native_unchanged": True, "implementation_decision": "NO_CHANGE", "claim_boundary": "Same-operator internal checkpoint engineering. Fixture controls are synthetic and isolated. Historical findings apply to exact retained commit only; no orphan response was created or consumed, no physical process termination cause or AGI is established."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "controls_passed": len(controls), "published_valid": published["report"]["valid"], "historical_codes": sorted({i["code"] for i in historical["report"]["issues"]}), "real_native_unchanged": True, "implementation_decision": "NO_CHANGE"}))


if __name__ == "__main__":
    main()
