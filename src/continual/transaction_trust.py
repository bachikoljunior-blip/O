from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from agi.provenance import canonical_json_bytes, verify_ed25519
from agi.regression import RegressionPolicy, compare_snapshots


TRUSTED_PLAN_PATH = "candidate-validation.json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_SIGNATURE_RE = re.compile(r"^[0-9a-f]{128}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")


class TransactionTrustError(ValueError):
    """Raised when a validation trust root or signed result is invalid."""


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def safe_relative_parts(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value.strip():
        raise TransactionTrustError("path must be a non-empty string")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise TransactionTrustError(f"unsafe repository path: {value}")
    return tuple(path.parts)


def _prefix(value: str) -> tuple[str, ...]:
    stripped = value[:-1] if value.endswith("/") else value
    return safe_relative_parts(stripped)


@dataclass(frozen=True)
class TrustedCommand:
    argv: tuple[str, ...]
    timeout_seconds: int = 300

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TrustedCommand":
        unknown = set(value) - {"argv", "timeout_seconds"}
        if unknown:
            raise TransactionTrustError(
                "unknown command fields: " + ", ".join(sorted(unknown))
            )
        raw_argv = value.get("argv")
        if not isinstance(raw_argv, list) or not raw_argv or not all(
            isinstance(item, str) and item for item in raw_argv
        ):
            raise TransactionTrustError("command argv must be a non-empty string array")
        command = cls(
            argv=tuple(raw_argv),
            timeout_seconds=int(value.get("timeout_seconds", 300)),
        )
        if not 1 <= command.timeout_seconds <= 900:
            raise TransactionTrustError("command timeout must be between 1 and 900")
        return command

    def to_dict(self) -> dict[str, Any]:
        return {"argv": list(self.argv), "timeout_seconds": self.timeout_seconds}


@dataclass(frozen=True)
class PathPolicy:
    allowed_prefixes: tuple[tuple[str, ...], ...]
    blocked_prefixes: tuple[tuple[str, ...], ...]
    maximum_files: int = 20
    maximum_total_utf8_bytes: int = 1_000_000

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PathPolicy":
        unknown = set(value) - {
            "allowed_prefixes",
            "blocked_prefixes",
            "maximum_files",
            "maximum_total_utf8_bytes",
        }
        if unknown:
            raise TransactionTrustError(
                "unknown path policy fields: " + ", ".join(sorted(unknown))
            )
        allowed = value.get("allowed_prefixes")
        blocked = value.get("blocked_prefixes")
        if not isinstance(allowed, list) or not allowed or not all(
            isinstance(item, str) for item in allowed
        ):
            raise TransactionTrustError("allowed_prefixes must be a non-empty string array")
        if not isinstance(blocked, list) or not all(
            isinstance(item, str) for item in blocked
        ):
            raise TransactionTrustError("blocked_prefixes must be a string array")
        policy = cls(
            allowed_prefixes=tuple(_prefix(item) for item in allowed),
            blocked_prefixes=tuple(_prefix(item) for item in blocked),
            maximum_files=int(value.get("maximum_files", 20)),
            maximum_total_utf8_bytes=int(
                value.get("maximum_total_utf8_bytes", 1_000_000)
            ),
        )
        if not 1 <= policy.maximum_files <= 200:
            raise TransactionTrustError("maximum_files must be between 1 and 200")
        if not 1 <= policy.maximum_total_utf8_bytes <= 20_000_000:
            raise TransactionTrustError(
                "maximum_total_utf8_bytes must be between 1 and 20000000"
            )
        return policy

    def allows(self, relative_path: str) -> bool:
        parts = safe_relative_parts(relative_path)
        if any(parts[: len(prefix)] == prefix for prefix in self.blocked_prefixes):
            return False
        return any(parts[: len(prefix)] == prefix for prefix in self.allowed_prefixes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_prefixes": ["/".join(parts) for parts in self.allowed_prefixes],
            "blocked_prefixes": ["/".join(parts) for parts in self.blocked_prefixes],
            "maximum_files": self.maximum_files,
            "maximum_total_utf8_bytes": self.maximum_total_utf8_bytes,
        }


@dataclass(frozen=True)
class TrustedValidationPlan:
    plan_id: str
    commands: tuple[TrustedCommand, ...]
    path_policy: PathPolicy
    require_signed_regression: bool
    regression_signers: Mapping[str, str]
    regression_policy: RegressionPolicy
    schema_version: int = 1

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TrustedValidationPlan":
        allowed_fields = {
            "schema_version",
            "plan_id",
            "commands",
            "path_policy",
            "require_signed_regression",
            "regression_signers",
            "regression_policy",
        }
        unknown = set(value) - allowed_fields
        if unknown:
            raise TransactionTrustError(
                "unknown trusted plan fields: " + ", ".join(sorted(unknown))
            )
        commands_value = value.get("commands")
        if not isinstance(commands_value, list) or not commands_value or not all(
            isinstance(item, Mapping) for item in commands_value
        ):
            raise TransactionTrustError("commands must be a non-empty object array")
        path_policy_value = value.get("path_policy")
        if not isinstance(path_policy_value, Mapping):
            raise TransactionTrustError("path_policy must be an object")
        signers_value = value.get("regression_signers", {})
        if not isinstance(signers_value, Mapping):
            raise TransactionTrustError("regression_signers must be an object")
        signers: dict[str, str] = {}
        for raw_id, raw_key in signers_value.items():
            signer_id = str(raw_id)
            public_key = str(raw_key).lower()
            if not _SAFE_ID_RE.fullmatch(signer_id):
                raise TransactionTrustError(f"unsafe regression signer id: {signer_id}")
            if not _KEY_RE.fullmatch(public_key):
                raise TransactionTrustError(
                    f"regression signer {signer_id} must have a 32-byte Ed25519 key"
                )
            if public_key in signers.values():
                raise TransactionTrustError(
                    "one public key cannot represent multiple regression signers"
                )
            signers[signer_id] = public_key

        policy_value = value.get("regression_policy", {})
        if not isinstance(policy_value, Mapping):
            raise TransactionTrustError("regression_policy must be an object")
        policy_fields = {
            "maximum_protected_score_drop",
            "allowed_new_failures",
            "minimum_mean_target_gain",
            "minimum_target_repeats",
            "require_same_artifact_provenance",
        }
        unknown_policy = set(policy_value) - policy_fields
        if unknown_policy:
            raise TransactionTrustError(
                "unknown regression policy fields: "
                + ", ".join(sorted(unknown_policy))
            )
        regression_policy = RegressionPolicy(
            maximum_protected_score_drop=float(
                policy_value.get("maximum_protected_score_drop", 0.0)
            ),
            allowed_new_failures=int(policy_value.get("allowed_new_failures", 0)),
            minimum_mean_target_gain=float(
                policy_value.get("minimum_mean_target_gain", 0.01)
            ),
            minimum_target_repeats=int(
                policy_value.get("minimum_target_repeats", 2)
            ),
            require_same_artifact_provenance=bool(
                policy_value.get("require_same_artifact_provenance", False)
            ),
        )
        regression_policy.validate()
        require_signed = value.get("require_signed_regression", True)
        if not isinstance(require_signed, bool):
            raise TransactionTrustError("require_signed_regression must be boolean")

        plan = cls(
            plan_id=str(value.get("plan_id", "")),
            commands=tuple(
                TrustedCommand.from_mapping(item) for item in commands_value
            ),
            path_policy=PathPolicy.from_mapping(path_policy_value),
            require_signed_regression=require_signed,
            regression_signers=signers,
            regression_policy=regression_policy,
            schema_version=int(value.get("schema_version", 1)),
        )
        plan.validate()
        return plan

    def validate(self) -> None:
        if self.schema_version != 1:
            raise TransactionTrustError("unsupported trusted plan schema")
        if not _SAFE_ID_RE.fullmatch(self.plan_id):
            raise TransactionTrustError("trusted plan_id is unsafe")
        if self.require_signed_regression and not self.regression_signers:
            # This is a safe disabled state. It intentionally prevents all
            # Candidate promotion until an owner registers an evaluator key.
            return

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "commands": [command.to_dict() for command in self.commands],
            "path_policy": self.path_policy.to_dict(),
            "require_signed_regression": self.require_signed_regression,
            "regression_signers": dict(self.regression_signers),
            "regression_policy": asdict(self.regression_policy),
        }

    @property
    def sha256(self) -> str:
        return sha256_json(self.to_dict())


def load_trusted_plan(
    repository: Path,
    base_commit: str,
) -> TrustedValidationPlan:
    if not _COMMIT_RE.fullmatch(base_commit):
        raise TransactionTrustError("base_commit must be a 40-character commit SHA")
    completed = subprocess.run(
        ["git", "show", f"{base_commit}:{TRUSTED_PLAN_PATH}"],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise TransactionTrustError(
            f"trusted plan is missing from base commit: {TRUSTED_PLAN_PATH}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise TransactionTrustError("trusted plan is not valid JSON") from error
    if not isinstance(value, Mapping):
        raise TransactionTrustError("trusted plan must contain one JSON object")
    return TrustedValidationPlan.from_mapping(value)


_REGRESSION_PAYLOAD_FIELDS = {
    "schema_version",
    "candidate_id",
    "base_commit",
    "manifest_sha256",
    "target_task_ids",
    "baseline_measurements",
    "candidate_measurements",
    "signer_id",
    "evaluated_at",
}
_REGRESSION_ALL_FIELDS = _REGRESSION_PAYLOAD_FIELDS | {"signature_hex"}


@dataclass(frozen=True)
class SignedRegressionBundle:
    schema_version: int
    candidate_id: str
    base_commit: str
    manifest_sha256: str
    target_task_ids: tuple[str, ...]
    baseline_measurements: tuple[Mapping[str, Any], ...]
    candidate_measurements: tuple[Mapping[str, Any], ...]
    signer_id: str
    evaluated_at: str
    signature_hex: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SignedRegressionBundle":
        unknown = set(value) - _REGRESSION_ALL_FIELDS
        if unknown:
            raise TransactionTrustError(
                "unknown regression bundle fields: " + ", ".join(sorted(unknown))
            )
        missing = _REGRESSION_ALL_FIELDS - set(value)
        if missing:
            raise TransactionTrustError(
                "missing regression bundle fields: " + ", ".join(sorted(missing))
            )
        raw_targets = value.get("target_task_ids")
        raw_baseline = value.get("baseline_measurements")
        raw_candidate = value.get("candidate_measurements")
        if not isinstance(raw_targets, list) or not raw_targets or not all(
            isinstance(item, str) and item for item in raw_targets
        ):
            raise TransactionTrustError(
                "target_task_ids must be a non-empty string array"
            )
        if not isinstance(raw_baseline, list) or not all(
            isinstance(item, Mapping) for item in raw_baseline
        ):
            raise TransactionTrustError("baseline_measurements must be an object array")
        if not isinstance(raw_candidate, list) or not all(
            isinstance(item, Mapping) for item in raw_candidate
        ):
            raise TransactionTrustError("candidate_measurements must be an object array")
        bundle = cls(
            schema_version=int(value["schema_version"]),
            candidate_id=str(value["candidate_id"]),
            base_commit=str(value["base_commit"]).lower(),
            manifest_sha256=str(value["manifest_sha256"]).lower(),
            target_task_ids=tuple(raw_targets),
            baseline_measurements=tuple(dict(item) for item in raw_baseline),
            candidate_measurements=tuple(dict(item) for item in raw_candidate),
            signer_id=str(value["signer_id"]),
            evaluated_at=str(value["evaluated_at"]),
            signature_hex=str(value["signature_hex"]).lower(),
        )
        bundle.validate_shape()
        return bundle

    def validate_shape(self) -> None:
        if self.schema_version != 1:
            raise TransactionTrustError("unsupported regression bundle schema")
        if not _SAFE_ID_RE.fullmatch(self.candidate_id):
            raise TransactionTrustError("regression candidate_id is unsafe")
        if not _COMMIT_RE.fullmatch(self.base_commit):
            raise TransactionTrustError("regression base_commit is invalid")
        if not _SHA256_RE.fullmatch(self.manifest_sha256):
            raise TransactionTrustError("regression manifest_sha256 is invalid")
        if not _SAFE_ID_RE.fullmatch(self.signer_id):
            raise TransactionTrustError("regression signer_id is unsafe")
        if not _SIGNATURE_RE.fullmatch(self.signature_hex):
            raise TransactionTrustError("regression signature must encode 64 bytes")
        if len(set(self.target_task_ids)) != len(self.target_task_ids):
            raise TransactionTrustError("regression target_task_ids contain duplicates")
        try:
            parsed = __import__("datetime").datetime.fromisoformat(
                self.evaluated_at.replace("Z", "+00:00")
            )
        except ValueError as error:
            raise TransactionTrustError("regression evaluated_at is invalid") from error
        if parsed.tzinfo is None:
            raise TransactionTrustError("regression evaluated_at must include an offset")

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "base_commit": self.base_commit,
            "manifest_sha256": self.manifest_sha256,
            "target_task_ids": list(self.target_task_ids),
            "baseline_measurements": [dict(item) for item in self.baseline_measurements],
            "candidate_measurements": [dict(item) for item in self.candidate_measurements],
            "signer_id": self.signer_id,
            "evaluated_at": self.evaluated_at,
        }

    @property
    def sha256(self) -> str:
        return sha256_json(
            {"payload": self.payload(), "signature_hex": self.signature_hex}
        )


def verify_regression_bundle(
    bundle: SignedRegressionBundle,
    *,
    plan: TrustedValidationPlan,
    candidate_id: str,
    base_commit: str,
    manifest_sha256: str,
    target_task_ids: Sequence[str],
) -> dict[str, Any]:
    expected_targets = tuple(sorted(set(target_task_ids)))
    if not expected_targets:
        raise TransactionTrustError("Candidate must identify target_task_ids")
    if bundle.candidate_id != candidate_id:
        raise TransactionTrustError("regression bundle candidate_id mismatch")
    if bundle.base_commit != base_commit:
        raise TransactionTrustError("regression bundle base_commit mismatch")
    if bundle.manifest_sha256 != manifest_sha256:
        raise TransactionTrustError("regression bundle manifest_sha256 mismatch")
    if tuple(sorted(bundle.target_task_ids)) != expected_targets:
        raise TransactionTrustError("regression bundle target_task_ids mismatch")

    public_key_hex = plan.regression_signers.get(bundle.signer_id)
    if public_key_hex is None:
        raise TransactionTrustError("regression bundle signer is not trusted by base commit")
    if not verify_ed25519(
        bytes.fromhex(public_key_hex),
        canonical_json_bytes(bundle.payload()),
        bytes.fromhex(bundle.signature_hex),
    ):
        raise TransactionTrustError("invalid regression bundle Ed25519 signature")

    result = compare_snapshots(
        list(bundle.baseline_measurements),
        list(bundle.candidate_measurements),
        target_task_ids=list(expected_targets),
        policy=plan.regression_policy,
        candidate_id=candidate_id,
    )
    if result.get("adopt_candidate") is not True:
        raise TransactionTrustError("signed regression evidence does not approve Candidate")
    return result
