from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from .evaluation import CRITERIA_BY_KEY, EvidenceRecord, evaluate_evidence


# Pure-Python strict Ed25519 verification. The implementation follows the
# Edwards25519 group equations from RFC 8032 and intentionally exposes verify
# only: evidence producers keep private-key operations outside this repository.
_Q = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _Q - 2, _Q)) % _Q
_I = pow(2, (_Q - 1) // 4, _Q)
_IDENTITY = (0, 1)
_SHA256_RE = __import__("re").compile(r"^[0-9a-f]{64}$")
_HEX_32_RE = __import__("re").compile(r"^[0-9a-f]{64}$")
_HEX_64_RE = __import__("re").compile(r"^[0-9a-f]{128}$")


class EvidenceValidationError(ValueError):
    """Raised when an evidence object cannot be interpreted safely."""


def _x_recover(y: int) -> int:
    xx = ((y * y - 1) * pow((_D * y * y + 1) % _Q, _Q - 2, _Q)) % _Q
    x = pow(xx, (_Q + 3) // 8, _Q)
    if (x * x - xx) % _Q != 0:
        x = (x * _I) % _Q
    if (x * x - xx) % _Q != 0:
        raise EvidenceValidationError("invalid Edwards25519 point")
    if x & 1:
        x = _Q - x
    return x


_BASE_Y = (4 * pow(5, _Q - 2, _Q)) % _Q
_BASE = (_x_recover(_BASE_Y), _BASE_Y)


def _point_add(left: tuple[int, int], right: tuple[int, int]) -> tuple[int, int]:
    x1, y1 = left
    x2, y2 = right
    product = (_D * x1 * x2 * y1 * y2) % _Q
    x3 = ((x1 * y2 + x2 * y1) * pow((1 + product) % _Q, _Q - 2, _Q)) % _Q
    y3 = ((y1 * y2 + x1 * x2) * pow((1 - product) % _Q, _Q - 2, _Q)) % _Q
    return x3, y3


def _scalar_multiply(point: tuple[int, int], scalar: int) -> tuple[int, int]:
    if scalar < 0:
        raise EvidenceValidationError("negative scalar")
    result = _IDENTITY
    addend = point
    value = scalar
    while value:
        if value & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        value >>= 1
    return result


def _encode_point(point: tuple[int, int]) -> bytes:
    x, y = point
    encoded = bytearray(y.to_bytes(32, "little"))
    encoded[31] |= (x & 1) << 7
    return bytes(encoded)


def _on_curve(point: tuple[int, int]) -> bool:
    x, y = point
    return (y * y - x * x - 1 - _D * x * x * y * y) % _Q == 0


def _decode_point(encoded: bytes) -> tuple[int, int]:
    if len(encoded) != 32:
        raise EvidenceValidationError("Ed25519 point must be 32 bytes")
    sign = encoded[31] >> 7
    y = int.from_bytes(encoded, "little") & ((1 << 255) - 1)
    if y >= _Q:
        raise EvidenceValidationError("non-canonical Edwards25519 y coordinate")
    x = _x_recover(y)
    if (x & 1) != sign:
        x = _Q - x
    point = (x, y)
    if not _on_curve(point) or _encode_point(point) != encoded:
        raise EvidenceValidationError("non-canonical Edwards25519 point")
    return point


def verify_ed25519(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Verify a strict Ed25519 signature without accepting low-order points."""

    try:
        if len(public_key) != 32 or len(signature) != 64:
            return False
        public_point = _decode_point(public_key)
        r_point = _decode_point(signature[:32])
        scalar = int.from_bytes(signature[32:], "little")
        if scalar >= _L:
            return False

        # Restrict both encoded points to the prime-order subgroup and reject
        # the identity. This avoids accepting torsion-point variants.
        if public_point == _IDENTITY or r_point == _IDENTITY:
            return False
        if _scalar_multiply(public_point, _L) != _IDENTITY:
            return False
        if _scalar_multiply(r_point, _L) != _IDENTITY:
            return False

        challenge = int.from_bytes(
            hashlib.sha512(signature[:32] + public_key + message).digest(),
            "little",
        ) % _L
        left = _scalar_multiply(_BASE, scalar)
        right = _point_add(r_point, _scalar_multiply(public_point, challenge))
        return left == right
    except (EvidenceValidationError, ValueError, OverflowError):
        return False


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvidenceValidationError(f"invalid timestamp: {value}") from error
    if parsed.tzinfo is None:
        raise EvidenceValidationError("timestamp must include a UTC offset")
    return parsed.astimezone(UTC)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Verifier:
    verifier_id: str
    independent_group: str
    public_key_hex: str
    allowed_source_kinds: tuple[str, ...] = (
        "external_test",
        "independent_reproduction",
        "human_audit",
    )
    status: str = "active"
    valid_from: str | None = None
    valid_until: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Verifier":
        allowed = value.get("allowed_source_kinds", ())
        if not isinstance(allowed, Sequence) or isinstance(allowed, (str, bytes)):
            raise EvidenceValidationError("allowed_source_kinds must be an array")
        metadata = value.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise EvidenceValidationError("verifier metadata must be an object")
        verifier = cls(
            verifier_id=str(value.get("verifier_id", "")),
            independent_group=str(value.get("independent_group", "")),
            public_key_hex=str(value.get("public_key_hex", "")).lower(),
            allowed_source_kinds=tuple(str(item) for item in allowed),
            status=str(value.get("status", "active")),
            valid_from=(str(value["valid_from"]) if value.get("valid_from") else None),
            valid_until=(str(value["valid_until"]) if value.get("valid_until") else None),
            metadata=dict(metadata),
        )
        verifier.validate()
        return verifier

    def validate(self) -> None:
        if not self.verifier_id or not self.independent_group:
            raise EvidenceValidationError("verifier_id and independent_group are required")
        if not _HEX_32_RE.fullmatch(self.public_key_hex):
            raise EvidenceValidationError("public_key_hex must encode 32 bytes")
        if self.status not in {"active", "suspended", "revoked"}:
            raise EvidenceValidationError(f"invalid verifier status: {self.status}")
        if self.valid_from:
            _parse_time(self.valid_from)
        if self.valid_until:
            _parse_time(self.valid_until)

    def is_active_at(self, evaluated_at: datetime) -> bool:
        if self.status != "active":
            return False
        if self.valid_from and evaluated_at < _parse_time(self.valid_from):
            return False
        if self.valid_until and evaluated_at > _parse_time(self.valid_until):
            return False
        return True


_ATTESTATION_PAYLOAD_FIELDS = (
    "schema_version",
    "criterion",
    "passed",
    "source_kind",
    "task_id",
    "domain",
    "run_id",
    "verifier_id",
    "suite_id",
    "suite_version",
    "suite_sha256",
    "artifact_sha256",
    "result_sha256",
    "challenge_nonce",
    "evaluated_at",
    "repeat_index",
    "metadata",
    "signature_algorithm",
)
_ATTESTATION_ALL_FIELDS = set(_ATTESTATION_PAYLOAD_FIELDS) | {
    "signature_hex",
    "attestation_id",
}


@dataclass(frozen=True)
class Attestation:
    schema_version: int
    criterion: str
    passed: bool
    source_kind: str
    task_id: str
    domain: str
    run_id: str
    verifier_id: str
    suite_id: str
    suite_version: str
    suite_sha256: str
    artifact_sha256: str
    result_sha256: str
    challenge_nonce: str
    evaluated_at: str
    repeat_index: int
    metadata: Mapping[str, Any]
    signature_algorithm: str
    signature_hex: str
    attestation_id: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Attestation":
        unknown = set(value) - _ATTESTATION_ALL_FIELDS
        if unknown:
            raise EvidenceValidationError(
                "unknown attestation fields: " + ", ".join(sorted(unknown))
            )
        missing = [field for field in _ATTESTATION_PAYLOAD_FIELDS if field not in value]
        if missing or "signature_hex" not in value:
            raise EvidenceValidationError(
                "missing attestation fields: "
                + ", ".join(missing + ([] if "signature_hex" in value else ["signature_hex"]))
            )
        metadata = value.get("metadata")
        if not isinstance(metadata, Mapping):
            raise EvidenceValidationError("attestation metadata must be an object")
        if not isinstance(value.get("passed"), bool):
            raise EvidenceValidationError("passed must be a JSON boolean")
        if not isinstance(value.get("schema_version"), int):
            raise EvidenceValidationError("schema_version must be an integer")
        if not isinstance(value.get("repeat_index"), int):
            raise EvidenceValidationError("repeat_index must be an integer")
        attestation = cls(
            schema_version=value["schema_version"],
            criterion=str(value["criterion"]),
            passed=value["passed"],
            source_kind=str(value["source_kind"]),
            task_id=str(value["task_id"]),
            domain=str(value["domain"]),
            run_id=str(value["run_id"]),
            verifier_id=str(value["verifier_id"]),
            suite_id=str(value["suite_id"]),
            suite_version=str(value["suite_version"]),
            suite_sha256=str(value["suite_sha256"]).lower(),
            artifact_sha256=str(value["artifact_sha256"]).lower(),
            result_sha256=str(value["result_sha256"]).lower(),
            challenge_nonce=str(value["challenge_nonce"]).lower(),
            evaluated_at=str(value["evaluated_at"]),
            repeat_index=value["repeat_index"],
            metadata=dict(metadata),
            signature_algorithm=str(value["signature_algorithm"]),
            signature_hex=str(value["signature_hex"]).lower(),
            attestation_id=(
                str(value["attestation_id"]).lower()
                if value.get("attestation_id") is not None
                else None
            ),
        )
        attestation.validate_shape()
        return attestation

    def validate_shape(self) -> None:
        if self.schema_version != 1:
            raise EvidenceValidationError(
                f"unsupported attestation schema_version: {self.schema_version}"
            )
        if self.criterion not in CRITERIA_BY_KEY:
            raise EvidenceValidationError(f"unknown criterion: {self.criterion}")
        required_text = {
            "task_id": self.task_id,
            "domain": self.domain,
            "run_id": self.run_id,
            "verifier_id": self.verifier_id,
            "suite_id": self.suite_id,
            "suite_version": self.suite_version,
        }
        missing = [name for name, text in required_text.items() if not text]
        if missing:
            raise EvidenceValidationError(
                "empty attestation fields: " + ", ".join(sorted(missing))
            )
        for name, digest in (
            ("suite_sha256", self.suite_sha256),
            ("artifact_sha256", self.artifact_sha256),
            ("result_sha256", self.result_sha256),
        ):
            if not _SHA256_RE.fullmatch(digest):
                raise EvidenceValidationError(f"{name} must be lowercase SHA-256 hex")
        if not _HEX_32_RE.fullmatch(self.challenge_nonce):
            raise EvidenceValidationError("challenge_nonce must encode 32 bytes")
        if self.repeat_index < 0:
            raise EvidenceValidationError("repeat_index must be non-negative")
        _parse_time(self.evaluated_at)
        if self.signature_algorithm != "ed25519":
            raise EvidenceValidationError("only ed25519 signatures are accepted")
        if not _HEX_64_RE.fullmatch(self.signature_hex):
            raise EvidenceValidationError("signature_hex must encode 64 bytes")
        if self.attestation_id is not None and not _SHA256_RE.fullmatch(
            self.attestation_id
        ):
            raise EvidenceValidationError("attestation_id must be lowercase SHA-256 hex")

    def payload(self) -> dict[str, Any]:
        value = asdict(self)
        value["metadata"] = dict(self.metadata)
        return {field: value[field] for field in _ATTESTATION_PAYLOAD_FIELDS}

    def payload_bytes(self) -> bytes:
        return canonical_json_bytes(self.payload())

    def computed_id(self) -> str:
        signed_envelope = {
            "payload": self.payload(),
            "signature_hex": self.signature_hex,
        }
        return sha256_json(signed_envelope)


@dataclass(frozen=True)
class Challenge:
    nonce: str
    suite_id: str
    issued_at: str
    expires_at: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Challenge":
        challenge = cls(
            nonce=str(value.get("nonce", "")).lower(),
            suite_id=str(value.get("suite_id", "")),
            issued_at=str(value.get("issued_at", "")),
            expires_at=str(value.get("expires_at", "")),
        )
        if not _HEX_32_RE.fullmatch(challenge.nonce):
            raise EvidenceValidationError("challenge nonce must encode 32 bytes")
        if not challenge.suite_id:
            raise EvidenceValidationError("challenge suite_id is required")
        issued = _parse_time(challenge.issued_at)
        expires = _parse_time(challenge.expires_at)
        if expires <= issued:
            raise EvidenceValidationError("challenge expires_at must follow issued_at")
        return challenge


@dataclass(frozen=True)
class Disclosure:
    task_id: str
    disclosed_at: str
    suite_sha256: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Disclosure":
        disclosure = cls(
            task_id=str(value.get("task_id", "")),
            disclosed_at=str(value.get("disclosed_at", "")),
            suite_sha256=(
                str(value["suite_sha256"]).lower()
                if value.get("suite_sha256") is not None
                else None
            ),
        )
        if not disclosure.task_id:
            raise EvidenceValidationError("disclosure task_id is required")
        _parse_time(disclosure.disclosed_at)
        if disclosure.suite_sha256 is not None and not _SHA256_RE.fullmatch(
            disclosure.suite_sha256
        ):
            raise EvidenceValidationError("disclosure suite_sha256 is invalid")
        return disclosure


def _registry(value: Mapping[str, Any]) -> dict[str, Verifier]:
    if value.get("schema_version") != 1:
        raise EvidenceValidationError("unsupported verifier registry schema")
    raw = value.get("verifiers", [])
    if not isinstance(raw, list):
        raise EvidenceValidationError("verifiers must be an array")
    registry: dict[str, Verifier] = {}
    public_keys: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise EvidenceValidationError("every verifier must be an object")
        verifier = Verifier.from_mapping(item)
        if verifier.verifier_id in registry:
            raise EvidenceValidationError(
                f"duplicate verifier_id: {verifier.verifier_id}"
            )
        if verifier.public_key_hex in public_keys:
            raise EvidenceValidationError("one public key cannot represent two verifiers")
        registry[verifier.verifier_id] = verifier
        public_keys.add(verifier.public_key_hex)
    return registry


def _disclosure_blocks(
    attestation: Attestation,
    disclosures: Sequence[Disclosure],
) -> bool:
    evaluated = _parse_time(attestation.evaluated_at)
    for disclosure in disclosures:
        if disclosure.task_id != attestation.task_id:
            continue
        if disclosure.suite_sha256 and disclosure.suite_sha256 != attestation.suite_sha256:
            continue
        if evaluated >= _parse_time(disclosure.disclosed_at):
            return True
    return False


def audit_ledger(
    registry_document: Mapping[str, Any],
    ledger_document: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify provenance, freshness, contamination, and AGI claim evidence."""

    registry = _registry(registry_document)
    if ledger_document.get("schema_version") != 1:
        raise EvidenceValidationError("unsupported evidence ledger schema")

    raw_challenges = ledger_document.get("challenges", [])
    raw_disclosures = ledger_document.get("task_disclosures", [])
    raw_attestations = ledger_document.get("attestations", [])
    if not all(isinstance(value, list) for value in (
        raw_challenges,
        raw_disclosures,
        raw_attestations,
    )):
        raise EvidenceValidationError(
            "challenges, task_disclosures, and attestations must be arrays"
        )

    challenges: dict[str, Challenge] = {}
    malformed_challenges: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_challenges):
        try:
            if not isinstance(raw, Mapping):
                raise EvidenceValidationError("challenge must be an object")
            challenge = Challenge.from_mapping(raw)
            if challenge.nonce in challenges:
                raise EvidenceValidationError("duplicate challenge nonce")
            challenges[challenge.nonce] = challenge
        except EvidenceValidationError as error:
            malformed_challenges.append({"index": index, "reason": str(error)})

    disclosures: list[Disclosure] = []
    malformed_disclosures: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_disclosures):
        try:
            if not isinstance(raw, Mapping):
                raise EvidenceValidationError("disclosure must be an object")
            disclosures.append(Disclosure.from_mapping(raw))
        except EvidenceValidationError as error:
            malformed_disclosures.append({"index": index, "reason": str(error)})

    accepted: list[EvidenceRecord] = []
    accepted_attestation_ids: list[str] = []
    rejected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    challenge_bindings: dict[str, tuple[str, str, str]] = {}

    for index, raw in enumerate(raw_attestations):
        reasons: list[str] = []
        try:
            if not isinstance(raw, Mapping):
                raise EvidenceValidationError("attestation must be an object")
            attestation = Attestation.from_mapping(raw)
        except EvidenceValidationError as error:
            rejected.append({"index": index, "reasons": [str(error)]})
            continue

        computed_id = attestation.computed_id()
        if attestation.attestation_id not in {None, computed_id}:
            reasons.append("attestation_id does not match the signed envelope")
        if computed_id in seen_ids:
            reasons.append("duplicate attestation envelope")
        seen_ids.add(computed_id)

        verifier = registry.get(attestation.verifier_id)
        evaluated_at = _parse_time(attestation.evaluated_at)
        if verifier is None:
            reasons.append("unregistered verifier")
        else:
            if not verifier.is_active_at(evaluated_at):
                reasons.append("verifier was not active at evaluation time")
            if attestation.source_kind not in verifier.allowed_source_kinds:
                reasons.append("source_kind is outside verifier authorization")
            if not verify_ed25519(
                bytes.fromhex(verifier.public_key_hex),
                attestation.payload_bytes(),
                bytes.fromhex(attestation.signature_hex),
            ):
                reasons.append("invalid Ed25519 signature")

        challenge = challenges.get(attestation.challenge_nonce)
        if challenge is None:
            reasons.append("challenge nonce was not issued by this ledger")
        else:
            if challenge.suite_id != attestation.suite_id:
                reasons.append("challenge suite_id mismatch")
            if evaluated_at < _parse_time(challenge.issued_at):
                reasons.append("attestation predates its challenge")
            if evaluated_at > _parse_time(challenge.expires_at):
                reasons.append("challenge expired before evaluation")
            binding = (
                attestation.verifier_id,
                attestation.run_id,
                attestation.suite_id,
            )
            previous_binding = challenge_bindings.get(challenge.nonce)
            if previous_binding is not None and previous_binding != binding:
                reasons.append("challenge was reused by a different verifier or run")
            else:
                challenge_bindings[challenge.nonce] = binding

        if _disclosure_blocks(attestation, disclosures):
            reasons.append("task was disclosed before the evaluation completed")

        if reasons:
            rejected.append(
                {
                    "index": index,
                    "attestation_id": computed_id,
                    "reasons": reasons,
                }
            )
            continue

        assert verifier is not None
        accepted_attestation_ids.append(computed_id)
        accepted.append(
            EvidenceRecord(
                criterion=attestation.criterion,
                passed=attestation.passed,
                source_kind=attestation.source_kind,
                task_id=attestation.task_id,
                domain=attestation.domain,
                run_id=attestation.run_id,
                verifier=verifier.independent_group,
                artifact_sha256=attestation.artifact_sha256,
                repeat_index=attestation.repeat_index,
                metadata={
                    **dict(attestation.metadata),
                    "attestation_id": computed_id,
                    "verifier_id": verifier.verifier_id,
                    "suite_id": attestation.suite_id,
                    "suite_version": attestation.suite_version,
                    "suite_sha256": attestation.suite_sha256,
                    "result_sha256": attestation.result_sha256,
                    "challenge_nonce": attestation.challenge_nonce,
                    "evaluated_at": attestation.evaluated_at,
                },
            )
        )

    evaluation = evaluate_evidence(accepted)
    canonical_ledger = {
        "schema_version": 1,
        "challenges": raw_challenges,
        "task_disclosures": raw_disclosures,
        "attestations": raw_attestations,
    }
    integrity_errors = (
        len(malformed_challenges)
        + len(malformed_disclosures)
        + len(rejected)
    )
    return {
        "integrity_ok": integrity_errors == 0,
        "ledger_sha256": sha256_json(canonical_ledger),
        "accepted_attestation_count": len(accepted),
        "accepted_attestation_ids": accepted_attestation_ids,
        "rejected_attestations": rejected,
        "malformed_challenges": malformed_challenges,
        "malformed_disclosures": malformed_disclosures,
        "registered_verifier_count": len(registry),
        "independent_group_count": len(
            {verifier.independent_group for verifier in registry.values()}
        ),
        "agi_evaluation": evaluation,
    }


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def issue_challenge(
    ledger_document: Mapping[str, Any],
    *,
    suite_id: str,
    ttl_minutes: int = 60,
    now: datetime | None = None,
) -> tuple[dict[str, Any], Challenge]:
    if not suite_id:
        raise EvidenceValidationError("suite_id is required")
    ttl = max(1, min(int(ttl_minutes), 24 * 60))
    issued = (now or datetime.now(UTC)).astimezone(UTC)
    challenge = Challenge(
        nonce=secrets.token_hex(32),
        suite_id=suite_id,
        issued_at=issued.isoformat(),
        expires_at=(issued + timedelta(minutes=ttl)).isoformat(),
    )
    updated = dict(ledger_document)
    if updated.get("schema_version") != 1:
        raise EvidenceValidationError("unsupported evidence ledger schema")
    challenges = list(updated.get("challenges", []))
    challenges.append(asdict(challenge))
    updated["challenges"] = challenges
    updated.setdefault("task_disclosures", [])
    updated.setdefault("attestations", [])
    return updated, challenge


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise EvidenceValidationError(f"{path} must contain one JSON object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agi.provenance",
        description="Issue fresh challenges and audit signed AGI evidence.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    audit = commands.add_parser("audit")
    audit.add_argument("ledger", type=Path)
    audit.add_argument("--registry", type=Path, required=True)
    audit.add_argument("--output", type=Path)
    audit.add_argument(
        "--require-clean-ledger",
        action="store_true",
        help="return non-zero when any malformed or rejected record exists",
    )
    audit.add_argument(
        "--require-agi-claim",
        action="store_true",
        help="return non-zero unless all strict AGI evidence criteria pass",
    )

    challenge = commands.add_parser("challenge")
    challenge.add_argument("ledger", type=Path)
    challenge.add_argument("--suite-id", required=True)
    challenge.add_argument("--ttl-minutes", type=int, default=60)
    challenge.add_argument("--output", type=Path)
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "audit":
        result = audit_ledger(_load_object(args.registry), _load_object(args.ledger))
        if args.output:
            _atomic_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        if args.require_clean_ledger and not result["integrity_ok"]:
            return 1
        if args.require_agi_claim and not result["agi_evaluation"][
            "agi_claim_supported"
        ]:
            return 2
        return 0

    if args.command == "challenge":
        updated, challenge = issue_challenge(
            _load_object(args.ledger),
            suite_id=args.suite_id,
            ttl_minutes=args.ttl_minutes,
        )
        destination = args.output or args.ledger
        _atomic_json(destination, updated)
        print(json.dumps(asdict(challenge), ensure_ascii=False, indent=2))
        return 0

    raise AssertionError(args.command)


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
