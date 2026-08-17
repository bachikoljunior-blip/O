from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


MANIFEST_KIND = "public-runtime-system-manifest-v1"
FORBIDDEN_SECRET_KEYS = frozenset(
    {
        "api_key",
        "bridge_secret",
        "credentials",
        "password",
        "private_key",
        "private_key_hex",
        "secret",
        "signing_key",
        "signing_key_hex",
        "token",
    }
)


class ExternalSystemManifestError(ValueError):
    pass


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _is_lower_hex(value: str, length: int) -> bool:
    if len(value) != length or value.lower() != value:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True


def _forbidden_paths(value: Any, prefix: str = "$") -> list[str]:
    paths: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key)
            child = f"{prefix}.{name}"
            if name.lower() in FORBIDDEN_SECRET_KEYS:
                paths.append(child)
            paths.extend(_forbidden_paths(item, child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            paths.extend(_forbidden_paths(item, f"{prefix}[{index}]"))
    return paths


def _nonempty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExternalSystemManifestError(f"{field} must be a non-empty string")
    text = value.strip()
    if len(text) > 256:
        raise ExternalSystemManifestError(f"{field} is too long")
    return text


def build_public_system_manifest(
    *,
    repository: str,
    commit_sha: str,
    artifact_sha256: str,
    provider: str,
    model: str,
    runner: str,
) -> dict[str, Any]:
    """Build the public runtime identity that an independent evaluator is asked to execute.

    This record intentionally contains no credentials and does not claim that the declared runtime was
    actually used. Its digest is suitable for binding into the strict external evaluation request so a
    signed result cannot later be reassigned to another provider/model/runner configuration that shares
    the same source commit.
    """

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "manifest_kind": MANIFEST_KIND,
        "source": {
            "repository": repository,
            "commit_sha": commit_sha,
            "artifact_sha256": artifact_sha256,
        },
        "runtime": {
            "provider": provider,
            "model": model,
            "runner": runner,
        },
        "identity_status": "declared-for-independent-verification",
        "claim_boundary": (
            "This public manifest identifies the runtime configuration requested for independent "
            "evaluation. It is not proof that the runtime was used, is independent, or is AGI."
        ),
    }
    validate_public_system_manifest(manifest, require_digest=False)
    manifest["manifest_sha256"] = _digest(manifest)
    return manifest


def validate_public_system_manifest(
    manifest: Mapping[str, Any],
    *,
    require_digest: bool = True,
) -> dict[str, Any]:
    if manifest.get("schema_version") != 1 or manifest.get("manifest_kind") != MANIFEST_KIND:
        raise ExternalSystemManifestError("unsupported public runtime system manifest schema/kind")
    forbidden = _forbidden_paths(manifest)
    if forbidden:
        raise ExternalSystemManifestError(
            "system manifest embeds forbidden secret fields: " + ", ".join(forbidden)
        )

    source = manifest.get("source")
    if not isinstance(source, Mapping):
        raise ExternalSystemManifestError("source must be an object")
    repository = _nonempty_text(source.get("repository"), "source.repository")
    if "/" not in repository or repository.startswith("/") or repository.endswith("/"):
        raise ExternalSystemManifestError("source.repository must be owner/name")
    commit_sha = _nonempty_text(source.get("commit_sha"), "source.commit_sha")
    artifact_sha256 = _nonempty_text(source.get("artifact_sha256"), "source.artifact_sha256")
    if not _is_lower_hex(commit_sha, 40):
        raise ExternalSystemManifestError("source.commit_sha must be canonical lowercase 40-hex")
    if not _is_lower_hex(artifact_sha256, 64):
        raise ExternalSystemManifestError(
            "source.artifact_sha256 must be canonical lowercase SHA-256"
        )

    runtime = manifest.get("runtime")
    if not isinstance(runtime, Mapping):
        raise ExternalSystemManifestError("runtime must be an object")
    provider = _nonempty_text(runtime.get("provider"), "runtime.provider")
    model = _nonempty_text(runtime.get("model"), "runtime.model")
    runner = _nonempty_text(runtime.get("runner"), "runtime.runner")
    if set(runtime) != {"provider", "model", "runner"}:
        raise ExternalSystemManifestError(
            "runtime must contain exactly provider, model, and runner"
        )
    if manifest.get("identity_status") != "declared-for-independent-verification":
        raise ExternalSystemManifestError(
            "identity_status must remain declared-for-independent-verification"
        )
    if not str(manifest.get("claim_boundary", "")).strip():
        raise ExternalSystemManifestError("claim_boundary must be non-empty")

    payload = dict(manifest)
    digest = payload.pop("manifest_sha256", None)
    expected_digest = _digest(payload)
    if require_digest:
        if not isinstance(digest, str) or not _is_lower_hex(digest, 64):
            raise ExternalSystemManifestError("manifest_sha256 is missing or malformed")
        if digest != expected_digest:
            raise ExternalSystemManifestError("manifest_sha256 does not match canonical manifest")

    return {
        "valid": True,
        "manifest_sha256": expected_digest,
        "repository": repository,
        "commit_sha": commit_sha,
        "artifact_sha256": artifact_sha256,
        "provider": provider,
        "model": model,
        "runner": runner,
        "claim_boundary": (
            "Manifest validation verifies only canonical public identity binding; independent execution "
            "and AGI remain unproven."
        ),
    }


def _load_object(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ExternalSystemManifestError(f"{path} must contain a JSON object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agi-external-system-manifest",
        description="Create or validate the public runtime identity bound to strict external AGI evaluation.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--repository", required=True)
    create.add_argument("--commit-sha", required=True)
    create.add_argument("--artifact-sha256", required=True)
    create.add_argument("--provider", required=True)
    create.add_argument("--model", required=True)
    create.add_argument("--runner", required=True)
    create.add_argument("--output", type=Path, required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("manifest", type=Path)
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            manifest = build_public_system_manifest(
                repository=args.repository,
                commit_sha=args.commit_sha,
                artifact_sha256=args.artifact_sha256,
                provider=args.provider,
                model=args.model,
                runner=args.runner,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            result: Mapping[str, Any] = manifest
        else:
            result = validate_public_system_manifest(_load_object(args.manifest))
    except (ExternalSystemManifestError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(dict(result), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
