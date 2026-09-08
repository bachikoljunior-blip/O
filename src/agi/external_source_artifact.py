from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

from .external_evaluation_request import build_external_evaluation_request
from .external_system_manifest import (
    build_public_system_manifest,
    validate_public_system_manifest,
)


ARTIFACT_KIND = "git-archive-tar-v1"


class ExternalSourceArtifactError(ValueError):
    pass


def _is_lower_hex(value: str, length: int) -> bool:
    if len(value) != length or value.lower() != value:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True


def _git(root: Path, *args: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise ExternalSourceArtifactError(f"git invocation failed: {exc}") from exc
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ExternalSourceArtifactError(
            f"git {' '.join(args)} failed with exit code {completed.returncode}: {stderr}"
        )
    return completed.stdout


def build_git_archive_artifact(root: Path, commit_sha: str) -> tuple[dict[str, Any], bytes]:
    """Build a reproducible tracked-source artifact for one exact Git commit.

    The archive is produced from the commit object, never from the working tree, so dirty or untracked
    files cannot silently change the handoff. The returned SHA-256 is the artifact digest bound into
    the strict external evaluation request.
    """

    root = root.resolve()
    if not _is_lower_hex(commit_sha, 40):
        raise ExternalSourceArtifactError(
            "commit_sha must be a canonical lowercase 40-hex Git commit id"
        )
    resolved = _git(root, "rev-parse", "--verify", f"{commit_sha}^{{commit}}").decode(
        "utf-8"
    ).strip()
    if resolved != commit_sha:
        raise ExternalSourceArtifactError("resolved commit does not match requested commit_sha")
    tree_sha = _git(root, "show", "-s", "--format=%T", commit_sha).decode("utf-8").strip()
    if not _is_lower_hex(tree_sha, 40):
        raise ExternalSourceArtifactError("git returned a malformed tree id")
    archive = _git(root, "archive", "--format=tar", commit_sha)
    artifact_sha256 = hashlib.sha256(archive).hexdigest()
    metadata = {
        "schema_version": 1,
        "artifact_kind": ARTIFACT_KIND,
        "commit_sha": commit_sha,
        "tree_sha": tree_sha,
        "artifact_sha256": artifact_sha256,
        "size_bytes": len(archive),
        "working_tree_used": False,
        "untracked_files_included": False,
        "claim_boundary": (
            "This metadata proves only the digest of a tracked-source archive reconstructed from the "
            "specified Git commit. It is not external evaluation evidence and does not establish AGI."
        ),
    }
    return metadata, archive


def validate_git_archive_artifact(metadata: Mapping[str, Any], archive: bytes) -> dict[str, Any]:
    if metadata.get("schema_version") != 1 or metadata.get("artifact_kind") != ARTIFACT_KIND:
        raise ExternalSourceArtifactError("unsupported source artifact schema/kind")
    commit_sha = str(metadata.get("commit_sha", ""))
    tree_sha = str(metadata.get("tree_sha", ""))
    artifact_sha256 = str(metadata.get("artifact_sha256", ""))
    if not _is_lower_hex(commit_sha, 40) or not _is_lower_hex(tree_sha, 40):
        raise ExternalSourceArtifactError("source artifact commit/tree ids must be canonical 40-hex")
    if not _is_lower_hex(artifact_sha256, 64):
        raise ExternalSourceArtifactError("artifact_sha256 must be canonical lowercase SHA-256")
    if not isinstance(metadata.get("size_bytes"), int) or metadata["size_bytes"] < 0:
        raise ExternalSourceArtifactError("size_bytes must be a non-negative integer")
    actual_sha256 = hashlib.sha256(archive).hexdigest()
    if actual_sha256 != artifact_sha256:
        raise ExternalSourceArtifactError("archive SHA-256 does not match metadata")
    if len(archive) != metadata["size_bytes"]:
        raise ExternalSourceArtifactError("archive size does not match metadata")
    if metadata.get("working_tree_used") is not False or metadata.get("untracked_files_included") is not False:
        raise ExternalSourceArtifactError("artifact metadata must attest commit-only tracked-source construction")
    return {
        "valid": True,
        "commit_sha": commit_sha,
        "tree_sha": tree_sha,
        "artifact_sha256": artifact_sha256,
        "size_bytes": len(archive),
        "claim_boundary": "Artifact validation is not external AGI evidence.",
    }


def build_external_handoff_from_git(
    *,
    root: Path,
    repository: str,
    commit_sha: str,
    producer_id: str,
    provider: str,
    model: str,
    runner: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes]:
    artifact, archive = build_git_archive_artifact(root, commit_sha)
    system_manifest = build_public_system_manifest(
        repository=repository,
        commit_sha=commit_sha,
        artifact_sha256=artifact["artifact_sha256"],
        provider=provider,
        model=model,
        runner=runner,
    )
    request = build_external_evaluation_request(
        repository=repository,
        commit_sha=commit_sha,
        artifact_sha256=artifact["artifact_sha256"],
        system_manifest_sha256=system_manifest["manifest_sha256"],
        producer_id=producer_id,
    )
    return artifact, system_manifest, request, archive


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agi-external-artifact",
        description=(
            "Build or validate a deterministic tracked-source Git archive and bound public runtime "
            "manifest for the strict external AGI evaluation handoff."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--root", type=Path, default=Path.cwd())
    create.add_argument("--repository", required=True)
    create.add_argument("--commit-sha", required=True)
    create.add_argument("--producer-id", required=True)
    create.add_argument("--provider", required=True)
    create.add_argument("--model", required=True)
    create.add_argument("--runner", required=True)
    create.add_argument("--archive-output", type=Path, required=True)
    create.add_argument("--metadata-output", type=Path, required=True)
    create.add_argument("--system-manifest-output", type=Path, required=True)
    create.add_argument("--request-output", type=Path, required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--metadata", type=Path, required=True)
    validate.add_argument("--archive", type=Path, required=True)
    validate.add_argument("--system-manifest", type=Path)
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            artifact, system_manifest, request, archive = build_external_handoff_from_git(
                root=args.root,
                repository=args.repository,
                commit_sha=args.commit_sha,
                producer_id=args.producer_id,
                provider=args.provider,
                model=args.model,
                runner=args.runner,
            )
            args.archive_output.parent.mkdir(parents=True, exist_ok=True)
            args.archive_output.write_bytes(archive)
            _write_json(args.metadata_output, artifact)
            _write_json(args.system_manifest_output, system_manifest)
            _write_json(args.request_output, request)
            result: Mapping[str, Any] = {
                "valid": True,
                "artifact": artifact,
                "system_manifest_sha256": system_manifest["manifest_sha256"],
                "evaluation_request_sha256": request["request_sha256"],
                "archive_output": str(args.archive_output),
                "metadata_output": str(args.metadata_output),
                "system_manifest_output": str(args.system_manifest_output),
                "request_output": str(args.request_output),
                "claim_boundary": "The generated handoff is operational scaffolding, not AGI evidence.",
            }
        else:
            metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
            if not isinstance(metadata, Mapping):
                raise ExternalSourceArtifactError("metadata must contain a JSON object")
            result = validate_git_archive_artifact(metadata, args.archive.read_bytes())
            if args.system_manifest is not None:
                manifest = json.loads(args.system_manifest.read_text(encoding="utf-8"))
                if not isinstance(manifest, Mapping):
                    raise ExternalSourceArtifactError("system manifest must contain a JSON object")
                manifest_result = validate_public_system_manifest(manifest)
                if manifest_result["commit_sha"] != result["commit_sha"]:
                    raise ExternalSourceArtifactError("system manifest commit does not match source artifact")
                if manifest_result["artifact_sha256"] != result["artifact_sha256"]:
                    raise ExternalSourceArtifactError(
                        "system manifest artifact digest does not match source artifact"
                    )
                result = {**result, "system_manifest": manifest_result}
    except (ExternalSourceArtifactError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(dict(result), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
