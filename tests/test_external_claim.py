from __future__ import annotations

import hashlib
from dataclasses import asdict, replace

from agi.evaluation import CRITERION_KEYS, EvaluationPolicy
from agi.external_claim import (
    DEFAULT_MIN_INDEPENDENT_GROUPS_PER_CRITERION,
    evaluate_external_ledger_claim,
)
from agi.external_evaluation_request import build_external_evaluation_request
from agi.external_provenance import (
    ExternalAttestation,
    ExternalChallenge,
    ExternalVerifier,
    _BASE,
    _L,
    _encode_point,
    _scalar_multiply,
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
    challenge = int.from_bytes(
        hashlib.sha512(encoded_r + public_key + message).digest(), "little"
    ) % _L
    s = (r + challenge * scalar) % _L
    return public_key.hex(), (encoded_r + s.to_bytes(32, "little")).hex()


def _fixtures(
    *,
    failed_criterion: str | None = None,
    request_producer: str = "candidate-system",
    attestation_producer: str = "candidate-system",
    request_artifact: str = "33" * 32,
    attestation_artifact: str = "33" * 32,
):
    seed = b"E" * 32
    challenge = ExternalChallenge(
        nonce="11" * 32,
        suite_id="external-hidden-suite",
        suite_sha256="22" * 32,
        issued_at="2026-08-16T09:00:00+00:00",
        expires_at="2026-08-16T10:00:00+00:00",
    )
    request = build_external_evaluation_request(
        repository="example/system",
        commit_sha="ab" * 20,
        artifact_sha256=request_artifact,
        system_manifest_sha256="44" * 32,
        producer_id=request_producer,
    )
    attestations = []
    public_key_hex = ""
    for index, criterion in enumerate(CRITERION_KEYS, start=1):
        unsigned = ExternalAttestation(
            schema_version=1,
            criterion=criterion,
            success=criterion != failed_criterion,
            source_kind="independent_reproduction",
            task_id=f"external-{criterion}-task",
            domain=f"domain-{criterion}",
            run_id="external-run-1",
            producer=attestation_producer,
            verifier_id="lab-a-key-1",
            suite_id=challenge.suite_id,
            suite_version="1.0",
            suite_sha256=challenge.suite_sha256,
            artifact_sha256=attestation_artifact,
            result_sha256=f"{index + 16:02x}" * 32,
            challenge_nonce=challenge.nonce,
            evaluated_at="2026-08-16T09:30:00+00:00",
            repeat_index=0,
            metadata={
                "runner": "independent",
                "evaluation_request_sha256": request["request_sha256"],
            },
            signature_algorithm="ed25519",
            signature_hex="00" * 64,
        )
        public_key_hex, signature_hex = _sign(seed, unsigned.payload_bytes())
        attestations.append(replace(unsigned, signature_hex=signature_hex))
    verifier = ExternalVerifier(
        verifier_id="lab-a-key-1",
        independent_group="independent-lab-a",
        public_key_hex=public_key_hex,
        allowed_source_kinds=("independent_reproduction",),
        valid_from="2026-08-16T08:00:00+00:00",
        valid_until="2026-08-17T08:00:00+00:00",
    )
    ledger = {
        "schema_version": 1,
        "evaluation_requests": [request],
        "challenges": [asdict(challenge)],
        "disclosures": [],
        "attestations": [asdict(item) for item in attestations],
    }
    registry = {"schema_version": 1, "verifiers": [asdict(verifier)]}
    return ledger, registry


def _minimal_policy() -> EvaluationPolicy:
    return EvaluationPolicy(
        min_successes_per_criterion=1,
        min_distinct_tasks_per_criterion=1,
        min_distinct_runs_per_criterion=1,
        min_distinct_domains_per_criterion=1,
        min_independent_successes_per_criterion=1,
        min_distinct_independent_attestors_per_criterion=1,
        require_production_tier=True,
        require_verified_independent_attestation=True,
        disallow_unresolved_production_failures=True,
    )


def test_clean_signed_external_ledger_reaches_six_criterion_gate_with_explicit_single_group_policy() -> None:
    ledger, registry = _fixtures()

    report = evaluate_external_ledger_claim(
        ledger,
        registry,
        bridge_attestor_id="evidence-bridge",
        bridge_secret="test-secret",
        policy=_minimal_policy(),
        min_independent_groups_per_criterion=1,
    )

    assert report["provenance_audit"]["clean"] is True
    assert report["core_evaluation"]["agi_claim_supported"] is True
    assert report["agi_claim_supported"] is True
    assert report["missing"] == []
    assert len(report["evidence_records"]) == len(CRITERION_KEYS)
    assert all(
        check["groups"] == ["independent-lab-a"]
        for check in report["external_group_checks"].values()
    )
    assert all(
        item["system_commit_sha"] == "ab" * 20
        and item["system_repository"] == "example/system"
        and item["artifact_sha256"] == "33" * 32
        and item["system_manifest_sha256"] == "44" * 32
        for item in report["external_attestations"]
    )


def test_strict_default_requires_two_independent_groups_per_criterion() -> None:
    ledger, registry = _fixtures()

    report = evaluate_external_ledger_claim(
        ledger,
        registry,
        bridge_attestor_id="evidence-bridge",
        bridge_secret="test-secret",
        policy=_minimal_policy(),
    )

    assert DEFAULT_MIN_INDEPENDENT_GROUPS_PER_CRITERION == 2
    assert report["provenance_audit"]["clean"] is True
    assert report["core_evaluation"]["agi_claim_supported"] is True
    assert report["agi_claim_supported"] is False
    assert set(report["missing"]) == set(CRITERION_KEYS)
    assert all(
        check == {
            "passed": False,
            "count": 1,
            "required": 2,
            "groups": ["independent-lab-a"],
        }
        for check in report["external_group_checks"].values()
    )


def test_one_public_key_cannot_be_relabelled_as_multiple_independent_groups() -> None:
    ledger, registry = _fixtures()
    copied = dict(registry["verifiers"][0])
    copied["verifier_id"] = "lab-b-key-1"
    copied["independent_group"] = "independent-lab-b"
    registry["verifiers"].append(copied)

    report = evaluate_external_ledger_claim(
        ledger,
        registry,
        bridge_attestor_id="evidence-bridge",
        bridge_secret="test-secret",
        policy=_minimal_policy(),
    )

    assert report["agi_claim_supported"] is False
    assert report["core_evaluation"] is None
    assert "public key cannot represent multiple independent groups" in report["error"]


def test_invalid_signature_fails_closed_before_core_claim_evaluation() -> None:
    ledger, registry = _fixtures()
    ledger["attestations"][0]["result_sha256"] = "ff" * 32

    report = evaluate_external_ledger_claim(
        ledger,
        registry,
        bridge_attestor_id="evidence-bridge",
        bridge_secret="test-secret",
        policy=_minimal_policy(),
    )

    assert report["agi_claim_supported"] is False
    assert report["provenance_audit"]["clean"] is False
    assert report["core_evaluation"] is None
    assert report["evidence_records"] == []


def test_signed_request_digest_tampering_fails_provenance_before_claim() -> None:
    ledger, registry = _fixtures()
    ledger["attestations"][0]["metadata"]["evaluation_request_sha256"] = "ff" * 32

    report = evaluate_external_ledger_claim(
        ledger,
        registry,
        bridge_attestor_id="evidence-bridge",
        bridge_secret="test-secret",
        policy=_minimal_policy(),
    )

    assert report["agi_claim_supported"] is False
    assert report["provenance_audit"]["clean"] is False
    assert report["core_evaluation"] is None


def test_missing_or_tampered_request_manifest_fails_closed() -> None:
    ledger, registry = _fixtures()
    ledger["evaluation_requests"] = []
    missing = evaluate_external_ledger_claim(
        ledger,
        registry,
        bridge_attestor_id="evidence-bridge",
        bridge_secret="test-secret",
        policy=_minimal_policy(),
        min_independent_groups_per_criterion=1,
    )
    assert missing["agi_claim_supported"] is False
    assert "strict evaluation request" in missing["error"]

    ledger, registry = _fixtures()
    ledger["evaluation_requests"][0]["subject"]["commit_sha"] = "cd" * 20
    tampered = evaluate_external_ledger_claim(
        ledger,
        registry,
        bridge_attestor_id="evidence-bridge",
        bridge_secret="test-secret",
        policy=_minimal_policy(),
        min_independent_groups_per_criterion=1,
    )
    assert tampered["agi_claim_supported"] is False
    assert "request_sha256 does not match" in tampered["error"]


def test_request_without_runtime_manifest_binding_fails_closed() -> None:
    ledger, registry = _fixtures()
    request = ledger["evaluation_requests"][0]
    request["subject"].pop("system_manifest_sha256")
    request["request_sha256"] = __import__("agi.external_provenance", fromlist=["sha256_json"]).sha256_json(
        {key: value for key, value in request.items() if key != "request_sha256"}
    )

    report = evaluate_external_ledger_claim(
        ledger,
        registry,
        bridge_attestor_id="evidence-bridge",
        bridge_secret="test-secret",
        policy=_minimal_policy(),
        min_independent_groups_per_criterion=1,
    )

    assert report["agi_claim_supported"] is False
    assert "system_manifest_sha256" in report["error"]


def test_valid_signature_bound_to_mismatched_request_subject_fails_closed() -> None:
    ledger, registry = _fixtures(request_producer="other-system")
    producer_mismatch = evaluate_external_ledger_claim(
        ledger,
        registry,
        bridge_attestor_id="evidence-bridge",
        bridge_secret="test-secret",
        policy=_minimal_policy(),
        min_independent_groups_per_criterion=1,
    )
    assert producer_mismatch["provenance_audit"]["clean"] is True
    assert producer_mismatch["agi_claim_supported"] is False
    assert "producer does not match" in producer_mismatch["error"]

    ledger, registry = _fixtures(request_artifact="99" * 32)
    artifact_mismatch = evaluate_external_ledger_claim(
        ledger,
        registry,
        bridge_attestor_id="evidence-bridge",
        bridge_secret="test-secret",
        policy=_minimal_policy(),
        min_independent_groups_per_criterion=1,
    )
    assert artifact_mismatch["provenance_audit"]["clean"] is True
    assert artifact_mismatch["agi_claim_supported"] is False
    assert "artifact does not match" in artifact_mismatch["error"]


def test_valid_signed_production_failure_is_preserved_and_blocks_claim() -> None:
    ledger, registry = _fixtures(failed_criterion="robustness")

    report = evaluate_external_ledger_claim(
        ledger,
        registry,
        bridge_attestor_id="evidence-bridge",
        bridge_secret="test-secret",
        policy=_minimal_policy(),
        min_independent_groups_per_criterion=1,
    )

    assert report["provenance_audit"]["clean"] is True
    assert report["agi_claim_supported"] is False
    assert "robustness" in report["missing"]
    assert report["core_evaluation"]["criteria"]["robustness"]["counts"]["production_failures"] == 1
    robustness = [
        item for item in report["external_attestations"] if item["criterion"] == "robustness"
    ]
    assert robustness == [
        {
            "attestation_id": robustness[0]["attestation_id"],
            "criterion": "robustness",
            "task_id": "external-robustness-task",
            "run_id": "external-run-1",
            "verifier_id": "lab-a-key-1",
            "independent_group": "independent-lab-a",
            "success": False,
            "evaluation_request_sha256": ledger["evaluation_requests"][0]["request_sha256"],
            "system_repository": "example/system",
            "system_commit_sha": "ab" * 20,
            "artifact_sha256": "33" * 32,
            "system_manifest_sha256": "44" * 32,
        }
    ]


def test_external_group_threshold_is_separate_from_bridge_attestor_count() -> None:
    ledger, registry = _fixtures()

    report = evaluate_external_ledger_claim(
        ledger,
        registry,
        bridge_attestor_id="evidence-bridge",
        bridge_secret="test-secret",
        policy=_minimal_policy(),
        min_independent_groups_per_criterion=2,
    )

    assert report["core_evaluation"]["agi_claim_supported"] is True
    assert report["agi_claim_supported"] is False
    assert set(report["missing"]) == set(CRITERION_KEYS)
    assert all(check["count"] == 1 and not check["passed"] for check in report["external_group_checks"].values())
