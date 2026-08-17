from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from agi.external_system_manifest import (
    ExternalSystemManifestError,
    build_public_system_manifest,
    run_cli,
    validate_public_system_manifest,
)


def _manifest() -> dict:
    return build_public_system_manifest(
        repository="example/system",
        commit_sha="a" * 40,
        artifact_sha256="b" * 64,
        provider="example-provider",
        model="example-model-v1",
        runner="sandbox-runner-v2",
    )


def test_manifest_is_public_canonical_and_binds_runtime_identity() -> None:
    manifest = _manifest()
    result = validate_public_system_manifest(manifest)

    assert result["valid"] is True
    assert result["provider"] == "example-provider"
    assert result["model"] == "example-model-v1"
    assert result["runner"] == "sandbox-runner-v2"
    assert result["manifest_sha256"] == manifest["manifest_sha256"]
    encoded = json.dumps(manifest, sort_keys=True)
    for forbidden_key in ("api_key", "bridge_secret", "password", "private_key", "token"):
        assert f'"{forbidden_key}":' not in encoded


def test_runtime_identity_change_changes_manifest_digest() -> None:
    first = _manifest()
    second = build_public_system_manifest(
        repository="example/system",
        commit_sha="a" * 40,
        artifact_sha256="b" * 64,
        provider="example-provider",
        model="example-model-v2",
        runner="sandbox-runner-v2",
    )

    assert first["manifest_sha256"] != second["manifest_sha256"]


def test_manifest_validation_fails_closed_on_tampering_and_secret_fields() -> None:
    manifest = _manifest()

    tampered = copy.deepcopy(manifest)
    tampered["runtime"]["runner"] = "different-runner"
    with pytest.raises(ExternalSystemManifestError, match="manifest_sha256"):
        validate_public_system_manifest(tampered)

    secret = copy.deepcopy(manifest)
    secret["runtime"]["token"] = "not-public"
    with pytest.raises(ExternalSystemManifestError, match="forbidden secret"):
        validate_public_system_manifest(secret)

    unverified = copy.deepcopy(manifest)
    unverified["identity_status"] = "verified-by-producer"
    with pytest.raises(ExternalSystemManifestError, match="identity_status"):
        validate_public_system_manifest(unverified)


def test_manifest_cli_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "system-manifest.json"
    create_code = run_cli(
        [
            "create",
            "--repository",
            "example/system",
            "--commit-sha",
            "c" * 40,
            "--artifact-sha256",
            "d" * 64,
            "--provider",
            "example-provider",
            "--model",
            "example-model-v1",
            "--runner",
            "sandbox-runner-v2",
            "--output",
            str(output),
        ]
    )
    assert create_code == 0
    capsys.readouterr()

    validate_code = run_cli(["validate", str(output)])
    assert validate_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True
    assert payload["model"] == "example-model-v1"
