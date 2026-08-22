from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import tempfile
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

from .evaluation import CRITERION_KEYS, EvidenceRecord, sign_evidence


_Q = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _Q - 2, _Q)) % _Q
_I = pow(2, (_Q - 1) // 4, _Q)
_IDENTITY = (0, 1)
_BASE_Y = (4 * pow(5, _Q - 2, _Q)) % _Q
_FORBIDDEN_ATTESTATION_SECRET_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "answer",
        "answers",
        "auth_token",
        "authorization",
        "bridge_secret",
        "credential",
        "credentials",
        "expected_answer",
        "expected_answers",
        "hidden_seed",
        "password",
        "private_key",
        "private_key_hex",
        "secret_key",
        "seed",
        "secret",
        "signing_key",
        "signing_key_hex",
        "token",
    }
)


def _is_forbidden_attestation_secret_key(value: str) -> bool:
    camel_separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value.strip())
    normalized = camel_separated.lower().replace("-", "_")
    if normalized in _FORBIDDEN_ATTESTATION_SECRET_KEYS:
        return True
    if normalized.endswith(
        (
            "_access_token",
            "_answer",
            "_answers",
            "_api_key",
            "_auth_token",
            "_credential",
            "_credentials",
            "_password",
            "_private_key",
            "_private_key_hex",
            "_secret",
            "_secret_key",
            "_seed",
            "_signing_key",
            "_signing_key_hex",
        )
    ):
        return True
    compact = "".join(character for character in normalized if character.isalnum())
    if any(
        marker in compact
        for marker in (
            "accesstoken",
            "apikey",
            "apitoken",
            "authtoken",
            "bridgesecret",
            "expectedanswer",
            "hiddenanswer",
            "hiddenseed",
            "privatekey",
            "secretkey",
            "signingkey",
        )
    ):
        return True
    tokens = tuple(token for token in re.split(r"[^a-z0-9]+", normalized) if token)
    if any(
        token in {
            "answer",
            "answers",
            "credential",
            "credentials",
            "password",
            "secret",
            "seed",
            "token",
        }
        for token in tokens
    ):
        return True
    return compact.endswith(
        (
            "accesstoken",
            "answer",
            "apikey",
            "answers",
            "authtoken",
            "credential",
            "credentials",
            "password",
            "privatekey",
            "privatekeyhex",
            "secret",
            "secretkey",
            "seed",
            "signingkey",
            "signingkeyhex",
            "token",
        )
    )


class ExternalEvidenceError(ValueError):
    """Raised when external evidence cannot be interpreted safely."""


def _x_recover(y: int) -> int:
    xx = ((y * y - 1) * pow((_D * y * y + 1) % _Q, _Q - 2, _Q)) % _Q
    x = pow(xx, (_Q + 3) // 8, _Q)
    if (x * x - xx) % _Q != 0:
        x = (x * _I) % _Q
    if (x * x - xx) % _Q != 0:
        raise ExternalEvidenceError("invalid Edwards25519 point")
    if x & 1:
        x = _Q - x
    return x


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
        raise ExternalEvidenceError("negative scalar")
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
        raise ExternalEvidenceError("Ed25519 point must be 32 bytes")
    sign = encoded[31] >> 7
    y = int.from_bytes(encoded, "little") & ((1 << 255) - 1)
    if y >= _Q:
        raise ExternalEvidenceError("non-canonical Edwards25519 y coordinate")
    x = _x_recover(y)
    if (x & 1) != sign:
        x = _Q - x
    point = (x, y)
    if not _on_curve(point) or _encode_point(point) != encoded:
        raise ExternalEvidenceError("non-canonical Edwards25519 point")
    return point


def verify_ed25519(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Strict Ed25519 verification with subgroup and canonical-encoding checks."""
    try:
        if len(public_key) != 32 or len(signature) != 64:
            return False
        public_point = _decode_point(public_key)
        r_point = _decode_point(signature[:32])
        scalar = int.from_bytes(signature[32:], "little")
        if scalar >= _L or public_point == _IDENTITY or r_point == _IDENTITY:
            return False
        if _scalar_multiply(public_point, _L) != _IDENTITY:
            return False
        if _scalar_multiply(r_point, _L) != _IDENTITY:
            return False
        challenge = int.from_bytes(
            hashlib.sha512(signature[:32] + public_key + message).digest(), "little"
        ) % _L
        left = _scalar_multiply(_BASE, scalar)
        right = _point_add(r_point, _scalar_multiply(public_point, challenge))
        return left == right
    except (ExternalEvidenceError, ValueError, OverflowError):
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


def _forbidden_secret_paths(value: Any, prefix: str = "$") -> tuple[str, ...]:
    paths: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key)
            path = f"{prefix}.{name}"
            if _is_forbidden_attestation_secret_key(name):
                paths.append(path)
            paths.extend(_forbidden_secret_paths(child, path))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            paths.extend(_forbidden_secret_paths(child, f"{prefix}[{index}]"))
    return tuple(paths)


def _is_hex(value: str, bytes_len: int) -> bool:
    if len(value) != bytes_len * 2:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return value == value.lower()


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExternalEvidenceError(f"invalid timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise ExternalEvidenceError("timestamp must include a UTC offset")
    return parsed.astimezone(UTC)


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "wb", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False
    ) as handle:
        handle.write(value)
        temporary = Path(handle.name)
    temporary.replace(path)


@dataclass(frozen=True)
class ExternalVerifier:
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

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExternalVerifier":
        allowed = value.get("allowed_source_kinds", ())
        if not isinstance(allowed, Sequence) or isinstance(allowed, (str, bytes)):
            raise ExternalEvidenceError("allowed_source_kinds must be an array")
        verifier = cls(
            verifier_id=str(value.get("verifier_id", "")),
            independent_group=str(value.get("independent_group", "")),
            public_key_hex=str(value.get("public_key_hex", "")).lower(),
            allowed_source_kinds=tuple(str(item) for item in allowed),
            status=str(value.get("status", "active")),
            valid_from=(str(value["valid_from"]) if value.get("valid_from") else None),
            valid_until=(str(value["valid_until"]) if value.get("valid_until") else None),
        )
        verifier.validate()
        return verifier

    def validate(self) -> None:
        if not self.verifier_id or not self.independent_group:
            raise ExternalEvidenceError("verifier_id and independent_group are required")
        if not _is_hex(self.public_key_hex, 32):
            raise ExternalEvidenceError("public_key_hex must be canonical lowercase 32-byte hex")
        if self.status not in {"active", "suspended", "revoked"}:
            raise ExternalEvidenceError(f"invalid verifier status: {self.status}")
        if self.valid_from:
            _parse_time(self.valid_from)
        if self.valid_until:
            _parse_time(self.valid_until)

    def active_at(self, moment: datetime) -> bool:
        if self.status != "active":
            return False
        if self.valid_from and moment < _parse_time(self.valid_from):
            return False
        if self.valid_until and moment > _parse_time(self.valid_until):
            return False
        return True


@dataclass(frozen=True)
class ExternalChallenge:
    nonce: str
    suite_id: str
    suite_sha256: str
    issued_at: str
    expires_at: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExternalChallenge":
        challenge = cls(
            nonce=str(value.get("nonce", "")).lower(),
            suite_id=str(value.get("suite_id", "")),
            suite_sha256=str(value.get("suite_sha256", "")).lower(),
            issued_at=str(value.get("issued_at", "")),
            expires_at=str(value.get("expires_at", "")),
        )
        challenge.validate()
        return challenge

    def validate(self) -> None:
        if not _is_hex(self.nonce, 32):
            raise ExternalEvidenceError("challenge nonce must be canonical lowercase 32-byte hex")
        if not self.suite_id or not _is_hex(self.suite_sha256, 32):
            raise ExternalEvidenceError("challenge requires suite_id and canonical suite_sha256")
        issued = _parse_time(self.issued_at)
        expires = _parse_time(self.expires_at)
        if expires <= issued:
            raise ExternalEvidenceError("challenge expires_at must be after issued_at")


@dataclass(frozen=True)
class TaskDisclosure:
    task_id: str
    suite_sha256: str
    disclosed_at: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TaskDisclosure":
        item = cls(
            task_id=str(value.get("task_id", "")),
            suite_sha256=str(value.get("suite_sha256", "")).lower(),
            disclosed_at=str(value.get("disclosed_at", "")),
        )
        if not item.task_id or not _is_hex(item.suite_sha256, 32):
            raise ExternalEvidenceError("disclosure requires task_id and canonical suite_sha256")
        _parse_time(item.disclosed_at)
        return item


_ATTESTATION_FIELDS = (
    "schema_version",
    "criterion",
    "success",
    "source_kind",
    "task_id",
    "domain",
    "run_id",
    "producer",
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


@dataclass(frozen=True)
class ExternalAttestation:
    schema_version: int
    criterion: str
    success: bool
    source_kind: str
    task_id: str
    domain: str
    run_id: str
    producer: str
    verifier_id: str
    suite_id: str
    suite_version: str
    suite_sha256: str
    artifact_sha256: str
    result_sha256: str
    challenge_nonce: str
    evaluated_at: str
    repeat_index: int
    metadata: Mapping[str, Any] = field(default_factory=dict)
    signature_algorithm: str = "ed25519"
    signature_hex: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExternalAttestation":
        allowed = set(_ATTESTATION_FIELDS) | {"signature_hex"}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ExternalEvidenceError("unknown attestation fields: " + ", ".join(unknown))
        missing = [field for field in _ATTESTATION_FIELDS if field not in value]
        if missing or "signature_hex" not in value:
            raise ExternalEvidenceError(
                "missing attestation fields: "
                + ", ".join(missing + ([] if "signature_hex" in value else ["signature_hex"]))
            )
        metadata = value.get("metadata")
        if not isinstance(metadata, Mapping):
            raise ExternalEvidenceError("metadata must be an object")
        if not isinstance(value.get("success"), bool):
            raise ExternalEvidenceError("success must be a JSON boolean")
        if not isinstance(value.get("schema_version"), int) or not isinstance(value.get("repeat_index"), int):
            raise ExternalEvidenceError("schema_version and repeat_index must be integers")
        attestation = cls(
            schema_version=value["schema_version"],
            criterion=str(value["criterion"]),
            success=value["success"],
            source_kind=str(value["source_kind"]),
            task_id=str(value["task_id"]),
            domain=str(value["domain"]),
            run_id=str(value["run_id"]),
            producer=str(value["producer"]),
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
        )
        attestation.validate_shape()
        return attestation

    def validate_shape(self) -> None:
        if self.schema_version != 1:
            raise ExternalEvidenceError("unsupported attestation schema_version")
        if self.criterion not in CRITERION_KEYS:
            raise ExternalEvidenceError(f"unknown criterion: {self.criterion}")
        for name in (
            "source_kind",
            "task_id",
            "domain",
            "run_id",
            "producer",
            "verifier_id",
            "suite_id",
            "suite_version",
        ):
            if not str(getattr(self, name)).strip():
                raise ExternalEvidenceError(f"{name} must be non-empty")
        for name in ("suite_sha256", "artifact_sha256", "result_sha256", "challenge_nonce"):
            if not _is_hex(str(getattr(self, name)), 32):
                raise ExternalEvidenceError(f"{name} must be canonical lowercase SHA-256/32-byte hex")
        if self.repeat_index < 0:
            raise ExternalEvidenceError("repeat_index must be non-negative")
        _parse_time(self.evaluated_at)
        if self.signature_algorithm != "ed25519":
            raise ExternalEvidenceError("signature_algorithm must be ed25519")
        if not _is_hex(self.signature_hex, 64):
            raise ExternalEvidenceError("signature_hex must be canonical lowercase 64-byte hex")

    def payload(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["metadata"] = dict(self.metadata)
        return {field: raw[field] for field in _ATTESTATION_FIELDS}

    def payload_bytes(self) -> bytes:
        return canonical_json_bytes(self.payload())

    def attestation_id(self) -> str:
        return sha256_json({"payload": self.payload(), "signature_hex": self.signature_hex})


def prepare_external_attestation_payload(value: Mapping[str, Any]) -> tuple[ExternalAttestation, bytes]:
    """Validate an unsigned result statement and return the exact bytes an evaluator must sign.

    The repository never receives or handles the evaluator's private key.  The input deliberately
    omits ``signature_hex`` so the unsigned statement cannot be mistaken for accepted evidence.
    """

    if not all(isinstance(key, str) for key in value):
        raise ExternalEvidenceError("unsigned attestation field names must be strings")
    if "signature_hex" in value:
        raise ExternalEvidenceError("unsigned attestation statement must omit signature_hex")
    forbidden = _forbidden_secret_paths(value)
    if forbidden:
        raise ExternalEvidenceError(
            "unsigned attestation statement contains forbidden secret fields: "
            + ", ".join(forbidden)
        )
    unknown = sorted(set(value) - set(_ATTESTATION_FIELDS))
    missing = [field for field in _ATTESTATION_FIELDS if field not in value]
    if unknown:
        raise ExternalEvidenceError("unknown unsigned attestation fields: " + ", ".join(unknown))
    if missing:
        raise ExternalEvidenceError("missing unsigned attestation fields: " + ", ".join(missing))
    if type(value["schema_version"]) is not int or type(value["repeat_index"]) is not int:
        raise ExternalEvidenceError("schema_version and repeat_index must be JSON integers")
    if type(value["success"]) is not bool:
        raise ExternalEvidenceError("success must be a JSON boolean")
    if not isinstance(value["metadata"], Mapping):
        raise ExternalEvidenceError("metadata must be an object")
    string_fields = set(_ATTESTATION_FIELDS) - {
        "schema_version",
        "success",
        "repeat_index",
        "metadata",
    }
    non_strings = sorted(field for field in string_fields if not isinstance(value[field], str))
    if non_strings:
        raise ExternalEvidenceError(
            "unsigned attestation string fields have invalid types: " + ", ".join(non_strings)
        )
    placeholder = dict(value)
    placeholder["signature_hex"] = "00" * 64
    attestation = ExternalAttestation.from_mapping(placeholder)
    if attestation.payload() != dict(value):
        raise ExternalEvidenceError(
            "unsigned attestation statement must already use canonical field values"
        )
    return attestation, attestation.payload_bytes()


def finalize_external_attestation(
    value: Mapping[str, Any],
    *,
    public_key_hex: str,
    signature_hex: str,
) -> ExternalAttestation:
    """Assemble a signed attestation only after verifying its detached Ed25519 signature.

    Full verifier registration, challenge freshness, held-out disclosure, replay, and request-subject
    checks remain the responsibility of ``audit_external_ledger``.  This boundary only prevents a
    malformed or mismatched detached signature from being packaged as an attestation.
    """

    unsigned, payload = prepare_external_attestation_payload(value)
    if not _is_hex(public_key_hex, 32):
        raise ExternalEvidenceError("public_key_hex must be canonical lowercase 32-byte hex")
    if not _is_hex(signature_hex, 64):
        raise ExternalEvidenceError("signature_hex must be canonical lowercase 64-byte hex")
    if not verify_ed25519(bytes.fromhex(public_key_hex), payload, bytes.fromhex(signature_hex)):
        raise ExternalEvidenceError("detached Ed25519 signature does not match canonical payload")
    return replace(unsigned, signature_hex=signature_hex)


@dataclass(frozen=True)
class ExternalAuditDecision:
    accepted: bool
    attestation_id: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def verify_external_attestation(
    attestation: ExternalAttestation,
    *,
    verifier: ExternalVerifier,
    challenge: ExternalChallenge,
    disclosures: Sequence[TaskDisclosure] = (),
) -> ExternalAuditDecision:
    reasons: list[str] = []
    evaluated_at = _parse_time(attestation.evaluated_at)
    if attestation.verifier_id != verifier.verifier_id:
        reasons.append("verifier identity mismatch")
    if attestation.producer in {verifier.verifier_id, verifier.independent_group}:
        reasons.append("producer must differ from independent evaluator")
    if attestation.source_kind not in verifier.allowed_source_kinds:
        reasons.append("source kind is not allowed for verifier")
    if not verifier.active_at(evaluated_at):
        reasons.append("verifier was not active at evaluation time")
    if attestation.challenge_nonce != challenge.nonce:
        reasons.append("challenge nonce mismatch")
    if attestation.suite_id != challenge.suite_id or attestation.suite_sha256 != challenge.suite_sha256:
        reasons.append("challenge suite binding mismatch")
    issued = _parse_time(challenge.issued_at)
    expires = _parse_time(challenge.expires_at)
    if evaluated_at < issued or evaluated_at > expires:
        reasons.append("evaluation occurred outside challenge validity window")
    for disclosure in disclosures:
        if (
            disclosure.task_id == attestation.task_id
            and disclosure.suite_sha256 == attestation.suite_sha256
            and evaluated_at >= _parse_time(disclosure.disclosed_at)
        ):
            reasons.append("held-out task was disclosed before evaluation completed")
            break
    try:
        public_key = bytes.fromhex(verifier.public_key_hex)
        signature = bytes.fromhex(attestation.signature_hex)
    except ValueError:
        reasons.append("signature material is not valid hex")
    else:
        if not verify_ed25519(public_key, attestation.payload_bytes(), signature):
            reasons.append("ed25519 signature verification failed")
    return ExternalAuditDecision(
        accepted=not reasons,
        attestation_id=attestation.attestation_id(),
        reasons=tuple(reasons),
    )


def external_provenance_ref(attestation: ExternalAttestation) -> str:
    return (
        f"ed25519:{attestation.attestation_id()};suite={attestation.suite_sha256};"
        f"result={attestation.result_sha256};challenge={attestation.challenge_nonce};"
        f"repeat={attestation.repeat_index}"
    )


def bridge_external_attestation(
    attestation: ExternalAttestation,
    *,
    verifier: ExternalVerifier,
    challenge: ExternalChallenge,
    disclosures: Sequence[TaskDisclosure] = (),
    bridge_attestor_id: str,
    bridge_secret: str,
) -> EvidenceRecord:
    """Convert verified public-key evidence into the existing strict HMAC claim gate.

    The external evaluator signature is checked first. The bridge then signs an EvidenceRecord
    whose HMAC payload conditionally includes a provenance_ref binding the external attestation,
    suite, result, challenge and repeat. No bridge secret is persisted in the resulting record.
    """
    decision = verify_external_attestation(
        attestation,
        verifier=verifier,
        challenge=challenge,
        disclosures=disclosures,
    )
    if not decision.accepted:
        raise ExternalEvidenceError("external attestation rejected: " + "; ".join(decision.reasons))
    if not bridge_attestor_id or not bridge_secret:
        raise ExternalEvidenceError("bridge_attestor_id and bridge_secret are required")
    evaluator = verifier.independent_group
    if bridge_attestor_id in {attestation.producer, evaluator}:
        raise ExternalEvidenceError("bridge attestor must be a third identity")
    record = EvidenceRecord(
        criterion=attestation.criterion,
        task_id=attestation.task_id,
        domain=attestation.domain,
        success=attestation.success,
        run_id=attestation.run_id,
        artifact_ref=f"sha256:{attestation.artifact_sha256}",
        evaluator=evaluator,
        tier="production",
        independent=True,
        timestamp=attestation.evaluated_at,
        notes=(
            f"Externally signed result {decision.attestation_id}; result_sha256={attestation.result_sha256}; "
            f"suite={attestation.suite_id}@{attestation.suite_version}."
        ),
        producer=attestation.producer,
        provenance_ref=external_provenance_ref(attestation),
    )
    return sign_evidence(record, attestor_id=bridge_attestor_id, secret=bridge_secret)


def _load_registry(value: Mapping[str, Any]) -> dict[str, ExternalVerifier]:
    if value.get("schema_version") != 1 or not isinstance(value.get("verifiers"), list):
        raise ExternalEvidenceError("verifier registry must have schema_version 1 and verifiers array")
    result: dict[str, ExternalVerifier] = {}
    for raw in value["verifiers"]:
        if not isinstance(raw, Mapping):
            raise ExternalEvidenceError("verifier entry must be an object")
        verifier = ExternalVerifier.from_mapping(raw)
        if verifier.verifier_id in result:
            raise ExternalEvidenceError(f"duplicate verifier_id: {verifier.verifier_id}")
        result[verifier.verifier_id] = verifier
    return result


def audit_external_ledger(
    ledger: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> dict[str, Any]:
    """Audit signatures, freshness, contamination, replay and duplicate-result inflation."""
    if ledger.get("schema_version") != 1:
        raise ExternalEvidenceError("ledger schema_version must be 1")
    for key in ("challenges", "disclosures", "attestations"):
        if not isinstance(ledger.get(key), list):
            raise ExternalEvidenceError(f"ledger {key} must be an array")
    verifiers = _load_registry(registry)
    challenges: dict[str, ExternalChallenge] = {}
    for raw in ledger["challenges"]:
        if not isinstance(raw, Mapping):
            raise ExternalEvidenceError("challenge entry must be an object")
        challenge = ExternalChallenge.from_mapping(raw)
        if challenge.nonce in challenges:
            raise ExternalEvidenceError(f"duplicate challenge nonce: {challenge.nonce}")
        challenges[challenge.nonce] = challenge
    disclosures = [TaskDisclosure.from_mapping(raw) for raw in ledger["disclosures"] if isinstance(raw, Mapping)]
    if len(disclosures) != len(ledger["disclosures"]):
        raise ExternalEvidenceError("disclosure entry must be an object")

    decisions: list[dict[str, Any]] = []
    accepted_ids: set[str] = set()
    challenge_bindings: dict[str, tuple[str, str, str]] = {}
    repeat_bindings: set[tuple[str, str, str, int]] = set()
    group_ids: set[str] = set()

    for index, raw in enumerate(ledger["attestations"]):
        reasons: list[str] = []
        try:
            if not isinstance(raw, Mapping):
                raise ExternalEvidenceError("attestation entry must be an object")
            attestation = ExternalAttestation.from_mapping(raw)
        except ExternalEvidenceError as exc:
            decisions.append({"index": index, "accepted": False, "attestation_id": None, "reasons": [str(exc)]})
            continue
        verifier = verifiers.get(attestation.verifier_id)
        challenge = challenges.get(attestation.challenge_nonce)
        if verifier is None:
            reasons.append("unregistered verifier")
        if challenge is None:
            reasons.append("unknown challenge")
        if verifier is not None and challenge is not None:
            decision = verify_external_attestation(
                attestation,
                verifier=verifier,
                challenge=challenge,
                disclosures=disclosures,
            )
            reasons.extend(decision.reasons)
        attestation_id = attestation.attestation_id()
        if attestation_id in accepted_ids:
            reasons.append("duplicate attestation replay")
        binding = (attestation.verifier_id, attestation.run_id, attestation.suite_sha256)
        previous = challenge_bindings.get(attestation.challenge_nonce)
        if previous is not None and previous != binding:
            reasons.append("challenge replayed for a different verifier, run or suite")
        repeat_key = (
            attestation.verifier_id,
            attestation.run_id,
            attestation.task_id,
            attestation.repeat_index,
        )
        if repeat_key in repeat_bindings:
            reasons.append("duplicate task repeat index")
        accepted = not reasons
        if accepted:
            accepted_ids.add(attestation_id)
            challenge_bindings.setdefault(attestation.challenge_nonce, binding)
            repeat_bindings.add(repeat_key)
            assert verifier is not None
            group_ids.add(verifier.independent_group)
        decisions.append(
            {
                "index": index,
                "accepted": accepted,
                "attestation_id": attestation_id,
                "reasons": reasons,
            }
        )

    rejected = sum(not item["accepted"] for item in decisions)
    return {
        "clean": rejected == 0,
        "accepted_count": len(decisions) - rejected,
        "rejected_count": rejected,
        "independent_group_count": len(group_ids),
        "accepted_attestation_ids": sorted(accepted_ids),
        "decisions": decisions,
        "ledger_digest": sha256_json(ledger),
        "registry_digest": sha256_json(registry),
        "claim_boundary": "A clean provenance audit establishes integrity only; it does not establish AGI.",
    }


def issue_challenge(
    ledger: MutableMapping[str, Any],
    *,
    suite_id: str,
    suite_sha256: str,
    ttl_minutes: int = 60,
    now: datetime | None = None,
) -> ExternalChallenge:
    if ledger.get("schema_version") != 1 or not isinstance(ledger.get("challenges"), list):
        raise ExternalEvidenceError("ledger must have schema_version 1 and challenges array")
    if ttl_minutes < 1 or ttl_minutes > 1440:
        raise ExternalEvidenceError("ttl_minutes must be between 1 and 1440")
    if not suite_id or not _is_hex(suite_sha256.lower(), 32):
        raise ExternalEvidenceError("suite_id and canonical suite_sha256 are required")
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    challenge = ExternalChallenge(
        nonce=secrets.token_hex(32),
        suite_id=suite_id,
        suite_sha256=suite_sha256.lower(),
        issued_at=moment.isoformat(),
        expires_at=(moment + timedelta(minutes=ttl_minutes)).isoformat(),
    )
    challenge.validate()
    ledger["challenges"].append(asdict(challenge))
    return challenge


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ExternalEvidenceError(f"{path} must contain a JSON object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agi-external-evidence")
    sub = parser.add_subparsers(dest="cmd", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("ledger", type=Path)
    audit.add_argument("--registry", type=Path, required=True)
    audit.add_argument("--output", type=Path)
    audit.add_argument("--require-clean", action="store_true")
    challenge = sub.add_parser("challenge")
    challenge.add_argument("ledger", type=Path)
    challenge.add_argument("--suite-id", required=True)
    challenge.add_argument("--suite-sha256", required=True)
    challenge.add_argument("--ttl-minutes", type=int, default=60)
    payload = sub.add_parser(
        "payload",
        help="validate an unsigned result statement and emit the exact canonical bytes to sign",
    )
    payload.add_argument("statement", type=Path)
    payload.add_argument("--output", type=Path, required=True)
    finalize = sub.add_parser(
        "finalize",
        help="verify a detached evaluator signature and assemble a signed attestation",
    )
    finalize.add_argument("statement", type=Path)
    finalize.add_argument("--public-key-hex", required=True)
    finalize.add_argument("--signature-hex", required=True)
    finalize.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.cmd == "audit":
        report = audit_external_ledger(_read_json(args.ledger), _read_json(args.registry))
        if args.output:
            _atomic_write_json(args.output, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        if args.require_clean and not report["clean"]:
            raise SystemExit(2)
        return
    if args.cmd == "challenge":
        ledger = _read_json(args.ledger)
        challenge = issue_challenge(
            ledger,
            suite_id=args.suite_id,
            suite_sha256=args.suite_sha256,
            ttl_minutes=args.ttl_minutes,
        )
        _atomic_write_json(args.ledger, ledger)
        print(json.dumps(asdict(challenge), ensure_ascii=False, indent=2, sort_keys=True))
        return
    if args.cmd == "payload":
        statement = _read_json(args.statement)
        attestation, payload = prepare_external_attestation_payload(statement)
        _atomic_write_bytes(args.output, payload)
        print(
            json.dumps(
                {
                    "algorithm": attestation.signature_algorithm,
                    "claim_boundary": "unsigned payload only; not external evidence",
                    "output": str(args.output),
                    "payload_bytes": len(payload),
                    "payload_sha256": hashlib.sha256(payload).hexdigest(),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return
    if args.cmd == "finalize":
        statement = _read_json(args.statement)
        attestation = finalize_external_attestation(
            statement,
            public_key_hex=args.public_key_hex,
            signature_hex=args.signature_hex,
        )
        _atomic_write_json(args.output, asdict(attestation))
        print(
            json.dumps(
                {
                    "attestation_id": attestation.attestation_id(),
                    "claim_boundary": (
                        "signature verified only; ledger audit and strict external claim gate still required"
                    ),
                    "output": str(args.output),
                    "payload_sha256": hashlib.sha256(attestation.payload_bytes()).hexdigest(),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return
    raise SystemExit(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    main()
