from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime

import pytest

from agi.evaluation import verify_independent_attestation
from agi.external_provenance import (
    ExternalAttestation,
    ExternalChallenge,
    ExternalEvidenceError,
    ExternalVerifier,
    TaskDisclosure,
    _BASE,
    _L,
    _encode_point,
    _scalar_multiply,
    audit_external_ledger,
    bridge_external_attestation,
    build_parser,
    issue_challenge,
    finalize_external_attestation,
    main as external_evidence_main,
    prepare_external_attestation_payload,
    verify_ed25519,
    verify_external_attestation,
)


def _sign(seed: bytes, message: bytes) -> tuple[str, str]:
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    prefix = digest[32:]
    public_key = _encode_point(_scalar_multiply(_BASE, scalar))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % _L
    encoded_r = _encode_point(_scalar_multiply(_BASE, r))
    challenge = int.from_bytes(hashlib.sha512(encoded_r + public_key + message).digest(), "little") % _L
    s = (r + challenge * scalar) % _L
    return public_key.hex(), (encoded_r + s.to_bytes(32, "little")).hex()


def _challenge() -> ExternalChallenge:
    return ExternalChallenge(
        nonce="11" * 32,
        suite_id="hidden-suite-a",
        suite_sha256="22" * 32,
        issued_at="2026-08-16T09:00:00+00:00",
        expires_at="2026-08-16T10:00:00+00:00",
    )


def _signed_attestation(*, seed: bytes = b"A" * 32, run_id: str = "run-1", repeat_index: int = 0):
    challenge = _challenge()
    unsigned = ExternalAttestation(
        schema_version=1,
        criterion="robustness",
        success=True,
        source_kind="independent_reproduction",
        task_id="heldout-tool-failure-17",
        domain="software-operations",
        run_id=run_id,
        producer="system-under-test",
        verifier_id="lab-a-key-1",
        suite_id=challenge.suite_id,
        suite_version="1.0",
        suite_sha256=challenge.suite_sha256,
        artifact_sha256="33" * 32,
        result_sha256="44" * 32,
        challenge_nonce=challenge.nonce,
        evaluated_at="2026-08-16T09:30:00+00:00",
        repeat_index=repeat_index,
        metadata={"runner": "external"},
        signature_algorithm="ed25519",
        signature_hex="00" * 64,
    )
    public_key_hex, signature_hex = _sign(seed, unsigned.payload_bytes())
    attestation = replace(unsigned, signature_hex=signature_hex)
    verifier = ExternalVerifier(
        verifier_id=attestation.verifier_id,
        independent_group="independent-lab-a",
        public_key_hex=public_key_hex,
        allowed_source_kinds=("independent_reproduction",),
        valid_from="2026-08-16T08:00:00+00:00",
        valid_until="2026-08-17T08:00:00+00:00",
    )
    return attestation, verifier, challenge


def _ledger(attestations, verifier, challenge, disclosures=()):
    return {
        "schema_version": 1,
        "challenges": [asdict(challenge)],
        "disclosures": [asdict(item) for item in disclosures],
        "attestations": [asdict(item) for item in attestations],
    }, {
        "schema_version": 1,
        "verifiers": [asdict(verifier)],
    }


def _unsigned_statement(attestation: ExternalAttestation) -> dict:
    value = asdict(attestation)
    value.pop("signature_hex")
    return value


def test_secret_free_detached_signature_round_trip_uses_exact_canonical_payload():
    signed, verifier, _ = _signed_attestation()
    statement = _unsigned_statement(signed)

    placeholder, payload = prepare_external_attestation_payload(statement)
    assert payload == signed.payload_bytes()
    assert placeholder.signature_hex == "00" * 64

    finalized = finalize_external_attestation(
        statement,
        public_key_hex=verifier.public_key_hex,
        signature_hex=signed.signature_hex,
    )
    assert finalized == signed


def test_detached_signature_packaging_fails_closed_without_mutating_input():
    signed, verifier, _ = _signed_attestation()
    statement = _unsigned_statement(signed)
    original = dict(statement)

    with pytest.raises(ExternalEvidenceError, match="does not match canonical payload"):
        finalize_external_attestation(
            {**statement, "result_sha256": "55" * 32},
            public_key_hex=verifier.public_key_hex,
            signature_hex=signed.signature_hex,
        )
    with pytest.raises(ExternalEvidenceError, match="omit signature_hex"):
        prepare_external_attestation_payload({**statement, "signature_hex": signed.signature_hex})
    with pytest.raises(ExternalEvidenceError, match="forbidden secret fields"):
        prepare_external_attestation_payload({**statement, "private_key_hex": "not-allowed"})
    with pytest.raises(ExternalEvidenceError, match="forbidden secret fields"):
        prepare_external_attestation_payload(
            {**statement, "metadata": {"private_key_hex": "must-stay-outside"}}
        )
    with pytest.raises(ExternalEvidenceError, match="forbidden secret fields"):
        prepare_external_attestation_payload(
            {**statement, "metadata": {"evaluator-access-token": "must-stay-outside"}}
        )
    with pytest.raises(ExternalEvidenceError, match="forbidden secret fields"):
        prepare_external_attestation_payload(
            {**statement, "metadata": {"hidden_answers": ["must-stay-outside"]}}
        )
    with pytest.raises(ExternalEvidenceError, match="forbidden secret fields"):
        prepare_external_attestation_payload(
            {**statement, "metadata": {"suite_seed": "must-stay-outside"}}
        )
    for disguised_key in (
        "Private Key",
        "privateKeyHex",
        "evaluator.secret-key",
        "private_key_material",
        "apiTokenValue",
        "secretValue",
        "hiddenAnswerDigest",
    ):
        with pytest.raises(ExternalEvidenceError, match="forbidden secret fields"):
            prepare_external_attestation_payload(
                {**statement, "metadata": {disguised_key: "must-stay-outside"}}
            )
    missing = dict(statement)
    missing.pop("suite_id")
    with pytest.raises(ExternalEvidenceError, match="missing unsigned attestation fields: suite_id"):
        prepare_external_attestation_payload(missing)
    with pytest.raises(ExternalEvidenceError, match="unknown unsigned attestation fields: extra"):
        prepare_external_attestation_payload({**statement, "extra": "not-covered"})
    with pytest.raises(ExternalEvidenceError, match="must already use canonical field values"):
        prepare_external_attestation_payload(
            {**statement, "artifact_sha256": "AA" * 32}
        )
    with pytest.raises(ExternalEvidenceError, match="must be JSON integers"):
        prepare_external_attestation_payload({**statement, "repeat_index": True})
    with pytest.raises(ExternalEvidenceError, match="string fields have invalid types"):
        prepare_external_attestation_payload({**statement, "suite_id": 7})
    assert statement == original


def test_payload_and_finalize_cli_emit_only_public_verifiable_artifacts(tmp_path, monkeypatch, capsys):
    signed, verifier, _ = _signed_attestation()
    statement = _unsigned_statement(signed)
    statement_path = tmp_path / "result-statement.json"
    payload_path = tmp_path / "payload.json"
    attestation_path = tmp_path / "attestation.json"
    statement_path.write_text(json.dumps(statement), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["agi-external-evidence", "payload", str(statement_path), "--output", str(payload_path)],
    )
    external_evidence_main()
    payload_report = json.loads(capsys.readouterr().out)
    assert payload_path.read_bytes() == signed.payload_bytes()
    assert payload_report["claim_boundary"] == "unsigned payload only; not external evidence"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agi-external-evidence",
            "finalize",
            str(statement_path),
            "--public-key-hex",
            verifier.public_key_hex,
            "--signature-hex",
            signed.signature_hex,
            "--output",
            str(attestation_path),
        ],
    )
    external_evidence_main()
    final_report = json.loads(capsys.readouterr().out)
    assert ExternalAttestation.from_mapping(
        json.loads(attestation_path.read_text(encoding="utf-8"))
    ) == signed
    assert final_report["attestation_id"] == signed.attestation_id()
    assert "private" not in statement_path.read_text(encoding="utf-8").lower()


def test_payload_and_finalize_cli_have_no_private_key_or_ledger_mutation_inputs():
    parser = build_parser()
    subparsers = parser._subparsers._group_actions[0].choices
    actions = subparsers["payload"]._actions + subparsers["finalize"]._actions
    option_strings = {option for action in actions for option in action.option_strings}

    assert "--private-key" not in option_strings
    assert "--private-key-hex" not in option_strings
    assert "--signing-key" not in option_strings
    assert "--ledger" not in option_strings
    assert "--registry" not in option_strings


def test_finalize_cli_does_not_write_on_signature_mismatch(tmp_path, monkeypatch):
    signed, verifier, _ = _signed_attestation()
    statement_path = tmp_path / "result-statement.json"
    output_path = tmp_path / "must-not-exist.json"
    statement_path.write_text(json.dumps(_unsigned_statement(signed)), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agi-external-evidence",
            "finalize",
            str(statement_path),
            "--public-key-hex",
            verifier.public_key_hex,
            "--signature-hex",
            "00" * 64,
            "--output",
            str(output_path),
        ],
    )
    with pytest.raises(ExternalEvidenceError, match="does not match canonical payload"):
        external_evidence_main()
    assert not output_path.exists()

    output_path.write_text("existing-output-must-survive\n", encoding="utf-8")
    with pytest.raises(ExternalEvidenceError, match="does not match canonical payload"):
        external_evidence_main()
    assert output_path.read_text(encoding="utf-8") == "existing-output-must-survive\n"


def test_rfc8032_vector_verifies_and_modified_message_fails():
    public_key = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    signature = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )
    assert verify_ed25519(public_key, b"", signature) is True
    assert verify_ed25519(public_key, b"x", signature) is False


def test_valid_external_attestation_bridges_into_existing_strict_claim_attestation():
    attestation, verifier, challenge = _signed_attestation()
    decision = verify_external_attestation(attestation, verifier=verifier, challenge=challenge)
    assert decision.accepted is True
    record = bridge_external_attestation(
        attestation,
        verifier=verifier,
        challenge=challenge,
        bridge_attestor_id="evidence-bridge-a",
        bridge_secret="bridge-test-secret",
    )
    assert record.independent is True
    assert record.tier == "production"
    assert record.evaluator == verifier.independent_group
    assert record.producer == "system-under-test"
    assert record.provenance_ref.startswith("ed25519:")
    assert attestation.attestation_id() in record.provenance_ref
    assert verify_independent_attestation(
        record, {"evidence-bridge-a": "bridge-test-secret"}
    ) is True


def test_bridge_hmac_binds_external_provenance_reference():
    attestation, verifier, challenge = _signed_attestation()
    record = bridge_external_attestation(
        attestation,
        verifier=verifier,
        challenge=challenge,
        bridge_attestor_id="evidence-bridge-a",
        bridge_secret="bridge-test-secret",
    )
    tampered = replace(record, provenance_ref=record.provenance_ref.replace("result=44", "result=55"))
    assert verify_independent_attestation(
        tampered, {"evidence-bridge-a": "bridge-test-secret"}
    ) is False


def test_signature_tampering_artifact_and_result_substitution_fail_closed():
    attestation, verifier, challenge = _signed_attestation()
    for tampered in (
        replace(attestation, artifact_sha256="55" * 32),
        replace(attestation, result_sha256="66" * 32),
        replace(attestation, success=False),
    ):
        decision = verify_external_attestation(tampered, verifier=verifier, challenge=challenge)
        assert decision.accepted is False
        assert "ed25519 signature verification failed" in decision.reasons


def test_disclosure_before_evaluation_contaminates_heldout_result():
    attestation, verifier, challenge = _signed_attestation()
    disclosure = TaskDisclosure(
        task_id=attestation.task_id,
        suite_sha256=attestation.suite_sha256,
        disclosed_at="2026-08-16T09:29:59+00:00",
    )
    decision = verify_external_attestation(
        attestation,
        verifier=verifier,
        challenge=challenge,
        disclosures=[disclosure],
    )
    assert decision.accepted is False
    assert "held-out task was disclosed before evaluation completed" in decision.reasons


def test_challenge_window_and_suite_binding_fail_closed():
    attestation, verifier, challenge = _signed_attestation()
    expired = replace(challenge, expires_at="2026-08-16T09:20:00+00:00")
    decision = verify_external_attestation(attestation, verifier=verifier, challenge=expired)
    assert decision.accepted is False
    assert "evaluation occurred outside challenge validity window" in decision.reasons

    wrong_suite = replace(challenge, suite_sha256="99" * 32)
    decision = verify_external_attestation(attestation, verifier=verifier, challenge=wrong_suite)
    assert decision.accepted is False
    assert "challenge suite binding mismatch" in decision.reasons


def test_ledger_rejects_challenge_rebinding_and_duplicate_repeat_inflation():
    first, verifier, challenge = _signed_attestation()
    duplicate_repeat, _, _ = _signed_attestation(seed=b"A" * 32, run_id="run-1", repeat_index=0)
    ledger, registry = _ledger([first, duplicate_repeat], verifier, challenge)
    report = audit_external_ledger(ledger, registry)
    assert report["clean"] is False
    assert report["accepted_count"] == 1
    assert report["rejected_count"] == 1
    assert any(
        reason in {"duplicate attestation replay", "duplicate task repeat index"}
        for reason in report["decisions"][1]["reasons"]
    )

    second = replace(first, run_id="other-run", signature_hex="00" * 64)
    public, signature = _sign(b"A" * 32, second.payload_bytes())
    assert public == verifier.public_key_hex
    second = replace(second, signature_hex=signature)
    ledger, registry = _ledger([first, second], verifier, challenge)
    report = audit_external_ledger(ledger, registry)
    assert report["clean"] is False
    assert "challenge replayed for a different verifier, run or suite" in report["decisions"][1]["reasons"]


def test_bridge_requires_third_identity_and_valid_external_signature():
    attestation, verifier, challenge = _signed_attestation()
    with pytest.raises(ExternalEvidenceError, match="third identity"):
        bridge_external_attestation(
            attestation,
            verifier=verifier,
            challenge=challenge,
            bridge_attestor_id=verifier.independent_group,
            bridge_secret="secret",
        )
    with pytest.raises(ExternalEvidenceError, match="external attestation rejected"):
        bridge_external_attestation(
            replace(attestation, signature_hex="00" * 64),
            verifier=verifier,
            challenge=challenge,
            bridge_attestor_id="bridge",
            bridge_secret="secret",
        )


def test_issue_challenge_has_fresh_nonce_and_bounded_expiry():
    ledger = {"schema_version": 1, "challenges": [], "disclosures": [], "attestations": []}
    now = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
    first = issue_challenge(
        ledger,
        suite_id="suite-a",
        suite_sha256="77" * 32,
        ttl_minutes=15,
        now=now,
    )
    second = issue_challenge(
        ledger,
        suite_id="suite-a",
        suite_sha256="77" * 32,
        ttl_minutes=15,
        now=now,
    )
    assert first.nonce != second.nonce
    assert first.issued_at == "2026-08-16T09:00:00+00:00"
    assert first.expires_at == "2026-08-16T09:15:00+00:00"
    assert len(ledger["challenges"]) == 2
