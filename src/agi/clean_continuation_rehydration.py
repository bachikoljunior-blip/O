from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from continual.engine import Engine
from continual.work_checkpoint_integrity import verify_work_checkpoint_integrity
from continual.work_session import pending_work_invocations


_SHA = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_INVOCATION = re.compile(r"^invoke-[0-9a-f]{24}$")
_RUN = re.compile(r"^run-[A-Za-z0-9._-]{6,128}$")
_STATE = Path("agi/WORK_EXECUTION_STATE.json")


class RehydrationError(RuntimeError):
    """The declared continuation cannot be reconstructed exactly."""


class _NoSemanticProvider:
    model = "clean-continuation-no-semantic-provider"

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, *_args: Any, **_kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("semantic provider must not be called by a zero-step probe")


def git_blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


def _json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RehydrationError(f"{label} is missing or malformed: {exc}") from exc
    if not isinstance(value, dict):
        raise RehydrationError(f"{label} must be a JSON object")
    return value


def _safe_rel(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise RehydrationError(f"{label} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise RehydrationError(f"{label} is not a canonical safe relative path")
    return path


def _require(value: bool, message: str) -> None:
    if not value:
        raise RehydrationError(message)


def _tree_inventory(root: Path) -> dict[str, Any]:
    records: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RehydrationError(f"clean projection contains symlink: {path.relative_to(root)}")
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            records.append((rel, hashlib.sha256(path.read_bytes()).hexdigest()))
    encoded = json.dumps(records, separators=(",", ":"), ensure_ascii=False).encode()
    return {
        "file_count": len(records),
        "digest": hashlib.sha256(encoded).hexdigest(),
    }


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=repo, text=True, stderr=subprocess.STDOUT
        ).strip()
    except subprocess.CalledProcessError as exc:
        raise RehydrationError(f"git {' '.join(args)} failed: {exc.output.strip()}") from exc


def _repo_observation(repo: Path) -> dict[str, Any]:
    return {
        "head": _git(repo, "rev-parse", "HEAD"),
        "status_digest": hashlib.sha256(
            subprocess.check_output(["git", "status", "--porcelain=v1", "-z"], cwd=repo)
        ).hexdigest(),
        "object_count": _git(repo, "count-objects", "-v"),
    }


def _materialize(repo: Path, commit: str, expected_tree: str, destination: Path) -> None:
    actual_tree = _git(repo, "rev-parse", f"{commit}^{{tree}}")
    _require(actual_tree == expected_tree, "source commit tree does not match declared remote tree")
    archive = subprocess.check_output(["git", "archive", "--format=tar", commit], cwd=repo)
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        for member in bundle.getmembers():
            path = Path(member.name)
            _require(
                not path.is_absolute() and ".." not in path.parts,
                "git archive contains an unsafe path",
            )
            _require(not member.issym() and not member.islnk(), "git archive contains a link")
        bundle.extractall(destination, filter="data")
    _require(not (destination / ".git").exists(), "clean projection unexpectedly contains .git")


def validate_projection(root: Path, declaration: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an already materialized exact-commit projection without mutation."""

    root = root.resolve()
    commit = declaration.get("source_commit_sha")
    tree = declaration.get("source_tree_sha")
    generation = declaration.get("lease_generation")
    execution_id = declaration.get("execution_id")
    fence = declaration.get("fence_token")
    fence_digest = declaration.get("fence_token_digest")
    run_id = declaration.get("run_id")
    work_id = declaration.get("pending_work_invocation_id")
    native_id = declaration.get("pending_native_invocation_id")
    request_digest = declaration.get("pending_request_digest")
    _require(isinstance(commit, str) and _SHA.fullmatch(commit) is not None, "invalid source commit")
    _require(isinstance(tree, str) and _SHA.fullmatch(tree) is not None, "invalid source tree")
    _require(isinstance(generation, int) and generation >= 1, "invalid lease generation")
    _require(isinstance(execution_id, str) and bool(execution_id), "invalid execution id")
    _require(isinstance(fence, str) and bool(fence), "invalid fence token")
    _require(isinstance(fence_digest, str) and _HEX64.fullmatch(fence_digest) is not None, "invalid fence digest")
    _require(hashlib.sha256(fence.encode()).hexdigest() == fence_digest, "declared fence digest mismatch")
    _require(isinstance(run_id, str) and _RUN.fullmatch(run_id) is not None, "invalid run id")
    _require(isinstance(work_id, str) and _INVOCATION.fullmatch(work_id) is not None, "invalid Work invocation id")
    _require(isinstance(native_id, str) and _INVOCATION.fullmatch(native_id) is not None, "invalid native invocation id")
    _require(isinstance(request_digest, str) and _HEX64.fullmatch(request_digest) is not None, "invalid request digest")

    declared_blobs = declaration.get("blobs")
    _require(isinstance(declared_blobs, Mapping) and bool(declared_blobs), "blobs must be a non-empty object")
    for raw_path, expected in declared_blobs.items():
        path = _safe_rel(raw_path, "blob path")
        _require(isinstance(expected, str) and _SHA.fullmatch(expected) is not None, f"invalid blob SHA for {path}")
        try:
            content = (root / path).read_bytes()
        except OSError as exc:
            raise RehydrationError(f"declared blob is missing: {path}") from exc
        _require(git_blob_sha(content) == expected, f"declared blob mismatch: {path}")

    state = _json(root / _STATE, "state")
    exact = state.get("exact_continuation")
    primary = state.get("primary_native_run")
    _require(isinstance(exact, Mapping), "state exact_continuation is malformed")
    _require(isinstance(primary, Mapping), "state primary_native_run is malformed")
    _require(state.get("execution_id") == execution_id, "state execution id mismatch")
    _require(state.get("lease_generation") == generation, "state generation mismatch")
    _require(state.get("fence_token") == fence, "state fence mismatch")
    _require(exact.get("status") == "remote_main_readback_verified", "continuation is not remote-main verified")
    _require(exact.get("lease_generation") == generation, "continuation generation mismatch")
    _require(exact.get("fence_token_digest") == fence_digest, "continuation fence digest mismatch")
    _require(state.get("active_run_id") == run_id and primary.get("run_id") == run_id, "native run binding mismatch")
    _require(exact.get("pending_work_invocation_id") == work_id, "pending Work id mismatch")
    _require(exact.get("pending_native_invocation_id") == native_id, "pending native id mismatch")
    _require(exact.get("pending_request_digest") == request_digest, "pending request digest mismatch")

    request_ref = _safe_rel(exact.get("pending_request_ref"), "pending request ref")
    native_ref = _safe_rel(exact.get("pending_native_invocation_ref"), "pending native ref")
    snapshot_ref = _safe_rel(exact.get("run_snapshot_ref"), "snapshot ref")
    _require(request_ref == Path(f".continual/work-model/invocations/{work_id}/request.json"), "noncanonical request ref")
    _require(native_ref == Path(f".continual/runs/{run_id}/invocations/{native_id}.json"), "noncanonical native ref")
    request = _json(root / request_ref, "pending Work request")
    native = _json(root / native_ref, "pending native invocation")
    snapshot = _json(root / snapshot_ref, "native snapshot")
    _require(request.get("request_digest") == request_digest, "Work request digest mismatch")
    _require(request.get("run_id") == run_id, "Work request run mismatch")
    _require(native.get("status") == "awaiting_work_model", "native invocation is not awaiting Work")
    _require(native.get("work_invocation_id") == work_id, "native Work id mismatch")
    _require(native.get("work_request_digest") == request_digest, "native request digest mismatch")
    _require(native.get("work_request_ref") == request_ref.as_posix(), "native request ref mismatch")
    _require(not (root / request_ref.parent / "response.json").exists(), "pending Work response already exists")
    _require(snapshot.get("run_id") == run_id, "snapshot run mismatch")
    _require(snapshot.get("revision") == exact.get("snapshot_revision"), "snapshot revision mismatch")
    _require(snapshot.get("phase") == exact.get("snapshot_phase") == "root_pending", "snapshot phase mismatch")

    checkpoint = verify_work_checkpoint_integrity(root, state=state)
    _require(checkpoint.get("valid") is True, "checkpoint integrity verification failed")
    pending = pending_work_invocations(root, run_id=run_id)
    _require(len(pending) == 1, "projection must contain exactly one pending Work request")
    _require(pending[0].get("invocation_id") == work_id, "pending Work request identity mismatch")
    _require(pending[0].get("request_digest") == request_digest, "pending Work request verification mismatch")

    before = _tree_inventory(root)
    provider = _NoSemanticProvider()
    try:
        Engine(root, provider).resume(run_id, max_steps=0)
    except ValueError as exc:
        _require(str(exc) == "max_steps must be at least 1", "unexpected zero-step rejection")
    else:
        raise RehydrationError("Engine unexpectedly accepted a zero-step resume")
    after = _tree_inventory(root)
    _require(provider.calls == 0, "zero-step probe called semantic provider")
    _require(before == after, "zero-step probe mutated clean projection")
    return {
        "schema_version": 1,
        "status": "PASS",
        "source_commit_sha": commit,
        "source_tree_sha": tree,
        "execution_id": execution_id,
        "lease_generation": generation,
        "run_id": run_id,
        "pending_work_invocation_id": work_id,
        "pending_native_invocation_id": native_id,
        "pending_request_digest": request_digest,
        "checkpoint_verified_reference_count": len(checkpoint.get("verified_references", [])),
        "pending_count": len(pending),
        "semantic_provider_calls": provider.calls,
        "before": before,
        "after": after,
        "mutation_count": 0,
    }


def replay_key(report: Mapping[str, Any]) -> str:
    bound = {
        key: report.get(key)
        for key in (
            "source_commit_sha",
            "source_tree_sha",
            "execution_id",
            "lease_generation",
            "run_id",
            "pending_work_invocation_id",
            "pending_native_invocation_id",
            "pending_request_digest",
            "before",
            "after",
        )
    }
    return hashlib.sha256(
        json.dumps(bound, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def admit_replay(report: Mapping[str, Any], admitted: set[str]) -> dict[str, Any]:
    key = replay_key(report)
    accepted = key not in admitted
    if accepted:
        admitted.add(key)
    return {"idempotency_key": key, "accepted": accepted, "suppressed_duplicate": not accepted}


def run_rehydration(repo: Path, declaration: Mapping[str, Any]) -> dict[str, Any]:
    """Archive an immutable commit into a clean directory and run the bounded probe."""

    repo = repo.resolve()
    before = _repo_observation(repo)
    commit = declaration.get("source_commit_sha")
    tree = declaration.get("source_tree_sha")
    _require(isinstance(commit, str) and _SHA.fullmatch(commit) is not None, "invalid source commit")
    _require(isinstance(tree, str) and _SHA.fullmatch(tree) is not None, "invalid source tree")
    with tempfile.TemporaryDirectory(prefix="clean-continuation-") as tmp:
        projection = Path(tmp) / "projection"
        _materialize(repo, commit, tree, projection)
        report = validate_projection(projection, declaration)
    after = _repo_observation(repo)
    _require(before == after, "source repository changed during rehydration")
    report["source_repository_before"] = before
    report["source_repository_after"] = deepcopy(after)
    report["source_repository_mutation_count"] = 0
    return report
