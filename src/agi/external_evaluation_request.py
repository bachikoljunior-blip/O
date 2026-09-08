from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .evaluation import CRITERION_KEYS, EvaluationPolicy
from .external_claim import DEFAULT_MIN_INDEPENDENT_GROUPS_PER_CRITERION


REQUEST_KIND = "strict-independent-agi-evaluation-request"
FORBIDDEN_SECRET_KEYS = frozenset(
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
ALLOWED_EXTERNAL_SOURCE_KINDS = (
    "external_test",
    "independent_reproduction",
    "human_audit",
)


class ExternalEvaluationRequestError(ValueError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


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


def build_external_evaluation_request(
    *,
    repository: str,
    commit_sha: str,
    artifact_sha256: str,
    system_manifest_sha256: str,
    producer_id: str,
) -> dict[str, Any]:
    """Build a secret-free handoff manifest for the repository's strict external AGI gate.

    The request binds an exact system source commit, tracked-source artifact digest, and public
    runtime/system manifest digest to the current conservative six-criterion policy. Independently
    signed attestations must report the runtime manifest they actually observed, allowing the claim
    bridge to reject a producer declaration that was not the runtime under evaluation. The request does
    not generate a hidden suite, verifier identity, challenge, signature, bridge secret, or success
    result; those remain under independent control. This is operational scaffolding only, not evidence.
    """

    request: dict[str, Any] = {
        "schema_version": 1,
        "request_kind": REQUEST_KIND,
        "subject": {
            "repository": repository,
            "commit_sha": commit_sha,
            "artifact_sha256": artifact_sha256,
            "system_manifest_sha256": system_manifest_sha256,
            "producer_id": producer_id,
        },
        "required_criteria": list(CRITERION_KEYS),
        "core_evaluation_policy": asdict(EvaluationPolicy()),
        "external_quorum": {
            "min_independent_groups_per_criterion": DEFAULT_MIN_INDEPENDENT_GROUPS_PER_CRITERION,
            "distinct_public_keys_required": True,
            "producer_may_not_be_evaluator": True,
            "bridge_attestor_must_be_third_identity": True,
        },
        "provenance_protocol": {
            "schema_version": 1,
            "signature_algorithm": "ed25519",
            "allowed_source_kinds": list(ALLOWED_EXTERNAL_SOURCE_KINDS),
            "hidden_suite_frozen_before_run": True,
            "fresh_challenge_issued_before_evaluation": True,
            "challenge_bound_to_suite_sha256": True,
            "raw_artifact_sha256_required": True,
            "result_sha256_required": True,
            "heldout_disclosure_history_required": True,
            "external_ledger_audit_required": True,
            "bridge_secret_out_of_band_required": True,
            "runtime_system_manifest_required": True,
            "signed_observed_system_manifest_required": True,
        },
        "handoff": {
            "evaluator_must_supply": [
                "independently controlled verifier identity and Ed25519 public key registration",
                "independently frozen hidden-suite id/version/hash",
                "fresh suite-bound challenge",
                "raw evaluation artifacts and result digests",
                "signed production attestations with preserved failures",
                "signed attestation metadata observed_system_manifest_sha256 identifying the runtime manifest actually observed during evaluation",
            ],
            "repository_must_not_supply": [
                "evaluator private key",
                "hidden task answers",
                "bridge trust secret",
                "fabricated production results",
            ],
        },
        "claim_boundary": (
            "This manifest is a request for independently controlled production evaluation. It is not "
            "an attestation, a production result, or AGI evidence. A claim remains blocked until the "
            "external ledger passes provenance audit, the strict two-group quorum is met for every "
            "criterion, each signed attestation reports the same observed runtime-system manifest bound "
            "by the request, and the conservative core evaluation policy passes with no unresolved "
            "blocking production failures."
        ),
    }
    validate_external_evaluation_request(request, require_digest=False)
    request["request_sha256"] = _digest(request)
    return request


def validate_external_evaluation_request(
    request: Mapping[str, Any],
    *,
    require_digest: bool = True,
) -> dict[str, Any]:
    if request.get("schema_version") != 1 or request.get("request_kind") != REQUEST_KIND:
        raise ExternalEvaluationRequestError("unsupported external evaluation request schema/kind")
    forbidden = _forbidden_paths(request)
    if forbidden:
        raise ExternalEvaluationRequestError(
            "request embeds forbidden secret/answer fields: " + ", ".join(forbidden)
        )

    subject = request.get("subject")
    if not isinstance(subject, Mapping):
        raise ExternalEvaluationRequestError("subject must be an object")
    repository = str(subject.get("repository", ""))
    commit_sha = str(subject.get("commit_sha", ""))
    artifact_sha256 = str(subject.get("artifact_sha256", ""))
    system_manifest_sha256 = str(subject.get("system_manifest_sha256", ""))
    producer_id = str(subject.get("producer_id", ""))
    if not repository.strip() or "/" not in repository or repository.startswith("/") or repository.endswith("/"):
        raise ExternalEvaluationRequestError("subject.repository must be owner/name")
    if not _is_lower_hex(commit_sha, 40):
        raise ExternalEvaluationRequestError("subject.commit_sha must be canonical lowercase 40-hex")
    if not _is_lower_hex(artifact_sha256, 64):
        raise ExternalEvaluationRequestError(
            "subject.artifact_sha256 must be canonical lowercase SHA-256"
        )
    if not _is_lower_hex(system_manifest_sha256, 64):
        raise ExternalEvaluationRequestError(
            "subject.system_manifest_sha256 must be canonical lowercase SHA-256"
        )
    if not producer_id.strip():
        raise ExternalEvaluationRequestError("subject.producer_id must be non-empty")

    if request.get("required_criteria") != list(CRITERION_KEYS):
        raise ExternalEvaluationRequestError(
            "required_criteria must exactly match the repository six-criterion gate"
        )
    strict_policy = asdict(EvaluationPolicy())
    if request.get("core_evaluation_policy") != strict_policy:
        raise ExternalEvaluationRequestError(
            "core_evaluation_policy must exactly match the conservative repository default"
        )

    quorum = request.get("external_quorum")
    if not isinstance(quorum, Mapping):
        raise ExternalEvaluationRequestError("external_quorum must be an object")
    expected_quorum = {
        "min_independent_groups_per_criterion": DEFAULT_MIN_INDEPENDENT_GROUPS_PER_CRITERION,
        "distinct_public_keys_required": True,
        "producer_may_not_be_evaluator": True,
        "bridge_attestor_must_be_third_identity": True,
    }
    if dict(quorum) != expected_quorum:
        raise ExternalEvaluationRequestError(
            "external_quorum must exactly preserve the strict independent-group identity gate"
        )

    provenance = request.get("provenance_protocol")
    if not isinstance(provenance, Mapping):
        raise ExternalEvaluationRequestError("provenance_protocol must be an object")
    required_provenance = {
        "schema_version": 1,
        "signature_algorithm": "ed25519",
        "allowed_source_kinds": list(ALLOWED_EXTERNAL_SOURCE_KINDS),
        "hidden_suite_frozen_before_run": True,
        "fresh_challenge_issued_before_evaluation": True,
        "challenge_bound_to_suite_sha256": True,
        "raw_artifact_sha256_required": True,
        "result_sha256_required": True,
        "heldout_disclosure_history_required": True,
        "external_ledger_audit_required": True,
        "bridge_secret_out_of_band_required": True,
        "runtime_system_manifest_required": True,
        "signed_observed_system_manifest_required": True,
    }
    if dict(provenance) != required_provenance:
        raise ExternalEvaluationRequestError(
            "provenance_protocol must exactly preserve the strict provenance requirements"
        )

    handoff = request.get("handoff")
    if not isinstance(handoff, Mapping):
        raise ExternalEvaluationRequestError("handoff must be an object")
    evaluator_supply = handoff.get("evaluator_must_supply")
    repository_forbidden = handoff.get("repository_must_not_supply")
    if not isinstance(evaluator_supply, list) or len(evaluator_supply) < 6:
        raise ExternalEvaluationRequestError("handoff is missing evaluator-controlled requirements")
    if not isinstance(repository_forbidden, list) or len(repository_forbidden) < 4:
        raise ExternalEvaluationRequestError("handoff is missing repository non-control requirements")
    if not str(request.get("claim_boundary", "")).strip():
        raise ExternalEvaluationRequestError("claim_boundary must be non-empty")

    digest = request.get("request_sha256")
    payload = dict(request)
    payload.pop("request_sha256", None)
    expected_digest = _digest(payload)
    if require_digest:
        if not isinstance(digest, str) or not _is_lower_hex(digest, 64):
            raise ExternalEvaluationRequestError("request_sha256 is missing or malformed")
        if digest != expected_digest:
            raise ExternalEvaluationRequestError("request_sha256 does not match canonical request")
    return {
        "valid": True,
        "request_sha256": expected_digest,
        "repository": repository,
        "commit_sha": commit_sha,
        "artifact_sha256": artifact_sha256,
        "system_manifest_sha256": system_manifest_sha256,
        "producer_id": producer_id,
        "criteria": list(CRITERION_KEYS),
        "min_independent_groups_per_criterion": DEFAULT_MIN_INDEPENDENT_GROUPS_PER_CRITERION,
        "claim_boundary": (
            "Validation proves only that this handoff request preserves the repository's strict external "
            "evaluation requirements and requires signed observed runtime identity. It does not prove "
            "that any independent evaluation occurred or that the declared runtime was actually used."
        ),
    }


def _load_object(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ExternalEvaluationRequestError(f"{path} must contain a JSON object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agi-external-request",
        description=(
            "Create or validate a secret-free request for the repository's strict independent "
            "production AGI evaluation gate."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--repository", required=True)
    create.add_argument("--commit-sha", required=True)
    create.add_argument("--artifact-sha256", required=True)
    create.add_argument("--system-manifest-sha256", required=True)
    create.add_argument("--producer-id", required=True)
    create.add_argument("--output", type=Path, required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("request", type=Path)
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            request = build_external_evaluation_request(
                repository=args.repository,
                commit_sha=args.commit_sha,
                artifact_sha256=args.artifact_sha256,
                system_manifest_sha256=args.system_manifest_sha256,
                producer_id=args.producer_id,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(request, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            result: Mapping[str, Any] = request
        else:
            result = validate_external_evaluation_request(_load_object(args.request))
    except (ExternalEvaluationRequestError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
