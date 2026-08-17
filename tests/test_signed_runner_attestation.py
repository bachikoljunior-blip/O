from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agi.external_provenance import _BASE, _L, _encode_point, _scalar_multiply
from agi.external_tool_acquisition import (
    EXTERNAL_TOOL_SCOPE,
    _hidden_external_adapter,
    run_external_tool_acquisition_campaign,
)
from continual.attested_external_tools import (
    AttestedContractedExternalToolRegistry,
    ExternalRunnerAttestationError,
    ExternalToolRunner,
    RunnerIsolationAttestation,
)
from continual.signed_runner_attestation import (
    Ed25519RunnerAttestationVerifier,
    SignedRunnerTrustProof,
)


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
    signature_scalar = (nonce + challenge * scalar) % _L
    return public_key.hex(), (encoded_nonce + signature_scalar.to_bytes(32, "little")).hex()


def _signed_runner(seed: str):
    adapter_fn, adapter_sha256 = _hidden_external_adapter(seed)
    verifier_id = "external-runner-lab-key-1"
    runner_id = "isolated-runner-signed-1"
    runner_sha256 = hashlib.sha256(b"isolated-runner-signed-1-image").hexdigest()
    attestation = RunnerIsolationAttestation.create(
        verifier_id=verifier_id,
        runner_id=runner_id,
        runner_sha256=runner_sha256,
        adapter_sha256=adapter_sha256,
    )
    unsigned = SignedRunnerTrustProof(
        verifier_id=verifier_id,
        runner_id=runner_id,
        statement_sha256=attestation.statement_sha256,
        challenge_nonce="11" * 32,
        issued_at="2026-08-17T04:00:00+00:00",
        expires_at="2026-08-17T05:00:00+00:00",
        signature_hex="00" * 64,
    )
    public_key_hex, signature_hex = _sign(b"runner-verifier-test-key-seed!!", unsigned.payload_bytes())
    proof = replace(unsigned, signature_hex=signature_hex)
    verifier = Ed25519RunnerAttestationVerifier(
        trusted_public_keys={verifier_id: public_key_hex},
        proofs={(verifier_id, runner_id): proof},
        required_challenge_nonce="11" * 32,
        verification_time="2026-08-17T04:30:00+00:00",
    )
    runner = ExternalToolRunner(
        adapter_sha256=adapter_sha256,
        runner_sha256=runner_sha256,
        attestation=attestation,
        invoke=adapter_fn,
    )
    return adapter_fn, runner, proof, public_key_hex, verifier


def test_signed_runner_authorization_activates_exact_verified_candidate(runtime_repo: Path) -> None:
    seed = "signed-runner-valid-candidate"
    acquisition = run_external_tool_acquisition_campaign(runtime_repo, seed)
    adapter_fn, runner, proof, _public_key, verifier = _signed_runner(seed)
    registry = AttestedContractedExternalToolRegistry(
        runtime_repo,
        {acquisition["tool_id"]: runner},
        verifier,
    )

    descriptors = registry.descriptors(EXTERNAL_TOOL_SCOPE)
    assert len(descriptors) == 1
    assert descriptors[0]["runner_attestation_sha256"] == proof.statement_sha256
    query = {"signed": 4, "runner": 7}
    assert registry.apply(
        scope=EXTERNAL_TOOL_SCOPE,
        tool_id=acquisition["tool_id"],
        value=query,
    ) == adapter_fn(query)


def test_expired_or_challenge_rebound_signature_fails_before_runner_invocation(
    runtime_repo: Path,
) -> None:
    seed = "signed-runner-fail-closed-before-call"
    acquisition = run_external_tool_acquisition_campaign(runtime_repo, seed)
    adapter_fn, trusted_runner, proof, public_key_hex, _verifier = _signed_runner(seed)
    calls: list[Any] = []

    def counted(value: Any) -> str:
        calls.append(value)
        return adapter_fn(value)

    runner = ExternalToolRunner(
        adapter_sha256=trusted_runner.adapter_sha256,
        runner_sha256=trusted_runner.runner_sha256,
        attestation=trusted_runner.attestation,
        invoke=counted,
    )
    for verifier in (
        Ed25519RunnerAttestationVerifier(
            trusted_public_keys={proof.verifier_id: public_key_hex},
            proofs={(proof.verifier_id, proof.runner_id): proof},
            required_challenge_nonce="22" * 32,
            verification_time="2026-08-17T04:30:00+00:00",
        ),
        Ed25519RunnerAttestationVerifier(
            trusted_public_keys={proof.verifier_id: public_key_hex},
            proofs={(proof.verifier_id, proof.runner_id): proof},
            required_challenge_nonce=proof.challenge_nonce,
            verification_time="2026-08-17T05:00:01+00:00",
        ),
    ):
        registry = AttestedContractedExternalToolRegistry(
            runtime_repo,
            {acquisition["tool_id"]: runner},
            verifier,
        )
        with pytest.raises(ExternalRunnerAttestationError, match="not independently trusted"):
            registry.available(EXTERNAL_TOOL_SCOPE)
    assert calls == []


def test_tampered_signed_statement_is_not_authorized(runtime_repo: Path) -> None:
    seed = "signed-runner-tamper-statement"
    acquisition = run_external_tool_acquisition_campaign(runtime_repo, seed)
    _adapter_fn, runner, proof, public_key_hex, _verifier = _signed_runner(seed)
    tampered = replace(proof, statement_sha256="99" * 32)
    verifier = Ed25519RunnerAttestationVerifier(
        trusted_public_keys={proof.verifier_id: public_key_hex},
        proofs={(proof.verifier_id, proof.runner_id): tampered},
        required_challenge_nonce=proof.challenge_nonce,
        verification_time="2026-08-17T04:30:00+00:00",
    )
    registry = AttestedContractedExternalToolRegistry(
        runtime_repo,
        {acquisition["tool_id"]: runner},
        verifier,
    )
    with pytest.raises(ExternalRunnerAttestationError, match="not independently trusted"):
        registry.available(EXTERNAL_TOOL_SCOPE)
