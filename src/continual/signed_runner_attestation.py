from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping

from .attested_external_tools import (
    ExternalRunnerAttestationError,
    RunnerIsolationAttestation,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ED25519_PUBLIC = re.compile(r"^[0-9a-f]{64}$")
_ED25519_SIGNATURE = re.compile(r"^[0-9a-f]{128}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExternalRunnerAttestationError(f"invalid signed runner timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise ExternalRunnerAttestationError("signed runner timestamps require a UTC offset")
    return parsed.astimezone(UTC)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _verify_ed25519(public_key: bytes, message: bytes, signature: bytes) -> bool:
    # Reuse the repository's strict canonical/subgroup-checked verifier. Import lazily so the
    # continual runtime does not create an import cycle while package-level safety guards install.
    from agi.external_provenance import verify_ed25519

    return verify_ed25519(public_key, message, signature)


@dataclass(frozen=True)
class SignedRunnerTrustProof:
    """A time-bounded Ed25519 authorization for one exact runner-isolation statement.

    The proof is deliberately separate from ``ExternalToolRunner``. A runner may present its
    isolation statement, but it cannot mint verifier authorization unless it possesses the private key
    corresponding to a separately configured trusted public key. The challenge nonce prevents a proof
    issued for one activation context from silently authorizing another context.
    """

    verifier_id: str
    runner_id: str
    statement_sha256: str
    challenge_nonce: str
    issued_at: str
    expires_at: str
    signature_algorithm: str = "ed25519"
    signature_hex: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "purpose": "runner-isolation-attestation-v1",
            "verifier_id": self.verifier_id,
            "runner_id": self.runner_id,
            "statement_sha256": self.statement_sha256,
            "challenge_nonce": self.challenge_nonce,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "signature_algorithm": self.signature_algorithm,
        }

    def payload_bytes(self) -> bytes:
        return _canonical_json_bytes(self.payload())

    def validate_shape(self) -> None:
        if not _SAFE_ID.fullmatch(self.verifier_id) or not _SAFE_ID.fullmatch(self.runner_id):
            raise ExternalRunnerAttestationError("signed runner proof IDs must be safe identifiers")
        if not _SHA256.fullmatch(self.statement_sha256):
            raise ExternalRunnerAttestationError("signed runner proof statement must be lowercase SHA-256")
        if not _SHA256.fullmatch(self.challenge_nonce):
            raise ExternalRunnerAttestationError("signed runner challenge nonce must be 32-byte lowercase hex")
        issued = _parse_time(self.issued_at)
        expires = _parse_time(self.expires_at)
        if expires <= issued:
            raise ExternalRunnerAttestationError("signed runner proof must expire after it is issued")
        if self.signature_algorithm != "ed25519":
            raise ExternalRunnerAttestationError("signed runner proof algorithm must be ed25519")
        if not _ED25519_SIGNATURE.fullmatch(self.signature_hex):
            raise ExternalRunnerAttestationError("signed runner proof requires a canonical Ed25519 signature")


class Ed25519RunnerAttestationVerifier:
    """Verify exact isolation statements using independently configured public keys and challenges.

    Public keys, signed proofs, the expected challenge nonce, and the verification time are supplied
    separately from the runner. Verification fails closed on identity/statement substitution, stale or
    future proofs, challenge rebinding, malformed keys/signatures, or Ed25519 failure. This makes the
    authorization auditable without sharing a secret or pinning every exact statement digest in the
    runtime configuration.

    A valid signature proves that the configured verifier authorized the exact statement. It does not,
    by itself, prove that the verifier is genuinely independent or that the runner really enforces the
    claimed operating-system isolation. Those remain external production-evidence requirements.
    """

    def __init__(
        self,
        *,
        trusted_public_keys: Mapping[str, str],
        proofs: Mapping[tuple[str, str], SignedRunnerTrustProof],
        required_challenge_nonce: str,
        verification_time: str,
    ) -> None:
        if not _SHA256.fullmatch(required_challenge_nonce):
            raise ExternalRunnerAttestationError("required runner challenge must be 32-byte lowercase hex")
        self.required_challenge_nonce = required_challenge_nonce
        self.verification_time = _parse_time(verification_time)

        keys: dict[str, str] = {}
        for verifier_id, public_key_hex in trusted_public_keys.items():
            if not isinstance(verifier_id, str) or not _SAFE_ID.fullmatch(verifier_id):
                raise ExternalRunnerAttestationError("trusted runner verifier ID is invalid")
            if not isinstance(public_key_hex, str) or not _ED25519_PUBLIC.fullmatch(public_key_hex):
                raise ExternalRunnerAttestationError("trusted runner verifier key must be canonical Ed25519 hex")
            keys[verifier_id] = public_key_hex
        self._keys = keys

        checked: dict[tuple[str, str], SignedRunnerTrustProof] = {}
        for key, proof in proofs.items():
            if not isinstance(key, tuple) or len(key) != 2:
                raise ExternalRunnerAttestationError("signed runner proof keys must be (verifier_id, runner_id)")
            if not isinstance(proof, SignedRunnerTrustProof):
                raise ExternalRunnerAttestationError("signed runner proof map contains an invalid proof")
            proof.validate_shape()
            if key != (proof.verifier_id, proof.runner_id):
                raise ExternalRunnerAttestationError("signed runner proof map key does not match proof identity")
            checked[key] = proof
        self._proofs = checked

    def verify(self, attestation: RunnerIsolationAttestation) -> bool:
        try:
            attestation.validate()
            proof = self._proofs.get((attestation.verifier_id, attestation.runner_id))
            if proof is None:
                return False
            proof.validate_shape()
            if proof.statement_sha256 != attestation.statement_sha256:
                return False
            if proof.challenge_nonce != self.required_challenge_nonce:
                return False
            issued = _parse_time(proof.issued_at)
            expires = _parse_time(proof.expires_at)
            if self.verification_time < issued or self.verification_time > expires:
                return False
            public_key_hex = self._keys.get(proof.verifier_id)
            if public_key_hex is None:
                return False
            return _verify_ed25519(
                bytes.fromhex(public_key_hex),
                proof.payload_bytes(),
                bytes.fromhex(proof.signature_hex),
            )
        except (ExternalRunnerAttestationError, ValueError, OverflowError):
            return False
