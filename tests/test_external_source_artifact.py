from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agi.external_source_artifact import (
    ExternalSourceArtifactError,
    build_external_handoff_from_git,
    build_git_archive_artifact,
    validate_git_archive_artifact,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.check_call(["git", "-C", str(root), "init", "-q"])
    subprocess.check_call(["git", "-C", str(root), "config", "user.name", "O test"])
    subprocess.check_call(["git", "-C", str(root), "config", "user.email", "o-test@example.invalid"])
    (root / "tracked.txt").write_text("v1\n", encoding="utf-8")
    subprocess.check_call(["git", "-C", str(root), "add", "tracked.txt"])
    subprocess.check_call(
        ["git", "-C", str(root), "-c", "commit.gpgsign=false", "commit", "-q", "-m", "first"]
    )
    return root, _git(root, "rev-parse", "HEAD")


def test_exact_commit_archive_ignores_dirty_and_untracked_worktree(tmp_path: Path) -> None:
    root, commit = _repo(tmp_path)
    first, first_archive = build_git_archive_artifact(root, commit)

    (root / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    (root / "untracked.txt").write_text("not in archive\n", encoding="utf-8")
    second, second_archive = build_git_archive_artifact(root, commit)

    assert first == second
    assert first_archive == second_archive
    assert first["commit_sha"] == commit
    assert first["working_tree_used"] is False
    assert first["untracked_files_included"] is False
    assert validate_git_archive_artifact(first, first_archive)["valid"] is True


def test_new_commit_changes_source_artifact_digest(tmp_path: Path) -> None:
    root, first_commit = _repo(tmp_path)
    first, _ = build_git_archive_artifact(root, first_commit)

    (root / "tracked.txt").write_text("v2\n", encoding="utf-8")
    subprocess.check_call(["git", "-C", str(root), "add", "tracked.txt"])
    subprocess.check_call(
        ["git", "-C", str(root), "-c", "commit.gpgsign=false", "commit", "-q", "-m", "second"]
    )
    second_commit = _git(root, "rev-parse", "HEAD")
    second, _ = build_git_archive_artifact(root, second_commit)

    assert second_commit != first_commit
    assert second["tree_sha"] != first["tree_sha"]
    assert second["artifact_sha256"] != first["artifact_sha256"]


def test_external_handoff_binds_request_to_reconstructed_archive(tmp_path: Path) -> None:
    root, commit = _repo(tmp_path)
    artifact, request, archive = build_external_handoff_from_git(
        root=root,
        repository="example/system",
        commit_sha=commit,
        producer_id="candidate-system",
    )

    assert request["subject"] == {
        "repository": "example/system",
        "commit_sha": commit,
        "artifact_sha256": artifact["artifact_sha256"],
        "producer_id": "candidate-system",
    }
    assert validate_git_archive_artifact(artifact, archive)["artifact_sha256"] == request["subject"]["artifact_sha256"]
    assert request["request_sha256"]


def test_archive_validation_rejects_tampering_and_noncanonical_commit(tmp_path: Path) -> None:
    root, commit = _repo(tmp_path)
    metadata, archive = build_git_archive_artifact(root, commit)

    with pytest.raises(ExternalSourceArtifactError, match="SHA-256"):
        validate_git_archive_artifact(metadata, archive + b"tamper")
    with pytest.raises(ExternalSourceArtifactError, match="canonical lowercase 40-hex"):
        build_git_archive_artifact(root, commit.upper())


def test_cli_outputs_reconstructable_metadata_request_and_archive(tmp_path: Path, capsys) -> None:
    from agi.external_source_artifact import run_cli

    root, commit = _repo(tmp_path)
    archive = tmp_path / "handoff" / "system.tar"
    metadata = tmp_path / "handoff" / "artifact.json"
    request = tmp_path / "handoff" / "request.json"

    code = run_cli(
        [
            "create",
            "--root",
            str(root),
            "--repository",
            "example/system",
            "--commit-sha",
            commit,
            "--producer-id",
            "candidate-system",
            "--archive-output",
            str(archive),
            "--metadata-output",
            str(metadata),
            "--request-output",
            str(request),
        ]
    )

    output = json.loads(capsys.readouterr().out)
    metadata_value = json.loads(metadata.read_text(encoding="utf-8"))
    request_value = json.loads(request.read_text(encoding="utf-8"))
    assert code == 0
    assert output["valid"] is True
    assert archive.is_file()
    assert metadata_value["artifact_sha256"] == request_value["subject"]["artifact_sha256"]
    assert request_value["subject"]["commit_sha"] == commit

    validate_code = run_cli(["validate", "--metadata", str(metadata), "--archive", str(archive)])
    validated = json.loads(capsys.readouterr().out)
    assert validate_code == 0
    assert validated["valid"] is True
