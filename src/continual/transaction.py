from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_SECRET_ENV_MARKERS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "AUTH",
    "COOKIE",
    "PRIVATE_KEY",
    "API_KEY",
)
_NETWORK_COMMANDS = {
    "curl",
    "wget",
    "ssh",
    "scp",
    "sftp",
    "nc",
    "ncat",
    "telnet",
    "gh",
}
_NETWORK_GIT_COMMANDS = {
    "push",
    "fetch",
    "pull",
    "clone",
    "remote",
    "ls-remote",
    "submodule",
}


class CandidateTransactionError(RuntimeError):
    pass


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest_json(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=destination.name + ".",
        dir=destination.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _safe_parts(relative: str) -> tuple[str, ...]:
    if not isinstance(relative, str) or not relative.strip():
        raise CandidateTransactionError("path must be a non-empty string")
    path = PurePosixPath(relative.replace("\\", "/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CandidateTransactionError(f"unsafe repository path: {relative}")
    return tuple(path.parts)


def _assert_no_symlink(root: Path, parts: tuple[str, ...]) -> Path:
    current = root
    for part in parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise CandidateTransactionError(f"symlink path is not allowed: {'/'.join(parts)}")
    resolved = (root / Path(*parts)).resolve()
    if resolved != root and root not in resolved.parents:
        raise CandidateTransactionError("path escapes repository")
    return resolved


def _file_digest(path: Path) -> str | None:
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise CandidateTransactionError(f"not a regular file: {path}")
    return _digest_bytes(path.read_bytes())


def _sanitised_environment() -> dict[str, str]:
    environment: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper.startswith("OPENAI_") or upper.startswith("ANTHROPIC_"):
            continue
        if any(marker in upper for marker in _SECRET_ENV_MARKERS):
            continue
        environment[key] = value
    environment["CONTINUAL_CANDIDATE_SANDBOX"] = "1"
    return environment


def _assert_command_is_local(argv: Sequence[str]) -> None:
    if not argv or not all(isinstance(item, str) and item for item in argv):
        raise CandidateTransactionError("validation argv must be a non-empty string array")
    executable = Path(argv[0]).name.lower()
    if executable in _NETWORK_COMMANDS:
        raise CandidateTransactionError(f"network command is blocked: {executable}")
    if executable == "git" and len(argv) > 1 and argv[1].lower() in _NETWORK_GIT_COMMANDS:
        raise CandidateTransactionError(f"network git command is blocked: {argv[1]}")


def _run(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    _assert_command_is_local(argv)
    timeout = max(1, min(int(timeout_seconds), 900))
    started = __import__("time").perf_counter_ns()
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(environment) if environment is not None else None,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        timed_out = False
    except subprocess.TimeoutExpired as error:
        returncode = 124
        stdout = error.stdout or ""
        stderr = error.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        timed_out = True
    duration_ms = (__import__("time").perf_counter_ns() - started) / 1_000_000
    return {
        "argv": list(argv),
        "returncode": returncode,
        "timed_out": timed_out,
        "duration_ms": round(duration_ms, 3),
        "stdout_sha256": _digest_bytes(stdout.encode("utf-8", errors="replace")),
        "stderr_sha256": _digest_bytes(stderr.encode("utf-8", errors="replace")),
        "stdout_tail": stdout[-20000:],
        "stderr_tail": stderr[-20000:],
    }


def _git(root: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        raise CandidateTransactionError(
            f"git {' '.join(arguments)} failed: {completed.stderr[-2000:]}"
        )
    return completed


def _head(root: Path) -> str:
    value = _git(root, "rev-parse", "HEAD").stdout.strip().lower()
    if not _COMMIT_RE.fullmatch(value):
        raise CandidateTransactionError("repository HEAD is not a SHA-1 commit")
    return value


def _tracked_clean(root: Path) -> bool:
    return not _git(
        root,
        "status",
        "--porcelain",
        "--untracked-files=no",
    ).stdout.strip()


@dataclass(frozen=True)
class PatchOperation:
    action: str
    path: str
    expected_sha256: str | None
    content: str | None = None
    new_sha256: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PatchOperation":
        operation = cls(
            action=str(value.get("action", "")),
            path=str(value.get("path", "")),
            expected_sha256=(
                str(value["expected_sha256"]).lower()
                if value.get("expected_sha256") is not None
                else None
            ),
            content=(str(value["content"]) if value.get("content") is not None else None),
            new_sha256=(
                str(value["new_sha256"]).lower()
                if value.get("new_sha256") is not None
                else None
            ),
        )
        operation.validate()
        return operation

    def validate(self) -> None:
        _safe_parts(self.path)
        if self.action not in {"write", "delete"}:
            raise CandidateTransactionError(f"unsupported patch action: {self.action}")
        if self.expected_sha256 is not None and not _SHA256_RE.fullmatch(
            self.expected_sha256
        ):
            raise CandidateTransactionError("expected_sha256 is invalid")
        if self.action == "write":
            if self.content is None:
                raise CandidateTransactionError("write operation requires content")
            digest = _digest_bytes(self.content.encode("utf-8"))
            if self.new_sha256 != digest:
                raise CandidateTransactionError("new_sha256 does not match content")
        else:
            if self.expected_sha256 is None:
                raise CandidateTransactionError("delete operation requires expected_sha256")
            if self.content is not None or self.new_sha256 is not None:
                raise CandidateTransactionError("delete operation cannot contain new content")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateManifest:
    candidate_id: str
    base_commit: str
    target_scope: str
    rationale: str
    operations: tuple[PatchOperation, ...]
    schema_version: int = 1

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CandidateManifest":
        raw_operations = value.get("operations")
        if not isinstance(raw_operations, list):
            raise CandidateTransactionError("operations must be an array")
        manifest = cls(
            candidate_id=str(value.get("candidate_id", "")),
            base_commit=str(value.get("base_commit", "")).lower(),
            target_scope=str(value.get("target_scope", "")),
            rationale=str(value.get("rationale", "")),
            operations=tuple(
                PatchOperation.from_mapping(item)
                for item in raw_operations
                if isinstance(item, Mapping)
            ),
            schema_version=int(value.get("schema_version", 1)),
        )
        if len(manifest.operations) != len(raw_operations):
            raise CandidateTransactionError("every operation must be an object")
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if self.schema_version != 1:
            raise CandidateTransactionError("unsupported Candidate manifest schema")
        if not _SAFE_ID_RE.fullmatch(self.candidate_id):
            raise CandidateTransactionError("candidate_id is unsafe")
        if not _COMMIT_RE.fullmatch(self.base_commit):
            raise CandidateTransactionError("base_commit must be a 40-character commit SHA")
        if not self.target_scope or not self.rationale:
            raise CandidateTransactionError("target_scope and rationale are required")
        if not self.operations:
            raise CandidateTransactionError("Candidate must contain at least one operation")
        paths = [operation.path for operation in self.operations]
        if len(paths) != len(set(paths)):
            raise CandidateTransactionError("Candidate contains duplicate paths")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "base_commit": self.base_commit,
            "target_scope": self.target_scope,
            "rationale": self.rationale,
            "operations": [operation.to_dict() for operation in self.operations],
        }


@dataclass(frozen=True)
class CommandSpec:
    argv: tuple[str, ...]
    timeout_seconds: int = 300

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CommandSpec":
        argv = value.get("argv")
        if not isinstance(argv, list):
            raise CandidateTransactionError("command argv must be an array")
        command = cls(
            argv=tuple(str(item) for item in argv),
            timeout_seconds=int(value.get("timeout_seconds", 300)),
        )
        _assert_command_is_local(command.argv)
        if not 1 <= command.timeout_seconds <= 900:
            raise CandidateTransactionError("command timeout must be between 1 and 900")
        return command

    def to_dict(self) -> dict[str, Any]:
        return {"argv": list(self.argv), "timeout_seconds": self.timeout_seconds}


@dataclass(frozen=True)
class ValidationPlan:
    plan_id: str
    commands: tuple[CommandSpec, ...]
    require_regression_adoption: bool = True
    schema_version: int = 1

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ValidationPlan":
        raw_commands = value.get("commands")
        if not isinstance(raw_commands, list):
            raise CandidateTransactionError("commands must be an array")
        plan = cls(
            plan_id=str(value.get("plan_id", "")),
            commands=tuple(
                CommandSpec.from_mapping(item)
                for item in raw_commands
                if isinstance(item, Mapping)
            ),
            require_regression_adoption=bool(
                value.get("require_regression_adoption", True)
            ),
            schema_version=int(value.get("schema_version", 1)),
        )
        if len(plan.commands) != len(raw_commands):
            raise CandidateTransactionError("every command must be an object")
        plan.validate()
        return plan

    def validate(self) -> None:
        if self.schema_version != 1:
            raise CandidateTransactionError("unsupported validation plan schema")
        if not _SAFE_ID_RE.fullmatch(self.plan_id):
            raise CandidateTransactionError("plan_id is unsafe")
        if not self.commands:
            raise CandidateTransactionError("validation plan requires at least one command")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "commands": [command.to_dict() for command in self.commands],
            "require_regression_adoption": self.require_regression_adoption,
        }


@dataclass(frozen=True)
class TransactionPolicy:
    allowed_prefixes: tuple[tuple[str, ...], ...] = (
        ("src",),
        ("agi",),
        ("README.md",),
        ("SYSTEM_DESIGN.md",),
    )
    blocked_prefixes: tuple[tuple[str, ...], ...] = (
        (".git",),
        (".continual",),
        (".github",),
        ("evidence",),
        ("prompts",),
        ("tests",),
        ("pyproject.toml",),
    )
    maximum_files: int = 20
    maximum_total_utf8_bytes: int = 1_000_000

    def assert_operation_allowed(self, operation: PatchOperation) -> None:
        parts = _safe_parts(operation.path)
        if any(parts[: len(prefix)] == prefix for prefix in self.blocked_prefixes):
            raise CandidateTransactionError(f"protected path: {operation.path}")
        if not any(parts[: len(prefix)] == prefix for prefix in self.allowed_prefixes):
            raise CandidateTransactionError(f"path outside Candidate policy: {operation.path}")

    def validate_manifest(self, manifest: CandidateManifest) -> None:
        if len(manifest.operations) > self.maximum_files:
            raise CandidateTransactionError("Candidate changes too many files")
        total = 0
        for operation in manifest.operations:
            self.assert_operation_allowed(operation)
            if operation.content is not None:
                total += len(operation.content.encode("utf-8"))
        if total > self.maximum_total_utf8_bytes:
            raise CandidateTransactionError("Candidate content exceeds byte limit")


def _apply_operations(
    root: Path,
    manifest: CandidateManifest,
    policy: TransactionPolicy,
) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    for operation in manifest.operations:
        policy.assert_operation_allowed(operation)
        parts = _safe_parts(operation.path)
        target = _assert_no_symlink(root, parts)
        before = _file_digest(target)
        if before != operation.expected_sha256:
            raise CandidateTransactionError(
                f"precondition hash mismatch for {operation.path}: "
                f"expected={operation.expected_sha256}, actual={before}"
            )
        if operation.action == "write":
            assert operation.content is not None
            _atomic_text(target, operation.content)
            after = _file_digest(target)
            if after != operation.new_sha256:
                raise CandidateTransactionError(
                    f"post-write hash mismatch for {operation.path}"
                )
        else:
            target.unlink()
            after = None
        applied.append(
            {
                "action": operation.action,
                "path": operation.path,
                "before_sha256": before,
                "after_sha256": after,
            }
        )
    return applied


def _validation_report_path(root: Path, candidate_id: str) -> Path:
    return root / ".continual" / "transactions" / candidate_id / "validation.json"


def validate_candidate(
    root: Path,
    manifest: CandidateManifest,
    plan: ValidationPlan,
    *,
    regression_decision: Mapping[str, Any] | None = None,
    policy: TransactionPolicy = TransactionPolicy(),
) -> dict[str, Any]:
    repository = root.resolve()
    manifest.validate()
    plan.validate()
    policy.validate_manifest(manifest)
    if not (repository / ".git").exists():
        raise CandidateTransactionError("repository is not a git worktree")
    if _head(repository) != manifest.base_commit:
        raise CandidateTransactionError("Candidate base_commit is not current HEAD")
    if not _tracked_clean(repository):
        raise CandidateTransactionError("tracked repository files must be clean")

    manifest_payload = manifest.to_dict()
    plan_payload = plan.to_dict()
    manifest_digest = _digest_json(manifest_payload)
    plan_digest = _digest_json(plan_payload)
    reasons: list[str] = []
    command_results: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    candidate_commit: str | None = None
    candidate_ref: str | None = None
    diff_sha256: str | None = None
    worktree_directory = Path(tempfile.mkdtemp(prefix="continual-candidate-"))
    worktree_added = False

    try:
        _git(repository, "worktree", "add", "--detach", str(worktree_directory), manifest.base_commit)
        worktree_added = True
        applied = _apply_operations(worktree_directory, manifest, policy)

        diff = _git(
            worktree_directory,
            "diff",
            "--binary",
            "--no-ext-diff",
            manifest.base_commit,
            "--",
            *[operation.path for operation in manifest.operations],
        ).stdout
        diff_sha256 = _digest_bytes(diff.encode("utf-8"))
        if not diff.strip():
            reasons.append("Candidate produces no tracked diff")

        diff_check = _git(worktree_directory, "diff", "--check", check=False)
        if diff_check.returncode != 0:
            reasons.append("git diff --check failed: " + diff_check.stdout[-2000:])

        environment = _sanitised_environment()
        if not reasons:
            for command in plan.commands:
                result = _run(
                    command.argv,
                    cwd=worktree_directory,
                    timeout_seconds=command.timeout_seconds,
                    environment=environment,
                )
                command_results.append(result)
                if result["returncode"] != 0:
                    reasons.append(
                        "validation command failed: " + " ".join(command.argv)
                    )
                    break

        regression_digest: str | None = None
        if regression_decision is not None:
            regression_digest = _digest_json(regression_decision)
        if plan.require_regression_adoption:
            if regression_decision is None:
                reasons.append("missing trusted regression decision")
            elif regression_decision.get("adopt_candidate") is not True:
                reasons.append("regression gate did not approve Candidate")

        if not reasons:
            paths = [operation.path for operation in manifest.operations]
            _git(worktree_directory, "add", "--", *paths)
            cached = _git(worktree_directory, "diff", "--cached", "--quiet", check=False)
            if cached.returncode == 0:
                reasons.append("Candidate has no staged change")
            elif cached.returncode != 1:
                reasons.append("unable to inspect staged Candidate diff")

        if not reasons:
            _git(
                worktree_directory,
                "-c",
                "user.name=Continual Candidate Validator",
                "-c",
                "user.email=continual-validator@invalid.local",
                "commit",
                "--no-gpg-sign",
                "-m",
                f"Candidate {manifest.candidate_id}: {manifest.target_scope}",
            )
            candidate_commit = _head(worktree_directory)
            candidate_ref = (
                "refs/heads/continual-candidates/"
                + re.sub(r"[^A-Za-z0-9_.-]+", "-", manifest.candidate_id)
            )
            existing = _git(repository, "rev-parse", "--verify", candidate_ref, check=False)
            if existing.returncode == 0:
                existing_commit = existing.stdout.strip().lower()
                if existing_commit != candidate_commit:
                    raise CandidateTransactionError(
                        "candidate_id already points to a different validated commit"
                    )
            else:
                _git(repository, "update-ref", candidate_ref, candidate_commit)

        report_without_digest = {
            "schema_version": 1,
            "candidate_id": manifest.candidate_id,
            "validated": not reasons,
            "reasons": reasons,
            "base_commit": manifest.base_commit,
            "candidate_commit": candidate_commit,
            "candidate_ref": candidate_ref,
            "manifest_sha256": manifest_digest,
            "validation_plan_sha256": plan_digest,
            "manifest": manifest_payload,
            "validation_plan": plan_payload,
            "applied_operations": applied,
            "diff_sha256": diff_sha256,
            "command_results": command_results,
            "regression_decision_sha256": regression_digest,
            "regression_adopted": (
                regression_decision.get("adopt_candidate") is True
                if regression_decision is not None
                else None
            ),
        }
        report = {
            **report_without_digest,
            "report_sha256": _digest_json(report_without_digest),
        }
        _atomic_json(_validation_report_path(repository, manifest.candidate_id), report)
        return report
    finally:
        if worktree_added:
            _git(repository, "worktree", "remove", "--force", str(worktree_directory), check=False)
        if worktree_directory.exists():
            __import__("shutil").rmtree(worktree_directory, ignore_errors=True)
        _git(repository, "worktree", "prune", check=False)


def _verify_validation_report(
    report: Mapping[str, Any],
    plan: ValidationPlan,
) -> tuple[str, str, str]:
    supplied = report.get("report_sha256")
    material = dict(report)
    material.pop("report_sha256", None)
    if supplied != _digest_json(material):
        raise CandidateTransactionError("validation report digest mismatch")
    if report.get("validated") is not True:
        raise CandidateTransactionError("Candidate was not validated")
    if report.get("validation_plan_sha256") != _digest_json(plan.to_dict()):
        raise CandidateTransactionError("promotion plan differs from validation plan")
    base_commit = str(report.get("base_commit", "")).lower()
    candidate_commit = str(report.get("candidate_commit", "")).lower()
    candidate_ref = str(report.get("candidate_ref", ""))
    if not _COMMIT_RE.fullmatch(base_commit) or not _COMMIT_RE.fullmatch(candidate_commit):
        raise CandidateTransactionError("validation report commit is invalid")
    if not candidate_ref.startswith("refs/heads/continual-candidates/"):
        raise CandidateTransactionError("validation report Candidate ref is invalid")
    return base_commit, candidate_commit, candidate_ref


def promote_candidate(
    root: Path,
    validation_report: Mapping[str, Any],
    plan: ValidationPlan,
) -> dict[str, Any]:
    repository = root.resolve()
    plan.validate()
    base_commit, candidate_commit, candidate_ref = _verify_validation_report(
        validation_report,
        plan,
    )
    candidate_id = str(validation_report.get("candidate_id", ""))
    lock_path = repository / ".continual" / "transactions" / "promotion.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.write(descriptor, candidate_id.encode("utf-8"))
            os.fsync(descriptor)
        except FileExistsError as error:
            raise CandidateTransactionError("another Candidate promotion is in progress") from error

        if _head(repository) != base_commit:
            raise CandidateTransactionError("repository advanced after Candidate validation")
        if not _tracked_clean(repository):
            raise CandidateTransactionError("tracked repository files must be clean")
        referenced = _git(repository, "rev-parse", "--verify", candidate_ref).stdout.strip().lower()
        if referenced != candidate_commit:
            raise CandidateTransactionError("Candidate ref does not match validated commit")

        merge = _git(repository, "merge", "--ff-only", candidate_commit, check=False)
        if merge.returncode != 0:
            raise CandidateTransactionError(
                "fast-forward promotion failed: " + merge.stderr[-2000:]
            )

        command_results: list[dict[str, Any]] = []
        reasons: list[str] = []
        environment = _sanitised_environment()
        for command in plan.commands:
            result = _run(
                command.argv,
                cwd=repository,
                timeout_seconds=command.timeout_seconds,
                environment=environment,
            )
            command_results.append(result)
            if result["returncode"] != 0:
                reasons.append("post-promotion command failed: " + " ".join(command.argv))
                break

        rolled_back = bool(reasons)
        if rolled_back:
            _git(repository, "reset", "--hard", base_commit)
        final_head = _head(repository)
        expected_head = base_commit if rolled_back else candidate_commit
        if final_head != expected_head:
            raise CandidateTransactionError("promotion or rollback did not reach expected commit")

        report_without_digest = {
            "schema_version": 1,
            "candidate_id": candidate_id,
            "promoted": not rolled_back,
            "rolled_back": rolled_back,
            "reasons": reasons,
            "base_commit": base_commit,
            "candidate_commit": candidate_commit,
            "final_head": final_head,
            "validation_report_sha256": validation_report.get("report_sha256"),
            "validation_plan_sha256": _digest_json(plan.to_dict()),
            "post_promotion_commands": command_results,
        }
        result = {
            **report_without_digest,
            "report_sha256": _digest_json(report_without_digest),
        }
        _atomic_json(
            repository
            / ".continual"
            / "transactions"
            / candidate_id
            / "promotion.json",
            result,
        )
        return result
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CandidateTransactionError(f"{path} must contain one JSON object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m continual.transaction",
        description="Validate and promote code Candidates through an isolated git transaction.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate")
    validate.add_argument("--repo", type=Path, default=Path("."))
    validate.add_argument("--manifest", type=Path, required=True)
    validate.add_argument("--plan", type=Path, required=True)
    validate.add_argument("--regression", type=Path)
    validate.add_argument("--output", type=Path)

    promote = commands.add_parser("promote")
    promote.add_argument("--repo", type=Path, default=Path("."))
    promote.add_argument("--validation-report", type=Path, required=True)
    promote.add_argument("--plan", type=Path, required=True)
    promote.add_argument("--output", type=Path)
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "validate":
        manifest = CandidateManifest.from_mapping(_load_object(args.manifest))
        plan = ValidationPlan.from_mapping(_load_object(args.plan))
        regression = _load_object(args.regression) if args.regression else None
        result = validate_candidate(
            args.repo,
            manifest,
            plan,
            regression_decision=regression,
        )
        if args.output:
            _atomic_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["validated"] else 2

    if args.command == "promote":
        plan = ValidationPlan.from_mapping(_load_object(args.plan))
        result = promote_candidate(
            args.repo,
            _load_object(args.validation_report),
            plan,
        )
        if args.output:
            _atomic_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["promoted"] else 2

    raise AssertionError(args.command)


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
