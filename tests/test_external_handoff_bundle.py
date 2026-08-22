from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

from agi.external_handoff_bundle import (
    CHALLENGE_KIND,
    ExternalHandoffBundleError,
    build_parser,
    run_cli,
    validate_external_handoff_bundle,
)
from agi.external_provenance import (
    ExternalAttestation,
    ExternalChallenge,
    ExternalVerifier,
    _BASE,
    _L,
    _encode_point,
    _scalar_multiply,
    sha256_json,
)
from agi.external_source_artifact import build_external_handoff_from_git


def _sign(seed: bytes, message: bytes) -> tuple[str, str]:
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    prefix = digest[32:]
    public_key = _encode_point(_scalar_multiply(_BASE, scalar))
    nonce = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % _L
    encoded_nonce = _encode_point(_scalar_multiply(_BASE, nonce))
    challenge = int.from_bytes(
        hashlib.sha512(encoded_nonce + public_key + message).digest(), "little"
    ) % _L
    response = (nonce + challenge * scalar) % _L
    return public_key.hex(), (encoded_nonce + response.to_bytes(32, "little")).hex()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    subprocess.check_call(["git", "-C", str(root), "init", "-q"])
    subprocess.check_call(["git", "-C", str(root), "config", "user.name", "O test"])
    subprocess.check_call(
        ["git", "-C", str(root), "config", "user.email", "o-test@example.invalid"]
    )
    (root / "system.txt").write_text("exact system\n", encoding="utf-8")
    subprocess.check_call(["git", "-C", str(root), "add", "system.txt"])
    subprocess.check_call(
        ["git", "-C", str(root), "-c", "commit.gpgsign=false", "commit", "-q", "-m", "system"]
    )
    commit = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    return root, commit


def _bundle(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root, commit = _repo(tmp_path)
    artifact, manifest, request, archive = build_external_handoff_from_git(
        root=root,
        repository="example/system",
        commit_sha=commit,
        producer_id="system-under-test",
        provider="external-provider",
        model="candidate-v1",
        runner="production-runner-v1",
    )
    challenge = ExternalChallenge(
        nonce="11" * 32,
        suite_id="independent-hidden-suite",
        suite_sha256="22" * 32,
        issued_at="2026-08-23T00:00:00+00:00",
        expires_at="2026-08-23T01:00:00+00:00",
    )
    public_key, _ = _sign(b"V" * 32, b"")
    verifier = ExternalVerifier(
        verifier_id="independent-lab-key-1",
        independent_group="independent-lab",
        public_key_hex=public_key,
        allowed_source_kinds=("independent_reproduction",),
        status="active",
        valid_from="2026-08-22T00:00:00+00:00",
        valid_until="2026-08-24T00:00:00+00:00",
    )
    verifier_mapping = asdict(verifier)
    challenge_mapping = {
        "schema_version": 1,
        "challenge_kind": CHALLENGE_KIND,
        "request_sha256": request["request_sha256"],
        "criterion": "robustness",
        "subject": dict(request["subject"]),
        **asdict(challenge),
    }
    result = b'{"passed":true,"failures":[]}\n'
    statement = ExternalAttestation(
        schema_version=1,
        criterion="robustness",
        success=True,
        source_kind="independent_reproduction",
        task_id="private-production-suite-run",
        domain="software-operations",
        run_id="external-run-1",
        producer="system-under-test",
        verifier_id=verifier.verifier_id,
        suite_id=challenge.suite_id,
        suite_version="1.0",
        suite_sha256=challenge.suite_sha256,
        artifact_sha256=artifact["artifact_sha256"],
        result_sha256=hashlib.sha256(result).hexdigest(),
        challenge_nonce=challenge.nonce,
        evaluated_at="2026-08-23T00:30:00+00:00",
        repeat_index=0,
        metadata={
            "evaluation_request_sha256": request["request_sha256"],
            "observed_system_manifest_sha256": manifest["manifest_sha256"],
            "external_verifier_sha256": sha256_json(verifier_mapping),
            "external_challenge_sha256": sha256_json(challenge_mapping),
        },
        signature_hex="00" * 64,
    )
    _, signature = _sign(b"V" * 32, statement.payload_bytes())
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_json(bundle / "artifact.json", artifact)
    (bundle / "system.tar").write_bytes(archive)
    _write_json(bundle / "system-manifest.json", manifest)
    _write_json(bundle / "request.json", request)
    _write_json(bundle / "verifier.json", verifier_mapping)
    _write_json(bundle / "challenge.json", challenge_mapping)
    (bundle / "result.bin").write_bytes(result)
    unsigned = asdict(statement)
    unsigned.pop("signature_hex")
    _write_json(bundle / "statement.json", unsigned)
    (bundle / "signature.hex").write_text(signature + "\n", encoding="ascii")
    return bundle, {
        "verifier_id": verifier.verifier_id,
        "public_key": public_key,
        "criterion": statement.criterion,
        "request_sha256": request["request_sha256"],
        "manifest_sha256": manifest["manifest_sha256"],
        "artifact_sha256": artifact["artifact_sha256"],
    }


def _validate(bundle: Path, expected: dict[str, str]):
    return validate_external_handoff_bundle(
        bundle,
        expected_verifier_id=expected["verifier_id"],
        expected_public_key_hex=expected["public_key"],
        expected_criterion=expected["criterion"],
    )


def _resign_statement(bundle: Path) -> None:
    statement = json.loads((bundle / "statement.json").read_text(encoding="utf-8"))
    placeholder = ExternalAttestation.from_mapping(
        {**statement, "signature_hex": "00" * 64}
    )
    _, signature = _sign(b"V" * 32, placeholder.payload_bytes())
    (bundle / "signature.hex").write_text(signature + "\n", encoding="ascii")


def test_valid_bundle_cross_binds_every_public_handoff_layer(tmp_path: Path) -> None:
    bundle, expected = _bundle(tmp_path)

    report, attestation = _validate(bundle, expected)

    assert report["valid"] is True
    assert report["evaluation_success"] is True
    assert report["ledger_mutated"] is False
    assert report["request_sha256"] == expected["request_sha256"]
    assert report["system_manifest_sha256"] == expected["manifest_sha256"]
    assert report["artifact_sha256"] == expected["artifact_sha256"]
    assert report["attestation_id"] == attestation.attestation_id()
    assert "not" in report["claim_boundary"].lower()


def test_valid_negative_result_is_not_misreported_as_evaluation_success(tmp_path: Path) -> None:
    bundle, expected = _bundle(tmp_path)
    statement_path = bundle / "statement.json"
    statement = json.loads(statement_path.read_text(encoding="utf-8"))
    statement["success"] = False
    _write_json(statement_path, statement)
    _resign_statement(bundle)

    report, attestation = _validate(bundle, expected)

    assert report["valid"] is True
    assert report["evaluation_success"] is False
    assert attestation.success is False


def test_cli_writes_verified_attestation_only_after_full_success(
    tmp_path: Path, capsys
) -> None:
    bundle, expected = _bundle(tmp_path)
    output = tmp_path / "verified-attestation.json"

    code = run_cli(
        [
            str(bundle),
            "--expected-verifier-id",
            expected["verifier_id"],
            "--expected-public-key-hex",
            expected["public_key"],
            "--expected-criterion",
            expected["criterion"],
            "--output",
            str(output),
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["valid"] is True
    assert report["ledger_mutated"] is False
    assert ExternalAttestation.from_mapping(json.loads(output.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    ("file_name", "path", "replacement", "message"),
    [
        ("request.json", ("subject", "commit_sha"), "aa" * 20, "request_sha256"),
        ("system-manifest.json", ("source", "repository"), "other/system", "manifest_sha256"),
        ("system-manifest.json", ("runtime", "model"), "other-model", "manifest_sha256"),
        ("statement.json", ("producer",), "other-system", "producer"),
        (
            "statement.json",
            ("metadata", "evaluation_request_sha256"),
            "bb" * 32,
            "request digest",
        ),
        (
            "statement.json",
            ("metadata", "observed_system_manifest_sha256"),
            "cc" * 32,
            "observed manifest",
        ),
        ("statement.json", ("challenge_nonce",), "dd" * 32, "challenge nonce"),
        ("statement.json", ("suite_id",), "other-suite", "suite_id"),
        ("statement.json", ("suite_sha256",), "ee" * 32, "suite digest"),
        ("statement.json", ("result_sha256",), "ff" * 32, "result digest"),
        ("challenge.json", ("request_sha256",), "12" * 32, "challenge request digest"),
        ("challenge.json", ("criterion",), "adaptability", "challenge criterion"),
        (
            "challenge.json",
            ("subject", "commit_sha"),
            "13" * 20,
            "challenge subject",
        ),
        (
            "challenge.json",
            ("subject", "system_manifest_sha256"),
            "14" * 32,
            "challenge subject",
        ),
        (
            "challenge.json",
            ("issued_at",),
            "2026-08-23T00:10:00+00:00",
            "signed challenge digest",
        ),
        (
            "challenge.json",
            ("expires_at",),
            "2026-08-23T00:50:00+00:00",
            "signed challenge digest",
        ),
        (
            "verifier.json",
            ("independent_group",),
            "tampered-independent-group",
            "signed verifier digest",
        ),
    ],
)
def test_bundle_rejects_cross_binding_mismatch(
    tmp_path: Path,
    file_name: str,
    path: tuple[str, ...],
    replacement: str,
    message: str,
) -> None:
    bundle, expected = _bundle(tmp_path)
    target = bundle / file_name
    value = json.loads(target.read_text(encoding="utf-8"))
    cursor = value
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = replacement
    _write_json(target, value)

    with pytest.raises((ExternalHandoffBundleError, ValueError), match=message):
        _validate(bundle, expected)


def test_bundle_rejects_tampered_result_identity_key_criterion_and_signature(
    tmp_path: Path,
) -> None:
    bundle, expected = _bundle(tmp_path)
    (bundle / "result.bin").write_bytes(b"tampered")
    with pytest.raises(ExternalHandoffBundleError, match="result digest"):
        _validate(bundle, expected)


def test_bundle_rejects_verifier_policy_widening_even_when_source_stays_allowed(
    tmp_path: Path,
) -> None:
    bundle, expected = _bundle(tmp_path)
    verifier_path = bundle / "verifier.json"
    verifier = json.loads(verifier_path.read_text(encoding="utf-8"))
    verifier["allowed_source_kinds"].append("external_test")
    _write_json(verifier_path, verifier)

    with pytest.raises(ExternalHandoffBundleError, match="signed verifier digest"):
        _validate(bundle, expected)

    bundle, expected = _bundle(tmp_path / "identity")
    with pytest.raises(ExternalHandoffBundleError, match="identity"):
        validate_external_handoff_bundle(
            bundle,
            expected_verifier_id="other-lab-key",
            expected_public_key_hex=expected["public_key"],
            expected_criterion=expected["criterion"],
        )
    with pytest.raises(ExternalHandoffBundleError, match="public key"):
        validate_external_handoff_bundle(
            bundle,
            expected_verifier_id=expected["verifier_id"],
            expected_public_key_hex="ab" * 32,
            expected_criterion=expected["criterion"],
        )
    with pytest.raises(ExternalHandoffBundleError, match="criterion"):
        validate_external_handoff_bundle(
            bundle,
            expected_verifier_id=expected["verifier_id"],
            expected_public_key_hex=expected["public_key"],
            expected_criterion="adaptability",
        )
    (bundle / "signature.hex").write_text("00" * 64 + "\n", encoding="ascii")
    with pytest.raises(ValueError, match="signature"):
        _validate(bundle, expected)


@pytest.mark.parametrize("mode", ["missing", "unknown", "member-symlink", "non-file"])
def test_fixed_layout_rejects_ambiguous_or_unsafe_members(tmp_path: Path, mode: str) -> None:
    bundle, expected = _bundle(tmp_path)
    if mode == "missing":
        (bundle / "result.bin").unlink()
    elif mode == "unknown":
        (bundle / "extra.json").write_text("{}", encoding="utf-8")
    elif mode == "member-symlink":
        (bundle / "result.bin").unlink()
        os.symlink(bundle / "system.tar", bundle / "result.bin")
    else:
        (bundle / "result.bin").unlink()
        (bundle / "result.bin").mkdir()

    with pytest.raises(ExternalHandoffBundleError):
        _validate(bundle, expected)


def test_bundle_rejects_directory_symlink_duplicate_nan_unknown_type_and_secret(
    tmp_path: Path,
) -> None:
    bundle, expected = _bundle(tmp_path)
    alias = tmp_path / "bundle-alias"
    os.symlink(bundle, alias)
    with pytest.raises(ExternalHandoffBundleError, match="directory must not be a symlink"):
        _validate(alias, expected)

    bundle, expected = _bundle(tmp_path / "strict-json")
    challenge = (bundle / "challenge.json").read_text(encoding="utf-8")
    (bundle / "challenge.json").write_text(
        challenge.replace('"nonce": "' + "11" * 32 + '"', '"nonce": "' + "11" * 32 + '", "nonce": "' + "11" * 32 + '"'),
        encoding="utf-8",
    )
    with pytest.raises(ExternalHandoffBundleError, match="duplicate JSON field"):
        _validate(bundle, expected)

    bundle, expected = _bundle(tmp_path / "nan")
    (bundle / "statement.json").write_text('{"value":NaN}', encoding="utf-8")
    with pytest.raises(ExternalHandoffBundleError, match="non-finite"):
        _validate(bundle, expected)

    bundle, expected = _bundle(tmp_path / "unknown")
    artifact = json.loads((bundle / "artifact.json").read_text(encoding="utf-8"))
    artifact["extra"] = "ambiguous"
    _write_json(bundle / "artifact.json", artifact)
    with pytest.raises(ExternalHandoffBundleError, match="unknown fields"):
        _validate(bundle, expected)

    bundle, expected = _bundle(tmp_path / "type")
    artifact = json.loads((bundle / "artifact.json").read_text(encoding="utf-8"))
    artifact["schema_version"] = True
    _write_json(bundle / "artifact.json", artifact)
    with pytest.raises(ExternalHandoffBundleError, match="JSON integers"):
        _validate(bundle, expected)

    bundle, expected = _bundle(tmp_path / "secret")
    statement = json.loads((bundle / "statement.json").read_text(encoding="utf-8"))
    statement["metadata"]["privateKeyHex"] = "must-never-enter"
    _write_json(bundle / "statement.json", statement)
    with pytest.raises(ExternalHandoffBundleError, match="forbidden secret"):
        _validate(bundle, expected)

    bundle, expected = _bundle(tmp_path / "request-secret")
    request = json.loads((bundle / "request.json").read_text(encoding="utf-8"))
    request["handoff"]["apiTokenValue"] = "must-never-enter"
    _write_json(bundle / "request.json", request)
    with pytest.raises(ExternalHandoffBundleError, match="forbidden secret"):
        _validate(bundle, expected)


def test_bundle_rejects_oversized_member_before_parsing(tmp_path: Path) -> None:
    bundle, expected = _bundle(tmp_path)
    with (bundle / "artifact.json").open("wb") as handle:
        handle.truncate(1_000_001)

    with pytest.raises(ExternalHandoffBundleError, match="exceeds the 1000000-byte limit"):
        _validate(bundle, expected)


@pytest.mark.parametrize("member", ["system.tar", "result.bin"])
def test_bundle_rejects_empty_binary_artifacts(tmp_path: Path, member: str) -> None:
    bundle, expected = _bundle(tmp_path)
    (bundle / member).write_bytes(b"")

    with pytest.raises(ExternalHandoffBundleError, match=f"{member} must not be empty"):
        _validate(bundle, expected)


def test_cli_failure_preserves_existing_output_and_rejects_output_under_bundle(
    tmp_path: Path, capsys
) -> None:
    bundle, expected = _bundle(tmp_path)
    output = tmp_path / "existing.json"
    output.write_text("preserve me\n", encoding="utf-8")
    (bundle / "signature.hex").write_text("00" * 64 + "\n", encoding="ascii")
    args = [
        str(bundle),
        "--expected-verifier-id",
        expected["verifier_id"],
        "--expected-public-key-hex",
        expected["public_key"],
        "--expected-criterion",
        expected["criterion"],
        "--output",
        str(output),
    ]
    assert run_cli(args) == 2
    assert json.loads(capsys.readouterr().out)["valid"] is False
    assert output.read_text(encoding="utf-8") == "preserve me\n"

    nested_output = bundle / "nested" / "attestation.json"
    args[-1] = str(nested_output)
    assert run_cli(args) == 2
    assert not nested_output.exists()

    outside = tmp_path / "outside.json"
    linked_output = bundle / "linked-output.json"
    os.symlink(outside, linked_output)
    args[-1] = str(linked_output)
    assert run_cli(args) == 2
    assert linked_output.is_symlink()
    assert not outside.exists()

    external_link = tmp_path / "external-link.json"
    os.symlink(outside, external_link)
    args[-1] = str(external_link)
    assert run_cli(args) == 2
    assert external_link.is_symlink()
    assert not outside.exists()


def test_cli_has_no_private_key_or_evidence_ledger_inputs() -> None:
    options = {
        option
        for action in build_parser()._actions
        for option in action.option_strings
    }
    assert not options.intersection(
        {
            "--private-key",
            "--private-key-hex",
            "--signing-key",
            "--ledger",
            "--registry",
            "--bridge-secret",
        }
    )
