"""Read-only native publication review; never resumes O or changes authority.

This is a handover preparation utility, not an alternate semantic executor.
Use verified main code for the existing native verifier. No dependency install,
Git ref update, response creation, or repository file write occurs here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args])


def blob(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def review(root: Path, run_id: str, base_ref: str, includes: list[str]) -> dict:
    from continual.store import Store
    from continual.work_session import verify_work_invocations, WorkSessionError

    root = root.resolve()
    if not re.fullmatch(r"run-[A-Za-z0-9._-]{6,128}", run_id):
        raise ValueError("invalid run ID")
    base = git(root, "rev-parse", "--verify", base_ref + "^{commit}").decode().strip()
    tracked = {}
    for entry in git(root, "ls-tree", "-rz", base).split(b"\0"):
        if entry:
            info, path = entry.split(b"\t", 1)
            mode, kind, sha = info.decode().split()
            tracked[path.decode()] = {"mode": mode, "type": kind, "sha": sha}

    run_dir = root / ".continual/runs" / run_id
    if not run_dir.is_dir():
        raise ValueError("run directory missing")
    paths = set()

    def include_file(path: Path) -> None:
        rel = path.relative_to(root).as_posix()
        if rel == "agi/WORK_EXECUTION_STATE.json":
            raise ValueError("live authority requires its separate expected-blob CAS")
        if path.is_symlink() or path.resolve() != path or not path.is_file():
            raise ValueError("not a regular in-repository file: " + rel)
        paths.add(path)

    for path in run_dir.rglob("*"):
        if path.is_symlink():
            raise ValueError("native symlink: " + str(path))
        if path.is_file():
            include_file(path)
    run_requests = []
    for request_path in sorted((root / ".continual/work-model/invocations").glob("invoke-*/request.json")):
        request = json.loads(request_path.read_bytes())
        if request.get("run_id") == run_id:
            run_requests.append((request_path, request))
            for path in request_path.parent.iterdir():
                if path.is_file() or path.is_symlink():
                    include_file(path)
    for rel in includes:
        if Path(rel).is_absolute() or ".." in Path(rel).parts:
            raise ValueError("include must be a repository-relative file")
        include_file(root / rel)

    errors = []
    try:
        native_integrity = verify_work_invocations(root, run_id=run_id)
        native_integrity.pop("invocation_ids", None)
    except WorkSessionError as exc:
        native_integrity = {"valid": False}
        errors.append({"kind": "native_artifact_integrity", "message": str(exc)})

    # A complete pre-application journal has another persistence dependency:
    # the Engine cache. Losing it creates a second Candidate request on resume.
    cache_checks = []
    for path in sorted((run_dir / "invocations").glob("*.json")):
        raw = path.read_bytes()
        rel = path.relative_to(root).as_posix()
        if tracked.get(rel, {}).get("sha") == blob(raw):
            continue
        journal = json.loads(raw)
        if journal.get("status") != "complete" or journal.get("component") != "candidate_evaluate":
            continue
        # Completed journals replace the awaiting record, so its Work pointer
        # is no longer present. Cross-bind payload, prompt and frozen output.
        matches = []
        for request_path, request in run_requests:
            if (request.get("component") != journal["component"]
                    or request.get("prompt_path") != journal.get("prompt_path")
                    or not request.get("payload_digest", "").startswith(journal["payload_digest"])):
                continue
            response_path = request_path.parent / "response.json"
            if response_path.is_file() and json.loads(response_path.read_bytes()).get("output") == journal["output"]:
                matches.append(request)
        if len(matches) != 1:
            errors.append({"kind": "preflight_request_binding", "path": rel,
                           "message": "completed Candidate must bind exactly one immutable Work request and response"})
            continue
        request = matches[0]
        payload = request["payload"]
        if payload.get("mode") != "pre-application":
            continue
        unit = payload["execution_unit"]
        identity_unit = unit.get("execution_unit", unit)
        identity = {
            "mode": "pre-application",
            "target_component": payload["target_component"],
            "execution_unit": identity_unit,
            "candidate_index": payload["candidate_index"],
        }
        cache_ref = (run_dir / "preflight" / (
            "preflight-" + payload["target_component"] + "-" +
            Store.stable_digest(identity) + ".json"
        )).relative_to(root).as_posix()
        cache_path = root / cache_ref
        present = cache_path.is_file() and not cache_path.is_symlink()
        exact = present and json.loads(cache_path.read_bytes()) == journal["output"]
        cache_checks.append({"native_invocation_id": journal["invocation_id"], "path": cache_ref,
                             "present": present, "equals_completed_output": exact})
        if not exact:
            errors.append({"kind": "preflight_cache_dependency", "path": cache_ref,
                           "message": "completed preflight cache missing or differs from immutable output"})

    files = []
    for path in sorted(paths):
        raw = path.read_bytes()
        rel = path.relative_to(root).as_posix()
        sha = blob(raw)
        mode = "100755" if path.stat().st_mode & 0o111 else "100644"
        if tracked.get(rel) == {"mode": mode, "type": "blob", "sha": sha}:
            continue
        item = {"path": rel, "mode": mode, "type": "blob", "sha": sha}
        if not errors:
            item["content"] = raw.decode("utf-8")
        files.append(item)
    return {"valid": not errors, "operation": "read_only_publication_review", "base_sha": base,
            "run_id": run_id, "native_integrity": native_integrity, "errors": errors,
            "preflight_cache_checks": cache_checks, "files": files,
            "authority_mutation": False, "native_resume": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--validator-source", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--include", action="append", default=[])
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(args.validator_source.resolve()))
    result = review(args.root, args.run_id, args.base_ref, args.include)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["valid"] else 1)
