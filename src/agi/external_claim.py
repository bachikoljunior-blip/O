from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping

from .evaluation import CRITERION_KEYS, EvaluationPolicy, EvidenceRecord, evaluate_evidence
from .external_provenance import (
    ExternalAttestation,
    ExternalChallenge,
    ExternalEvidenceError,
    ExternalVerifier,
    TaskDisclosure,
    audit_external_ledger,
    bridge_external_attestation,
    sha256_json,
)


DEFAULT_MIN_INDEPENDENT_GROUPS_PER_CRITERION = 2
_REQUEST_KIND = "strict-independent-agi-evaluation-request"
_FORBIDDEN_REQUEST_KEYS = frozenset(
    {
        "bridge_secret",
        "private_key",
        "private_key_hex",
        "signing_key",
        "signing_key_hex",
        "hidden_seed",
        "expected",
        "expected_answer",
        "answers",
    }
)


def _failure_result(
    message: str,
    audit: Mapping[str, Any] | None = None,
    *,
    required_groups: int = DEFAULT_MIN_INDEPENDENT_GROUPS_PER_CRITERION,
) -> dict[str, Any]:
    return {
        "agi_claim_supported": False,
        "provenance_audit": dict(audit or {"clean": False, "error": message}),
        "core_evaluation": None,
        "external_group_checks": {
            key: {"passed": False, "count": 0, "required": required_groups, "groups": []}
            for key in CRITERION_KEYS
        },
        "evidence_records": [],
        "missing": list(CRITERION_KEYS),
        "claim": "AGI claim is not supported; external production evidence did not pass the provenance-to-claim bridge.",
        "error": message,
    }


def _is_lower_hex(value: str, length: int) -> bool:
    if len(value) != length or value.lower() != value:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True


def _contains_forbidden_request_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in _FORBIDDEN_REQUEST_KEYS:
                return True
            if _contains_forbidden_request_key(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_forbidden_request_key(item) for item in value)
    return False


def _evaluation_request_map(ledger: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Validate strict request manifests that externally signed results may bind to.

    The provenance layer already validates evaluator signatures, challenges, held-out timing, and replay.
    This additional gate makes the signed result identify the exact request that froze the system subject.
    A request digest therefore transitively binds repository, source commit, artifact digest, producer,
    six-criterion policy, and the default two-independent-group quorum without putting secrets in the ledger.
    """

    values = ledger.get("evaluation_requests")
    if not isinstance(values, list) or not values:
        raise ExternalEvidenceError(
            "ledger must include at least one strict evaluation request for subject binding"
        )
    strict_policy = asdict(EvaluationPolicy())
    strict_quorum = {
        "min_independent_groups_per_criterion": DEFAULT_MIN_INDEPENDENT_GROUPS_PER_CRITERION,
        "distinct_public_keys_required": True,
        "producer_may_not_be_evaluator": True,
        "bridge_attestor_must_be_third_identity": True,
    }
    requests: dict[str, Mapping[str, Any]] = {}
    for raw in values:
        if not isinstance(raw, Mapping):
            raise ExternalEvidenceError("evaluation request entry must be an object")
        if raw.get("schema_version") != 1 or raw.get("request_kind") != _REQUEST_KIND:
            raise ExternalEvidenceError("unsupported external evaluation request schema/kind")
        if _contains_forbidden_request_key(raw):
            raise ExternalEvidenceError("external evaluation request embeds forbidden secret/answer fields")
        subject = raw.get("subject")
        if not isinstance(subject, Mapping):
            raise ExternalEvidenceError("external evaluation request subject must be an object")
        repository = str(subject.get("repository", ""))
        commit_sha = str(subject.get("commit_sha", ""))
        artifact_sha256 = str(subject.get("artifact_sha256", ""))
        producer_id = str(subject.get("producer_id", ""))
        if (
            not repository.strip()
            or "/" not in repository
            or repository.startswith("/")
            or repository.endswith("/")
        ):
            raise ExternalEvidenceError("evaluation request subject.repository must be owner/name")
        if not _is_lower_hex(commit_sha, 40):
            raise ExternalEvidenceError("evaluation request commit_sha must be canonical lowercase 40-hex")
        if not _is_lower_hex(artifact_sha256, 64):
            raise ExternalEvidenceError("evaluation request artifact_sha256 must be canonical lowercase SHA-256")
        if not producer_id.strip():
            raise ExternalEvidenceError("evaluation request producer_id must be non-empty")
        if raw.get("required_criteria") != list(CRITERION_KEYS):
            raise ExternalEvidenceError("evaluation request does not preserve the six-criterion gate")
        if raw.get("core_evaluation_policy") != strict_policy:
            raise ExternalEvidenceError("evaluation request does not preserve the conservative core policy")
        quorum = raw.get("external_quorum")
        if not isinstance(quorum, Mapping) or dict(quorum) != strict_quorum:
            raise ExternalEvidenceError("evaluation request does not preserve the strict external quorum")
        digest = raw.get("request_sha256")
        if not isinstance(digest, str) or not _is_lower_hex(digest, 64):
            raise ExternalEvidenceError("evaluation request request_sha256 is missing or malformed")
        payload = dict(raw)
        payload.pop("request_sha256", None)
        if sha256_json(payload) != digest:
            raise ExternalEvidenceError("evaluation request request_sha256 does not match canonical request")
        if digest in requests:
            raise ExternalEvidenceError("duplicate evaluation request digest")
        requests[digest] = raw
    return requests


def _bound_request(
    attestation: ExternalAttestation,
    requests: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any]:
    digest = attestation.metadata.get("evaluation_request_sha256")
    if not isinstance(digest, str) or not _is_lower_hex(digest, 64):
        raise ExternalEvidenceError(
            "external attestation metadata must include signed evaluation_request_sha256"
        )
    request = requests.get(digest)
    if request is None:
        raise ExternalEvidenceError(
            "external attestation references an evaluation request not present in the ledger"
        )
    subject = request["subject"]
    assert isinstance(subject, Mapping)
    if attestation.artifact_sha256 != str(subject["artifact_sha256"]):
        raise ExternalEvidenceError(
            "external attestation artifact does not match its bound evaluation request"
        )
    if attestation.producer != str(subject["producer_id"]):
        raise ExternalEvidenceError(
            "external attestation producer does not match its bound evaluation request"
        )
    return request


def _registry_map(registry: Mapping[str, Any]) -> dict[str, ExternalVerifier]:
    if registry.get("schema_version") != 1 or not isinstance(registry.get("verifiers"), list):
        raise ExternalEvidenceError("verifier registry must have schema_version 1 and verifiers array")
    result: dict[str, ExternalVerifier] = {}
    public_key_groups: dict[str, str] = {}
    for raw in registry["verifiers"]:
        if not isinstance(raw, Mapping):
            raise ExternalEvidenceError("verifier entry must be an object")
        verifier = ExternalVerifier.from_mapping(raw)
        if verifier.verifier_id in result:
            raise ExternalEvidenceError(f"duplicate verifier_id: {verifier.verifier_id}")
        previous_group = public_key_groups.get(verifier.public_key_hex)
        if previous_group is not None and previous_group != verifier.independent_group:
            raise ExternalEvidenceError(
                "one verifier public key cannot represent multiple independent groups"
            )
        public_key_groups[verifier.public_key_hex] = verifier.independent_group
        result[verifier.verifier_id] = verifier
    return result


def _challenge_map(ledger: Mapping[str, Any]) -> dict[str, ExternalChallenge]:
    values = ledger.get("challenges")
    if not isinstance(values, list):
        raise ExternalEvidenceError("ledger challenges must be an array")
    result: dict[str, ExternalChallenge] = {}
    for raw in values:
        if not isinstance(raw, Mapping):
            raise ExternalEvidenceError("challenge entry must be an object")
        challenge = ExternalChallenge.from_mapping(raw)
        if challenge.nonce in result:
            raise ExternalEvidenceError(f"duplicate challenge nonce: {challenge.nonce}")
        result[challenge.nonce] = challenge
    return result


def _disclosures(ledger: Mapping[str, Any]) -> list[TaskDisclosure]:
    values = ledger.get("disclosures")
    if not isinstance(values, list):
        raise ExternalEvidenceError("ledger disclosures must be an array")
    result: list[TaskDisclosure] = []
    for raw in values:
        if not isinstance(raw, Mapping):
            raise ExternalEvidenceError("disclosure entry must be an object")
        result.append(TaskDisclosure.from_mapping(raw))
    return result


def _attestations(ledger: Mapping[str, Any]) -> list[ExternalAttestation]:
    values = ledger.get("attestations")
    if not isinstance(values, list):
        raise ExternalEvidenceError("ledger attestations must be an array")
    result: list[ExternalAttestation] = []
    for raw in values:
        if not isinstance(raw, Mapping):
            raise ExternalEvidenceError("attestation entry must be an object")
        result.append(ExternalAttestation.from_mapping(raw))
    return result


def evaluate_external_ledger_claim(
    ledger: Mapping[str, Any],
    registry: Mapping[str, Any],
    *,
    bridge_attestor_id: str,
    bridge_secret: str,
    policy: EvaluationPolicy | None = None,
    min_independent_groups_per_criterion: int = DEFAULT_MIN_INDEPENDENT_GROUPS_PER_CRITERION,
) -> dict[str, Any]:
    """Bridge independently signed production results into the six-criterion claim gate.

    The entire external ledger is audited first. Any malformed, replayed, contaminated,
    unregistered, or signature-invalid attestation fails the claim closed rather than being
    silently dropped. Only a clean ledger is bridged into the existing HMAC trust boundary.
    The bridge secret is supplied out of band and is never included in the returned report.

    Every accepted result must additionally carry a signed ``evaluation_request_sha256`` metadata
    field resolving to a strict request manifest stored in the ledger. The request is independently
    revalidated here and binds the result to the exact repository commit, artifact digest, producer,
    six-criterion policy, and default two-group quorum. This prevents a valid result artifact from being
    silently reassigned to a different source commit or evaluation handoff.

    Distinct external evaluator organizations are counted from the verified public-key registry,
    not from bridge keys. The strict default requires two independent groups per criterion, and a
    single Ed25519 public key cannot be relabeled as multiple independent groups. Callers may raise
    the quorum further for stronger production policies; lowering it is an explicit non-default
    policy choice and must never be mistaken for the repository's strict AGI claim gate.
    """

    if min_independent_groups_per_criterion < 1:
        raise ValueError("min_independent_groups_per_criterion must be at least 1")
    if not isinstance(bridge_attestor_id, str) or not bridge_attestor_id.strip():
        return _failure_result(
            "bridge_attestor_id must be non-empty",
            required_groups=min_independent_groups_per_criterion,
        )
    if not isinstance(bridge_secret, str) or not bridge_secret:
        return _failure_result(
            "bridge_secret must be non-empty",
            required_groups=min_independent_groups_per_criterion,
        )

    try:
        audit = audit_external_ledger(ledger, registry)
    except ExternalEvidenceError as exc:
        return _failure_result(
            str(exc),
            required_groups=min_independent_groups_per_criterion,
        )
    if not audit.get("clean"):
        return _failure_result(
            "external provenance audit is not clean",
            audit,
            required_groups=min_independent_groups_per_criterion,
        )

    try:
        requests = _evaluation_request_map(ledger)
        verifiers = _registry_map(registry)
        challenges = _challenge_map(ledger)
        disclosures = _disclosures(ledger)
        attestations = _attestations(ledger)
        records: list[EvidenceRecord] = []
        groups_by_criterion: dict[str, set[str]] = {key: set() for key in CRITERION_KEYS}
        attestation_refs: list[dict[str, Any]] = []
        for attestation in attestations:
            verifier = verifiers.get(attestation.verifier_id)
            challenge = challenges.get(attestation.challenge_nonce)
            if verifier is None or challenge is None:
                raise ExternalEvidenceError("clean audit contained unresolved verifier or challenge")
            request = _bound_request(attestation, requests)
            subject = request["subject"]
            assert isinstance(subject, Mapping)
            record = bridge_external_attestation(
                attestation,
                verifier=verifier,
                challenge=challenge,
                disclosures=disclosures,
                bridge_attestor_id=bridge_attestor_id,
                bridge_secret=bridge_secret,
            )
            records.append(record)
            groups_by_criterion[attestation.criterion].add(verifier.independent_group)
            attestation_refs.append(
                {
                    "attestation_id": attestation.attestation_id(),
                    "criterion": attestation.criterion,
                    "task_id": attestation.task_id,
                    "run_id": attestation.run_id,
                    "verifier_id": verifier.verifier_id,
                    "independent_group": verifier.independent_group,
                    "success": attestation.success,
                    "evaluation_request_sha256": request["request_sha256"],
                    "system_repository": subject["repository"],
                    "system_commit_sha": subject["commit_sha"],
                    "artifact_sha256": subject["artifact_sha256"],
                }
            )
    except ExternalEvidenceError as exc:
        return _failure_result(
            str(exc),
            audit,
            required_groups=min_independent_groups_per_criterion,
        )

    selected_policy = policy or EvaluationPolicy()
    core = evaluate_evidence(
        records,
        selected_policy,
        trusted_attestors={bridge_attestor_id: bridge_secret},
    )
    group_checks = {
        key: {
            "passed": len(groups_by_criterion[key]) >= min_independent_groups_per_criterion,
            "count": len(groups_by_criterion[key]),
            "required": min_independent_groups_per_criterion,
            "groups": sorted(groups_by_criterion[key]),
        }
        for key in CRITERION_KEYS
    }
    groups_pass = all(item["passed"] for item in group_checks.values())
    supported = bool(audit.get("clean")) and bool(core["agi_claim_supported"]) and groups_pass
    missing = sorted(
        set(core.get("missing", []))
        | {key for key, item in group_checks.items() if not item["passed"]}
    )
    return {
        "agi_claim_supported": supported,
        "provenance_audit": audit,
        "core_evaluation": core,
        "external_group_checks": group_checks,
        "evidence_records": [asdict(record) for record in records],
        "external_attestations": attestation_refs,
        "missing": missing,
        "claim": (
            "AGI claim is supported by the supplied independently signed production evidence under this policy."
            if supported
            else "AGI claim is not supported; continue capability work and independently signed production evaluation."
        ),
        "claim_boundary": (
            "This result can support a claim only when every upstream external signature, challenge, held-out status, "
            "request-to-system binding, bridge trust secret, production result, and policy threshold is independently "
            "auditable. The default quorum requires two cryptographically distinct external evaluator groups per criterion."
        ),
    }
