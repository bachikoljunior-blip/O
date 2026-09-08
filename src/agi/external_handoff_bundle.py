from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .evaluation import CRITERION_KEYS
from .external_evaluation_request import validate_external_evaluation_request
from .external_provenance import (
    ExternalAttestation,
    ExternalChallenge,
    ExternalEvidenceError,
    ExternalVerifier,
    _forbidden_secret_paths,
    finalize_external_attestation,
    sha256_json,
    verify_external_attestation,
)
from .external_source_artifact import validate_git_archive_artifact
from .external_system_manifest import validate_public_system_manifest


BUNDLE_KIND = "strict-external-evaluation-handoff-directory-v1"
CHALLENGE_KIND = "strict-external-evaluation-challenge-v1"
_BUNDLE_FILES = frozenset(
    {
        "artifact.json",
        "system.tar",
        "system-manifest.json",
        "request.json",
        "verifier.json",
        "challenge.json",
        "result.bin",
        "statement.json",
        "signature.hex",
    }
)
_JSON_FILES = (
    "artifact.json",
    "system-manifest.json",
    "request.json",
    "verifier.json",
    "challenge.json",
    "statement.json",
)
_MAX_JSON_BYTES = 1_000_000
_MAX_SIGNATURE_BYTES = 1_024
_MAX_BINARY_BYTES = 256 * 1024 * 1024


class ExternalHandoffBundleError(ValueError):
    pass


def _reject_constant(value: str) -> None:
    raise ExternalHandoffBundleError(f"non-finite JSON constant is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExternalHandoffBundleError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _read_limited(path: Path, limit: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ExternalHandoffBundleError(f"cannot safely open {path.name}: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ExternalHandoffBundleError(
                f"bundle member must remain a regular file while reading: {path.name}"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            value = handle.read(limit + 1)
    finally:
        os.close(descriptor)
    if len(value) > limit:
        raise ExternalHandoffBundleError(f"{path.name} exceeds the {limit}-byte limit")
    return value


def _read_json(path: Path, *, reject_secret_fields: bool = True) -> Mapping[str, Any]:
    raw = _read_limited(path, _MAX_JSON_BYTES)
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExternalHandoffBundleError(f"{path.name} is not strict UTF-8 JSON: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ExternalHandoffBundleError(f"{path.name} must contain a JSON object")
    if not all(isinstance(key, str) for key in value):
        raise ExternalHandoffBundleError(f"{path.name} field names must be strings")
    forbidden = _forbidden_secret_paths(value) if reject_secret_fields else ()
    if forbidden:
        raise ExternalHandoffBundleError(
            f"{path.name} contains forbidden secret fields: " + ", ".join(forbidden)
        )
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    unknown = sorted(set(value) - expected)
    missing = sorted(expected - set(value))
    if unknown:
        raise ExternalHandoffBundleError(f"{label} has unknown fields: " + ", ".join(unknown))
    if missing:
        raise ExternalHandoffBundleError(f"{label} is missing fields: " + ", ".join(missing))


def _require_strings(value: Mapping[str, Any], fields: set[str], label: str) -> None:
    invalid = sorted(field for field in fields if not isinstance(value.get(field), str))
    if invalid:
        raise ExternalHandoffBundleError(
            f"{label} string fields have invalid types: " + ", ".join(invalid)
        )


def _bundle_paths(bundle: Path) -> dict[str, Path]:
    if bundle.is_symlink():
        raise ExternalHandoffBundleError("bundle directory must not be a symlink")
    try:
        root = bundle.resolve(strict=True)
    except OSError as exc:
        raise ExternalHandoffBundleError(f"bundle directory is unavailable: {exc}") from exc
    if not root.is_dir():
        raise ExternalHandoffBundleError("bundle path must be a directory")
    entries = {entry.name: entry for entry in root.iterdir()}
    unknown = sorted(set(entries) - _BUNDLE_FILES)
    missing = sorted(_BUNDLE_FILES - set(entries))
    if unknown:
        raise ExternalHandoffBundleError("bundle has unknown members: " + ", ".join(unknown))
    if missing:
        raise ExternalHandoffBundleError("bundle is missing members: " + ", ".join(missing))
    for name, path in entries.items():
        if path.is_symlink():
            raise ExternalHandoffBundleError(f"bundle member must not be a symlink: {name}")
        if not path.is_file():
            raise ExternalHandoffBundleError(f"bundle member must be a regular file: {name}")
    return entries


def _validate_artifact(value: Mapping[str, Any], archive: bytes) -> dict[str, Any]:
    fields = {
        "schema_version",
        "artifact_kind",
        "commit_sha",
        "tree_sha",
        "artifact_sha256",
        "size_bytes",
        "working_tree_used",
        "untracked_files_included",
        "claim_boundary",
    }
    _require_exact_keys(value, fields, "artifact.json")
    _require_strings(
        value,
        {"artifact_kind", "commit_sha", "tree_sha", "artifact_sha256", "claim_boundary"},
        "artifact.json",
    )
    if type(value["schema_version"]) is not int or type(value["size_bytes"]) is not int:
        raise ExternalHandoffBundleError(
            "artifact.json schema_version and size_bytes must be JSON integers"
        )
    return validate_git_archive_artifact(value, archive)


def _validate_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    _require_exact_keys(
        value,
        {
            "schema_version",
            "manifest_kind",
            "source",
            "runtime",
            "identity_status",
            "claim_boundary",
            "manifest_sha256",
        },
        "system-manifest.json",
    )
    if type(value["schema_version"]) is not int:
        raise ExternalHandoffBundleError("system-manifest.json schema_version must be a JSON integer")
    source = value.get("source")
    if not isinstance(source, Mapping):
        raise ExternalHandoffBundleError("system-manifest.json source must be an object")
    _require_exact_keys(source, {"repository", "commit_sha", "artifact_sha256"}, "manifest source")
    _require_strings(source, set(source), "manifest source")
    return validate_public_system_manifest(value)


def _validate_request(value: Mapping[str, Any]) -> dict[str, Any]:
    _require_exact_keys(
        value,
        {
            "schema_version",
            "request_kind",
            "subject",
            "required_criteria",
            "core_evaluation_policy",
            "external_quorum",
            "provenance_protocol",
            "handoff",
            "claim_boundary",
            "request_sha256",
        },
        "request.json",
    )
    if type(value["schema_version"]) is not int:
        raise ExternalHandoffBundleError("request.json schema_version must be a JSON integer")
    subject = value.get("subject")
    if not isinstance(subject, Mapping):
        raise ExternalHandoffBundleError("request.json subject must be an object")
    _require_exact_keys(
        subject,
        {"repository", "commit_sha", "artifact_sha256", "system_manifest_sha256", "producer_id"},
        "request subject",
    )
    _require_strings(subject, set(subject), "request subject")
    return validate_external_evaluation_request(value)


def _validate_verifier(value: Mapping[str, Any]) -> ExternalVerifier:
    required = {
        "verifier_id",
        "independent_group",
        "public_key_hex",
        "allowed_source_kinds",
        "status",
        "valid_from",
        "valid_until",
    }
    _require_exact_keys(value, required, "verifier.json")
    _require_strings(
        value,
        {"verifier_id", "independent_group", "public_key_hex", "status"},
        "verifier.json",
    )
    if value["valid_from"] is not None and not isinstance(value["valid_from"], str):
        raise ExternalHandoffBundleError("verifier.json valid_from must be a string or null")
    if value["valid_until"] is not None and not isinstance(value["valid_until"], str):
        raise ExternalHandoffBundleError("verifier.json valid_until must be a string or null")
    kinds = value["allowed_source_kinds"]
    if not isinstance(kinds, list) or not all(isinstance(item, str) for item in kinds):
        raise ExternalHandoffBundleError("verifier.json allowed_source_kinds must be a string array")
    verifier = ExternalVerifier.from_mapping(value)
    if verifier.public_key_hex != value["public_key_hex"]:
        raise ExternalHandoffBundleError("verifier public_key_hex must already be canonical lowercase")
    return verifier


def _validate_challenge(
    value: Mapping[str, Any],
) -> tuple[ExternalChallenge, dict[str, Any]]:
    _require_exact_keys(
        value,
        {
            "schema_version",
            "challenge_kind",
            "request_sha256",
            "criterion",
            "subject",
            "nonce",
            "suite_id",
            "suite_sha256",
            "issued_at",
            "expires_at",
        },
        "challenge.json",
    )
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ExternalHandoffBundleError("challenge.json schema_version must be JSON integer 1")
    if value["challenge_kind"] != CHALLENGE_KIND:
        raise ExternalHandoffBundleError("challenge.json has an unsupported challenge_kind")
    _require_strings(
        value,
        {
            "challenge_kind",
            "request_sha256",
            "criterion",
            "nonce",
            "suite_id",
            "suite_sha256",
            "issued_at",
            "expires_at",
        },
        "challenge.json",
    )
    subject = value.get("subject")
    if not isinstance(subject, Mapping):
        raise ExternalHandoffBundleError("challenge.json subject must be an object")
    _require_exact_keys(
        subject,
        {"repository", "commit_sha", "artifact_sha256", "system_manifest_sha256", "producer_id"},
        "challenge subject",
    )
    _require_strings(subject, set(subject), "challenge subject")
    challenge_fields = {
        field: value[field]
        for field in ("nonce", "suite_id", "suite_sha256", "issued_at", "expires_at")
    }
    challenge = ExternalChallenge.from_mapping(challenge_fields)
    if asdict(challenge) != challenge_fields:
        raise ExternalHandoffBundleError("challenge.json must already use canonical field values")
    return challenge, {
        "request_sha256": value["request_sha256"],
        "criterion": value["criterion"],
        "subject": dict(subject),
    }


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def validate_external_handoff_bundle(
    bundle: Path,
    *,
    expected_verifier_id: str,
    expected_public_key_hex: str,
    expected_criterion: str,
) -> tuple[dict[str, Any], ExternalAttestation]:
    """Validate one fixed-layout public handoff without mutating evidence state."""

    if not expected_verifier_id.strip():
        raise ExternalHandoffBundleError("expected_verifier_id must be non-empty")
    if expected_criterion not in CRITERION_KEYS:
        raise ExternalHandoffBundleError("expected_criterion must be one strict gate criterion")
    if (
        len(expected_public_key_hex) != 64
        or expected_public_key_hex.lower() != expected_public_key_hex
    ):
        raise ExternalHandoffBundleError(
            "expected_public_key_hex must be canonical lowercase 32-byte hex"
        )
    try:
        bytes.fromhex(expected_public_key_hex)
    except ValueError as exc:
        raise ExternalHandoffBundleError(
            "expected_public_key_hex must be canonical lowercase 32-byte hex"
        ) from exc

    paths = _bundle_paths(bundle)
    values = {
        name: _read_json(
            paths[name],
            # The repository-authored request deliberately names the public policy flag
            # bridge_secret_out_of_band_required. Its dedicated strict validator permits that
            # requirement while rejecting embedded bridge_secret/private-key values.
            reject_secret_fields=name != "request.json",
        )
        for name in _JSON_FILES
    }
    request_forbidden = tuple(
        path
        for path in _forbidden_secret_paths(values["request.json"])
        if path != "$.provenance_protocol.bridge_secret_out_of_band_required"
    )
    if request_forbidden:
        raise ExternalHandoffBundleError(
            "request.json contains forbidden secret fields: " + ", ".join(request_forbidden)
        )
    archive = _read_limited(paths["system.tar"], _MAX_BINARY_BYTES)
    result_bytes = _read_limited(paths["result.bin"], _MAX_BINARY_BYTES)
    if not archive:
        raise ExternalHandoffBundleError("system.tar must not be empty")
    if not result_bytes:
        raise ExternalHandoffBundleError("result.bin must not be empty")
    signature = _read_limited(paths["signature.hex"], _MAX_SIGNATURE_BYTES)
    try:
        signature_hex = signature.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ExternalHandoffBundleError("signature.hex must contain ASCII lowercase hex") from exc
    if signature not in {signature_hex.encode("ascii"), (signature_hex + "\n").encode("ascii")}:
        raise ExternalHandoffBundleError("signature.hex must contain only canonical hex and one optional newline")

    artifact = _validate_artifact(values["artifact.json"], archive)
    manifest = _validate_manifest(values["system-manifest.json"])
    request = _validate_request(values["request.json"])
    verifier = _validate_verifier(values["verifier.json"])
    challenge, challenge_binding = _validate_challenge(values["challenge.json"])
    statement = values["statement.json"]

    if verifier.verifier_id != expected_verifier_id:
        raise ExternalHandoffBundleError("verifier identity does not match expected_verifier_id")
    if verifier.public_key_hex != expected_public_key_hex:
        raise ExternalHandoffBundleError("verifier public key does not match expected_public_key_hex")
    if statement.get("verifier_id") != expected_verifier_id:
        raise ExternalHandoffBundleError("statement verifier_id does not match expected verifier")
    if statement.get("criterion") != expected_criterion:
        raise ExternalHandoffBundleError("statement criterion does not match expected_criterion")

    if challenge_binding["request_sha256"] != request["request_sha256"]:
        raise ExternalHandoffBundleError("challenge request digest does not match request")
    if challenge_binding["criterion"] != expected_criterion:
        raise ExternalHandoffBundleError("challenge criterion does not match expected_criterion")
    if challenge_binding["subject"] != dict(values["request.json"]["subject"]):
        raise ExternalHandoffBundleError("challenge subject does not match request subject")

    subject = values["request.json"]["subject"]
    assert isinstance(subject, Mapping)
    if artifact["commit_sha"] != request["commit_sha"]:
        raise ExternalHandoffBundleError("request commit does not match source artifact")
    if artifact["artifact_sha256"] != request["artifact_sha256"]:
        raise ExternalHandoffBundleError("request artifact digest does not match source artifact")
    if manifest["repository"] != request["repository"]:
        raise ExternalHandoffBundleError("request repository does not match system manifest")
    if manifest["commit_sha"] != request["commit_sha"]:
        raise ExternalHandoffBundleError("request commit does not match system manifest")
    if manifest["artifact_sha256"] != request["artifact_sha256"]:
        raise ExternalHandoffBundleError("request artifact digest does not match system manifest")
    if manifest["manifest_sha256"] != request["system_manifest_sha256"]:
        raise ExternalHandoffBundleError("request digest does not match system manifest")

    if statement.get("artifact_sha256") != request["artifact_sha256"]:
        raise ExternalHandoffBundleError("statement artifact digest does not match request")
    if statement.get("producer") != request["producer_id"]:
        raise ExternalHandoffBundleError("statement producer does not match request")
    metadata = statement.get("metadata")
    if not isinstance(metadata, Mapping):
        raise ExternalHandoffBundleError("statement metadata must be an object")
    if metadata.get("evaluation_request_sha256") != request["request_sha256"]:
        raise ExternalHandoffBundleError("statement request digest does not match request")
    if metadata.get("observed_system_manifest_sha256") != request["system_manifest_sha256"]:
        raise ExternalHandoffBundleError("statement observed manifest does not match request")
    if metadata.get("external_verifier_sha256") != sha256_json(values["verifier.json"]):
        raise ExternalHandoffBundleError(
            "statement signed verifier digest does not match verifier.json"
        )
    if metadata.get("external_challenge_sha256") != sha256_json(values["challenge.json"]):
        raise ExternalHandoffBundleError(
            "statement signed challenge digest does not match challenge.json"
        )
    if statement.get("challenge_nonce") != challenge.nonce:
        raise ExternalHandoffBundleError("statement challenge nonce does not match challenge")
    if statement.get("suite_id") != challenge.suite_id:
        raise ExternalHandoffBundleError("statement suite_id does not match challenge")
    if statement.get("suite_sha256") != challenge.suite_sha256:
        raise ExternalHandoffBundleError("statement suite digest does not match challenge")
    result_sha256 = hashlib.sha256(result_bytes).hexdigest()
    if statement.get("result_sha256") != result_sha256:
        raise ExternalHandoffBundleError("statement result digest does not match result.bin")

    attestation = finalize_external_attestation(
        statement,
        public_key_hex=verifier.public_key_hex,
        signature_hex=signature_hex,
    )
    decision = verify_external_attestation(
        attestation,
        verifier=verifier,
        challenge=challenge,
    )
    if not decision.accepted:
        raise ExternalHandoffBundleError(
            "handoff attestation failed public verification: " + "; ".join(decision.reasons)
        )
    report = {
        "valid": True,
        "bundle_kind": BUNDLE_KIND,
        "attestation_id": attestation.attestation_id(),
        "criterion": attestation.criterion,
        "evaluation_success": attestation.success,
        "verifier_id": verifier.verifier_id,
        "independent_group": verifier.independent_group,
        "request_sha256": request["request_sha256"],
        "challenge_nonce": challenge.nonce,
        "repository": request["repository"],
        "commit_sha": request["commit_sha"],
        "artifact_sha256": request["artifact_sha256"],
        "system_manifest_sha256": request["system_manifest_sha256"],
        "result_sha256": result_sha256,
        "ledger_mutated": False,
        "claim_boundary": (
            "Complete public handoff cross-binding and signature verification passed. This is not "
            "ledger acceptance, evaluator-independence proof, quorum evidence, or an AGI claim."
        ),
    }
    return report, attestation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agi-external-bundle",
        description="Validate a fixed-layout secret-free external evaluator handoff bundle.",
    )
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--expected-verifier-id", required=True)
    parser.add_argument("--expected-public-key-hex", required=True)
    parser.add_argument("--expected-criterion", choices=CRITERION_KEYS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        bundle = args.bundle.resolve(strict=False)
        output_lexical = Path(os.path.abspath(os.fspath(args.output)))
        output_resolved = args.output.resolve(strict=False)
        if output_lexical != output_resolved:
            raise ExternalHandoffBundleError("output path must not traverse a symlink")
        for output in (output_lexical, output_resolved):
            try:
                output.relative_to(bundle)
            except ValueError:
                continue
            raise ExternalHandoffBundleError(
                "output must be outside the validated bundle directory"
            )
        report, attestation = validate_external_handoff_bundle(
            args.bundle,
            expected_verifier_id=args.expected_verifier_id,
            expected_public_key_hex=args.expected_public_key_hex,
            expected_criterion=args.expected_criterion,
        )
        _atomic_write_json(output_resolved, asdict(attestation))
        report = {**report, "output": str(output_resolved)}
    except (
        ExternalHandoffBundleError,
        ExternalEvidenceError,
        OSError,
        ValueError,
    ) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
