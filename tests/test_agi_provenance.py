from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from agi.provenance import (
    _BASE,
    _L,
    _encode_point,
    _scalar_multiply,
    audit_ledger,
    canonical_json_bytes,
    issue_challenge,
    verify_ed25519,
)


RFC8032_PUBLIC_KEY = bytes.fromhex(
    "d75a980182b10ab7d54bfed3c964073a"
    "0ee172f3daa62325af021a68f707511a"
)
RFC8032_SIGNATURE = bytes.fromhex(
    "e5564300c360ac729086e2cc806e828a"
    "84877f1eb8e5d974d873e06522490155"
    "5fb8821590a33bacc61e39701cf9b46b"
    "d25bf5f0595bbe24655141438e7a100b"
)


def _sign(seed: bytes, message: bytes) -> tuple[bytes, bytes]:
    expanded = hashlib.sha512(seed).digest()
    scalar_bytes = bytearray(expanded[:32])
    scalar_bytes[0] &= 248
    scalar_bytes[31] &= 63
    scalar_bytes[31] |= 64
    scalar = int.from_bytes(scalar_bytes, "little")
    public_key = _encode_point(_scalar_multiply(_BASE, scalar))

    nonce = int.from_bytes(
        hashlib.sha512(expanded[32:] + message).digest(), "little"
    ) % _L
    encoded_r = _encode_point(_scalar_multiply(_BASE, nonce))
    challenge = int.from_bytes(
        hashlib.sha512(encoded_r + public_key + message).digest(), "little"
    ) % _L
    encoded_s = ((nonce + challenge * scalar) % _L).to_bytes(32, "little")
    return public_key, encoded_r + encoded_s


def _documents(
    *,
    task_id: str = "held-out-task-1",
    run_id: str = "external-run-1",
    evaluated_at: str = "2026-08-16T08:30:00+00:00",
    challenge_nonce: str = "11" * 32,
):
    seed = bytes.fromhex("1f" * 32)
    payload = {
        "schema_version": 1,
        "criterion": "breadth",
        "passed": True,
        "source_kind": "external_test",
        "task_id": task_id,
        "domain": "mathematics",
        "run_id": run_id,
        "verifier_id": "lab-a-key-1",
        "suite_id": "held-out-suite-a",
        "suite_version": "1.0",
        "suite_sha256": "22" * 32,
        "artifact_sha256": "33" * 32,
        "result_sha256": "44" * 32,
        "challenge_nonce": challenge_nonce,
        "evaluated_at": evaluated_at,
        "repeat_index": 0,
        "metadata": {"runner": "lab-a-runner-3"},
        "signature_algorithm": "ed25519",
    }
    public_key, signature = _sign(seed, canonical_json_bytes(payload))
    attestation = {**payload, "signature_hex": signature.hex()}
    registry = {
        "schema_version": 1,
        "verifiers": [
            {
                "verifier_id": "lab-a-key-1",
                "independent_group": "independent-lab-a",
                "public_key_hex": public_key.hex(),
                "allowed_source_kinds": ["external_test"],
                "status": "active",
            }
        ],
    }
    ledger = {
        "schema_version": 1,
        "challenges": [
            {
                "nonce": challenge_nonce,
                "suite_id": "held-out-suite-a",
                "issued_at": "2026-08-16T08:00:00+00:00",
                "expires_at": "2026-08-16T09:00:00+00:00",
            }
        ],
        "task_disclosures": [],
        "attestations": [attestation],
    }
    return registry, ledger


def test_rfc8032_signature_vector_and_tampering() -> None:
    assert verify_ed25519(RFC8032_PUBLIC_KEY, b"", RFC8032_SIGNATURE) is True
    assert verify_ed25519(RFC8032_PUBLIC_KEY, b"x", RFC8032_SIGNATURE) is False
    altered = bytearray(RFC8032_SIGNATURE)
    altered[0] ^= 1
    assert verify_ed25519(RFC8032_PUBLIC_KEY, b"", bytes(altered)) is False


def test_valid_signed_attestation_is_admitted_but_not_enough_for_agi() -> None:
    registry, ledger = _documents()

    result = audit_ledger(registry, ledger)

    assert result["integrity_ok"] is True
    assert result["accepted_attestation_count"] == 1
    assert result["rejected_attestations"] == []
    assert result["agi_evaluation"]["agi_claim_supported"] is False
    detail = result["agi_evaluation"]["criterion_details"]["breadth"]
    assert detail["verified_pass_trials"] == 1
    assert detail["verifiers"] == ["independent-lab-a"]


def test_invalid_signature_is_rejected() -> None:
    registry, ledger = _documents()
    signature = ledger["attestations"][0]["signature_hex"]
    ledger["attestations"][0]["signature_hex"] = (
        ("00" if signature[:2] != "00" else "01") + signature[2:]
    )

    result = audit_ledger(registry, ledger)

    assert result["integrity_ok"] is False
    assert result["accepted_attestation_count"] == 0
    assert "invalid Ed25519 signature" in result["rejected_attestations"][0]["reasons"]


def test_disclosed_held_out_task_is_rejected_as_contaminated() -> None:
    registry, ledger = _documents()
    ledger["task_disclosures"] = [
        {
            "task_id": "held-out-task-1",
            "suite_sha256": "22" * 32,
            "disclosed_at": "2026-08-16T08:15:00+00:00",
        }
    ]

    result = audit_ledger(registry, ledger)

    reasons = result["rejected_attestations"][0]["reasons"]
    assert "task was disclosed before the evaluation completed" in reasons


def test_challenge_cannot_be_reused_by_a_different_run() -> None:
    registry, ledger = _documents()
    _, second_ledger = _documents(
        task_id="held-out-task-2",
        run_id="external-run-2",
    )
    ledger["attestations"].append(second_ledger["attestations"][0])

    result = audit_ledger(registry, ledger)

    assert result["accepted_attestation_count"] == 1
    assert "challenge was reused by a different verifier or run" in result[
        "rejected_attestations"
    ][0]["reasons"]


def test_issue_challenge_is_fresh_bounded_and_non_destructive() -> None:
    original = {
        "schema_version": 1,
        "challenges": [],
        "task_disclosures": [],
        "attestations": [],
    }
    now = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)

    updated, challenge = issue_challenge(
        original,
        suite_id="suite-a",
        ttl_minutes=30,
        now=now,
    )

    assert original["challenges"] == []
    assert len(challenge.nonce) == 64
    assert challenge.issued_at == now.isoformat()
    assert challenge.expires_at == (now + timedelta(minutes=30)).isoformat()
    assert updated["challenges"][0]["nonce"] == challenge.nonce
