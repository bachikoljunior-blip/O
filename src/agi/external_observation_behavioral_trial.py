from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


class ExternalObservationBehavioralTrialError(ValueError):
    """Raised when the bounded paired trial cannot remain fail-closed."""


TRIAL_ID = "external-observation-behavioral-effect-v1"
CASE_IDS = (
    "eligible-fresh-exact-head",
    "absent-observation",
    "expired-observation",
    "wrong-head-observation",
    "request-digest-invalid",
    "incomplete-required-job",
    "authority-conflicting-observation",
)
_DECISIONS = {"ALLOW", "HOLD"}
_HEX = set("0123456789abcdef")
_TOP_FIELDS = {
    "schema_version",
    "trial_id",
    "source_commit",
    "decision_time",
    "source_request",
    "source_receipt",
    "expected_binding",
    "cases",
    "replay_count",
    "protected_paths",
    "forbidden_effects",
    "claim_boundary",
}
_CASE_FIELDS = {"case_id", "mutation", "expected_decision", "expected_reasons"}


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExternalObservationBehavioralTrialError(
            f"{label} must be non-empty text"
        )
    return value


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ExternalObservationBehavioralTrialError(
            f"{label} must be a positive integer"
        )
    return value


def _hex(value: Any, length: int, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != length
        or any(character not in _HEX for character in value)
    ):
        raise ExternalObservationBehavioralTrialError(
            f"{label} must be lowercase {length}-hex"
        )
    return value


def _timestamp(value: Any, label: str) -> datetime:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExternalObservationBehavioralTrialError(
            f"{label} must be ISO-8601"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExternalObservationBehavioralTrialError(
            f"{label} must include a timezone"
        )
    return parsed.astimezone(UTC)


def _repository_path(root: Path, raw: Any, label: str) -> tuple[str, Path]:
    text = _text(raw, label)
    if "\\" in text:
        raise ExternalObservationBehavioralTrialError(
            f"{label} must use POSIX separators"
        )
    pure = PurePosixPath(text)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ExternalObservationBehavioralTrialError(
            f"{label} must be confined and repository-relative"
        )
    candidate = root / Path(*pure.parts)
    cursor = root
    if any(
        (cursor := cursor / part).is_symlink()
        for part in pure.parts
    ):
        raise ExternalObservationBehavioralTrialError(
            f"{label} must not traverse a symbolic link"
        )
    path = candidate.resolve()
    if path == root or root not in path.parents or not path.is_file():
        raise ExternalObservationBehavioralTrialError(
            f"{label} must name a regular repository file"
        )
    return pure.as_posix(), path


def _exact_text_list(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ExternalObservationBehavioralTrialError(
            f"{label} must be a{' non-empty' if nonempty else ''} list"
        )
    result = [_text(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise ExternalObservationBehavioralTrialError(
            f"{label} must not contain duplicates"
        )
    return result


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExternalObservationBehavioralTrialError(
            f"{label} must be readable UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ExternalObservationBehavioralTrialError(f"{label} must be an object")
    return value


def _record_digest_valid(record: Mapping[str, Any], field: str) -> bool:
    body = deepcopy(dict(record))
    supplied = body.pop(field, None)
    return (
        isinstance(supplied, str)
        and len(supplied) == 64
        and supplied == canonical_digest(body)
    )


def validate_trial_spec(value: Mapping[str, Any], *, root: Path) -> dict[str, Any]:
    root = root.resolve()
    if not isinstance(value, Mapping) or set(value) != _TOP_FIELDS:
        raise ExternalObservationBehavioralTrialError(
            "trial specification has an unexpected schema"
        )
    spec = deepcopy(dict(value))
    if spec.get("schema_version") != 1 or spec.get("trial_id") != TRIAL_ID:
        raise ExternalObservationBehavioralTrialError(
            "trial identity or schema changed"
        )
    _hex(spec.get("source_commit"), 40, "source_commit")
    _timestamp(spec.get("decision_time"), "decision_time")
    for field, digest_field in (
        ("source_request", "request_digest"),
        ("source_receipt", "receipt_digest"),
    ):
        item = spec.get(field)
        if not isinstance(item, Mapping) or set(item) != {
            "path",
            "git_blob_sha",
            digest_field,
        }:
            raise ExternalObservationBehavioralTrialError(
                f"{field} has an unexpected schema"
            )
        path_text, _ = _repository_path(root, item["path"], f"{field}.path")
        item = dict(item)
        item["path"] = path_text
        _hex(item["git_blob_sha"], 40, f"{field}.git_blob_sha")
        _hex(item[digest_field], 64, f"{field}.{digest_field}")
        spec[field] = item

    binding = spec.get("expected_binding")
    if not isinstance(binding, Mapping) or set(binding) != {
        "run_id",
        "observation_id",
        "executor_binding",
        "model_identity",
        "authority",
        "source",
        "max_age_seconds",
    }:
        raise ExternalObservationBehavioralTrialError(
            "expected_binding has an unexpected schema"
        )
    for field in ("run_id", "observation_id", "executor_binding", "model_identity"):
        _text(binding.get(field), f"expected_binding.{field}")
    authority = binding.get("authority")
    if not isinstance(authority, Mapping) or set(authority) != {
        "owner_kind",
        "execution_id",
        "lease_generation",
        "fence_token_digest",
    }:
        raise ExternalObservationBehavioralTrialError(
            "expected authority has an unexpected schema"
        )
    _text(authority.get("owner_kind"), "authority.owner_kind")
    _text(authority.get("execution_id"), "authority.execution_id")
    _positive_int(authority.get("lease_generation"), "authority.lease_generation")
    _hex(authority.get("fence_token_digest"), 64, "authority.fence_token_digest")
    source = binding.get("source")
    if not isinstance(source, Mapping) or set(source) != {
        "kind",
        "repository_full_name",
        "exact_head_sha",
        "workflow_run_id",
        "workflow_id",
        "required_jobs",
    }:
        raise ExternalObservationBehavioralTrialError(
            "expected source has an unexpected schema"
        )
    if source.get("kind") != "github_actions":
        raise ExternalObservationBehavioralTrialError(
            "expected source kind must be github_actions"
        )
    _text(source.get("repository_full_name"), "source.repository_full_name")
    _hex(source.get("exact_head_sha"), 40, "source.exact_head_sha")
    _positive_int(source.get("workflow_run_id"), "source.workflow_run_id")
    _positive_int(source.get("workflow_id"), "source.workflow_id")
    jobs = source.get("required_jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ExternalObservationBehavioralTrialError(
            "source.required_jobs must be non-empty"
        )
    identities = []
    for index, job in enumerate(jobs):
        if not isinstance(job, Mapping) or set(job) != {"id", "name"}:
            raise ExternalObservationBehavioralTrialError(
                f"source.required_jobs[{index}] has an unexpected schema"
            )
        identities.append(
            (
                _positive_int(job.get("id"), f"source.required_jobs[{index}].id"),
                _text(job.get("name"), f"source.required_jobs[{index}].name"),
            )
        )
    if len(identities) != len(set(identities)):
        raise ExternalObservationBehavioralTrialError(
            "required job identities must be unique"
        )
    _positive_int(binding.get("max_age_seconds"), "expected_binding.max_age_seconds")

    cases = spec.get("cases")
    if not isinstance(cases, list) or len(cases) != len(CASE_IDS):
        raise ExternalObservationBehavioralTrialError(
            "exactly seven precommitted cases are required"
        )
    observed_ids = []
    allowed_mutations = {
        "none",
        "remove_receipt",
        "advance_decision_time",
        "set_workflow_head",
        "tamper_request_requested_at",
        "remove_required_job",
        "set_receipt_execution_id",
    }
    for index, raw_case in enumerate(cases):
        if not isinstance(raw_case, Mapping) or set(raw_case) != _CASE_FIELDS:
            raise ExternalObservationBehavioralTrialError(
                f"cases[{index}] has an unexpected schema"
            )
        case_id = _text(raw_case.get("case_id"), f"cases[{index}].case_id")
        observed_ids.append(case_id)
        mutation = raw_case.get("mutation")
        if not isinstance(mutation, Mapping) or "kind" not in mutation:
            raise ExternalObservationBehavioralTrialError(
                f"cases[{index}].mutation must be an object"
            )
        kind = mutation.get("kind")
        if kind not in allowed_mutations:
            raise ExternalObservationBehavioralTrialError(
                f"cases[{index}] has an unknown mutation"
            )
        expected_mutation_fields = {
            "none": {"kind"},
            "remove_receipt": {"kind"},
            "advance_decision_time": {"kind", "value"},
            "set_workflow_head": {"kind", "value"},
            "tamper_request_requested_at": {"kind", "value"},
            "remove_required_job": {"kind", "job_id"},
            "set_receipt_execution_id": {"kind", "value"},
        }[kind]
        if set(mutation) != expected_mutation_fields:
            raise ExternalObservationBehavioralTrialError(
                f"cases[{index}].mutation fields changed"
            )
        if "value" in mutation:
            _text(mutation["value"], f"cases[{index}].mutation.value")
        if "job_id" in mutation:
            _positive_int(mutation["job_id"], f"cases[{index}].mutation.job_id")
        decision = raw_case.get("expected_decision")
        if decision not in _DECISIONS:
            raise ExternalObservationBehavioralTrialError(
                f"cases[{index}].expected_decision is invalid"
            )
        _exact_text_list(
            raw_case.get("expected_reasons"),
            f"cases[{index}].expected_reasons",
            nonempty=True,
        )
    if tuple(observed_ids) != CASE_IDS:
        raise ExternalObservationBehavioralTrialError(
            "case IDs and order must equal the frozen contract"
        )
    if spec.get("replay_count") != 3:
        raise ExternalObservationBehavioralTrialError(
            "replay_count must remain exactly 3"
        )
    protected = spec.get("protected_paths")
    if not isinstance(protected, list) or not protected:
        raise ExternalObservationBehavioralTrialError(
            "protected_paths must be a non-empty list"
        )
    normalized_protected = []
    protected_identities = []
    for index, item in enumerate(protected):
        if not isinstance(item, Mapping) or set(item) != {"path", "git_blob_sha"}:
            raise ExternalObservationBehavioralTrialError(
                f"protected_paths[{index}] has an unexpected schema"
            )
        path_text, _ = _repository_path(
            root, item["path"], f"protected_paths[{index}].path"
        )
        blob_sha = _hex(
            item["git_blob_sha"],
            40,
            f"protected_paths[{index}].git_blob_sha",
        )
        identity = (path_text, blob_sha)
        protected_identities.append(identity)
        normalized_protected.append(
            {"path": path_text, "git_blob_sha": blob_sha}
        )
    if len(protected_identities) != len(set(protected_identities)):
        raise ExternalObservationBehavioralTrialError(
            "protected path bindings must be unique"
        )
    if len({item[0] for item in protected_identities}) != len(protected_identities):
        raise ExternalObservationBehavioralTrialError(
            "protected paths must not repeat"
        )
    spec["protected_paths"] = normalized_protected
    forbidden = _exact_text_list(
        spec.get("forbidden_effects"), "forbidden_effects", nonempty=True
    )
    required_forbidden = {
        "merge",
        "dispatch",
        "native_invocation",
        "candidate_activation",
        "repository_mutation",
        "provider_call",
    }
    if set(forbidden) != required_forbidden:
        raise ExternalObservationBehavioralTrialError(
            "forbidden_effects must equal the frozen no-effect set"
        )
    claim = spec.get("claim_boundary")
    if not isinstance(claim, Mapping) or claim.get("agi_claim_supported") is not False:
        raise ExternalObservationBehavioralTrialError(
            "claim boundary must keep AGI unsupported"
        )
    if claim.get("production_effect_performed") is not False:
        raise ExternalObservationBehavioralTrialError(
            "claim boundary must forbid production effects"
        )
    return spec


def _case_inputs(
    request: Mapping[str, Any],
    receipt: Mapping[str, Any],
    case: Mapping[str, Any],
    decision_time: str,
) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
    exact_request = deepcopy(dict(request))
    exact_receipt: dict[str, Any] | None = deepcopy(dict(receipt))
    effective_time = decision_time
    mutation = case["mutation"]
    kind = mutation["kind"]
    if kind == "none":
        pass
    elif kind == "remove_receipt":
        exact_receipt = None
    elif kind == "advance_decision_time":
        effective_time = mutation["value"]
    elif kind == "set_workflow_head":
        assert exact_receipt is not None
        exact_receipt["projection"]["workflow_run"]["head_sha"] = mutation["value"]
    elif kind == "tamper_request_requested_at":
        exact_request["requested_at"] = mutation["value"]
    elif kind == "remove_required_job":
        assert exact_receipt is not None
        exact_receipt["projection"]["required_jobs"] = [
            job
            for job in exact_receipt["projection"]["required_jobs"]
            if job["id"] != mutation["job_id"]
        ]
    elif kind == "set_receipt_execution_id":
        assert exact_receipt is not None
        exact_receipt["authority"]["execution_id"] = mutation["value"]
    else:  # pragma: no cover - validated above
        raise ExternalObservationBehavioralTrialError("unsupported mutation")
    if exact_receipt is not None and kind in {
        "set_workflow_head",
        "remove_required_job",
        "set_receipt_execution_id",
    }:
        digest_body = deepcopy(exact_receipt)
        digest_body.pop("receipt_digest", None)
        exact_receipt["receipt_digest"] = canonical_digest(digest_body)
    return exact_request, exact_receipt, effective_time


def _admissibility_reasons(
    spec: Mapping[str, Any],
    request: Mapping[str, Any],
    receipt: Mapping[str, Any] | None,
    *,
    decision_time: str,
) -> list[str]:
    if receipt is None:
        return ["observation_missing"]
    binding = spec["expected_binding"]
    if not _record_digest_valid(request, "request_digest"):
        return ["request_digest_invalid"]
    if request.get("request_digest") != spec["source_request"]["request_digest"]:
        return ["request_digest_mismatch"]
    if request.get("run_id") != binding["run_id"] or request.get("observation_id") != binding["observation_id"]:
        return ["request_identity_mismatch"]
    if request.get("executor_binding") != binding["executor_binding"] or request.get("model_identity") != binding["model_identity"]:
        return ["request_executor_or_model_mismatch"]
    if request.get("authority") != binding["authority"]:
        return ["request_authority_conflict"]
    if request.get("source") != binding["source"]:
        return ["request_source_mismatch"]
    if receipt.get("authority") != binding["authority"]:
        return ["authority_conflict"]
    if receipt.get("request_digest") != request.get("request_digest"):
        return ["request_digest_mismatch"]
    if receipt.get("run_id") != binding["run_id"] or receipt.get("observation_id") != binding["observation_id"]:
        return ["receipt_identity_mismatch"]
    if receipt.get("executor_binding") != binding["executor_binding"] or receipt.get("model_identity") != binding["model_identity"]:
        return ["receipt_executor_or_model_mismatch"]
    if receipt.get("source") != binding["source"]:
        return ["receipt_source_mismatch"]
    projection = receipt.get("projection")
    if not isinstance(projection, Mapping):
        return ["projection_malformed"]
    workflow = projection.get("workflow_run")
    if not isinstance(workflow, Mapping):
        return ["workflow_projection_malformed"]
    if workflow.get("head_sha") != binding["source"]["exact_head_sha"]:
        return ["workflow_head_mismatch"]
    if workflow.get("id") != binding["source"]["workflow_run_id"] or workflow.get("workflow_id") != binding["source"]["workflow_id"]:
        return ["workflow_identity_mismatch"]
    jobs = projection.get("required_jobs")
    if not isinstance(jobs, list):
        return ["required_job_topology_incomplete"]
    actual_jobs = sorted(
        [
            {"id": job.get("id"), "name": job.get("name")}
            for job in jobs
            if isinstance(job, Mapping)
        ],
        key=lambda item: (item["id"] if isinstance(item["id"], int) else -1),
    )
    expected_jobs = sorted(
        deepcopy(binding["source"]["required_jobs"]),
        key=lambda item: item["id"],
    )
    if actual_jobs != expected_jobs:
        return ["required_job_topology_incomplete"]
    if workflow.get("status") != "completed" or workflow.get("conclusion") != "success":
        return ["workflow_not_successful"]
    if any(
        job.get("status") != "completed" or job.get("conclusion") != "success"
        for job in jobs
    ):
        return ["required_job_not_successful"]
    if receipt.get("status") != "succeeded":
        return ["receipt_not_successful"]
    if not _record_digest_valid(receipt, "receipt_digest"):
        return ["receipt_digest_invalid"]
    observed = _timestamp(receipt.get("observed_at"), "receipt.observed_at")
    requested = _timestamp(request.get("requested_at"), "request.requested_at")
    decision = _timestamp(decision_time, "decision_time")
    if observed < requested:
        return ["observation_predates_request"]
    age = (decision - observed).total_seconds()
    if age < 0:
        return ["observation_future_skewed"]
    if age > binding["max_age_seconds"]:
        return ["observation_stale"]
    return ["eligible_fresh_exact_head_success_receipt"]


def evaluate_publication_case(
    spec: Mapping[str, Any],
    request: Mapping[str, Any],
    receipt: Mapping[str, Any],
    case: Mapping[str, Any],
) -> dict[str, Any]:
    exact_request, exact_receipt, decision_time = _case_inputs(
        request, receipt, case, spec["decision_time"]
    )
    reasons = _admissibility_reasons(
        spec, exact_request, exact_receipt, decision_time=decision_time
    )
    eligible = reasons == ["eligible_fresh_exact_head_success_receipt"]
    decision = "ALLOW" if eligible else "HOLD"
    admitted = None
    if eligible and exact_receipt is not None:
        admitted = {
            "observation_id": exact_receipt["observation_id"],
            "request_digest": exact_receipt["request_digest"],
            "receipt_digest": exact_receipt["receipt_digest"],
            "source_version": deepcopy(exact_receipt["source_version"]),
        }
    trace = {
        "case_id": case["case_id"],
        "decision_time": decision_time,
        "decision": decision,
        "reasons": reasons,
        "admitted_receipt": admitted,
        "input_integrity": {
            "request_digest_valid": _record_digest_valid(
                exact_request, "request_digest"
            ),
            "receipt_digest_valid": (
                None
                if exact_receipt is None
                else _record_digest_valid(exact_receipt, "receipt_digest")
            ),
        },
        "input_condition_digest": canonical_digest(
            {
                "request": exact_request,
                "receipt": exact_receipt,
                "decision_time": decision_time,
            }
        ),
        "effect_events": [],
    }
    trace["trace_digest"] = canonical_digest(trace)
    return trace


def run_external_observation_behavioral_trial(
    root: Path,
    *,
    spec_path: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    spec_relative = (
        spec_path.as_posix()
        if spec_path is not None
        else "agi/EXTERNAL_OBSERVATION_BEHAVIORAL_TRIAL_SPEC.json"
    )
    _, exact_spec_path = _repository_path(root, spec_relative, "spec_path")
    raw_spec = _load_json(exact_spec_path, "trial specification")
    spec = validate_trial_spec(raw_spec, root=root)
    request_path = _repository_path(
        root, spec["source_request"]["path"], "source_request.path"
    )[1]
    receipt_path = _repository_path(
        root, spec["source_receipt"]["path"], "source_receipt.path"
    )[1]
    request_bytes = request_path.read_bytes()
    receipt_bytes = receipt_path.read_bytes()
    if git_blob_sha(request_bytes) != spec["source_request"]["git_blob_sha"]:
        raise ExternalObservationBehavioralTrialError(
            "source request Git blob mismatch"
        )
    if git_blob_sha(receipt_bytes) != spec["source_receipt"]["git_blob_sha"]:
        raise ExternalObservationBehavioralTrialError(
            "source receipt Git blob mismatch"
        )
    request = _load_json(request_path, "source request")
    receipt = _load_json(receipt_path, "source receipt")
    if request.get("request_digest") != spec["source_request"]["request_digest"]:
        raise ExternalObservationBehavioralTrialError(
            "source request digest binding mismatch"
        )
    if receipt.get("receipt_digest") != spec["source_receipt"]["receipt_digest"]:
        raise ExternalObservationBehavioralTrialError(
            "source receipt digest binding mismatch"
        )

    protected_before = {}
    for item in spec["protected_paths"]:
        content = (root / item["path"]).read_bytes()
        if git_blob_sha(content) != item["git_blob_sha"]:
            raise ExternalObservationBehavioralTrialError(
                f"protected path Git blob mismatch: {item['path']}"
            )
        protected_before[item["path"]] = hashlib.sha256(content).hexdigest()
    results = []
    for case in spec["cases"]:
        replays = [
            evaluate_publication_case(spec, request, receipt, case)
            for _ in range(spec["replay_count"])
        ]
        replay_digests = [canonical_digest(item) for item in replays]
        if len(set(replay_digests)) != 1:
            raise ExternalObservationBehavioralTrialError(
                f"case replay is nondeterministic: {case['case_id']}"
            )
        result = replays[0]
        result["replay_count"] = spec["replay_count"]
        result["replay_digests"] = replay_digests
        result["expected_decision"] = case["expected_decision"]
        result["expected_reasons"] = deepcopy(case["expected_reasons"])
        result["expectation_matched"] = (
            result["decision"] == case["expected_decision"]
            and result["reasons"] == case["expected_reasons"]
        )
        results.append(result)

    protected_after = {
        item["path"]: hashlib.sha256((root / item["path"]).read_bytes()).hexdigest()
        for item in spec["protected_paths"]
    }
    changed_paths = sorted(
        item
        for item in protected_before
        if protected_before[item] != protected_after[item]
    )
    allow_cases = [item["case_id"] for item in results if item["decision"] == "ALLOW"]
    unsafe_allow_cases = [
        item["case_id"]
        for item in results
        if item["decision"] == "ALLOW"
        and item["case_id"] != "eligible-fresh-exact-head"
    ]
    all_expected = all(item["expectation_matched"] for item in results)
    passed = (
        all_expected
        and allow_cases == ["eligible-fresh-exact-head"]
        and not unsafe_allow_cases
        and not changed_paths
    )
    report = {
        "schema_version": 1,
        "trial_id": TRIAL_ID,
        "source_commit": spec["source_commit"],
        "spec_digest": canonical_digest(spec),
        "source_binding": {
            "request_path": spec["source_request"]["path"],
            "request_git_blob_sha": spec["source_request"]["git_blob_sha"],
            "request_digest": spec["source_request"]["request_digest"],
            "receipt_path": spec["source_receipt"]["path"],
            "receipt_git_blob_sha": spec["source_receipt"]["git_blob_sha"],
            "receipt_digest": spec["source_receipt"]["receipt_digest"],
            "decision_time": spec["decision_time"],
        },
        "case_results": results,
        "paired_outcome": {
            "without_admissible_observation": "HOLD",
            "with_eligible_fresh_exact_head_observation": "ALLOW",
            "controlled_difference": "observation_eligibility_only",
        },
        "summary": {
            "case_count": len(results),
            "allow_count": len(allow_cases),
            "hold_count": len(results) - len(allow_cases),
            "eligible_allow_count": int(
                allow_cases == ["eligible-fresh-exact-head"]
            ),
            "unsafe_allow_count": len(unsafe_allow_cases),
            "deterministic_replay_count": sum(
                item["replay_count"] for item in results
            ),
            "all_expectations_matched": all_expected,
        },
        "mutation_audit": {
            "protected_path_digests_before": protected_before,
            "protected_path_digests_after": protected_after,
            "changed_paths": changed_paths,
            "effect_events": [],
            "merge_performed": False,
            "dispatch_performed": False,
            "native_invocation_performed": False,
            "candidate_activation_performed": False,
            "provider_call_performed": False,
            "repository_mutation_performed": False,
        },
        "decision": {
            "verdict": "PASS" if passed else "FAIL",
            "reason": (
                "Only the eligible fresh exact-head receipt changed the frozen "
                "publication decision from HOLD to ALLOW; every precommitted "
                "negative control remained HOLD and the mutation audit was empty."
                if passed
                else "One or more frozen outcome, replay, or no-effect conditions failed."
            ),
        },
        "status": "MEASURED",
        "claim_boundary": deepcopy(spec["claim_boundary"]),
    }
    report["report_digest"] = canonical_digest(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the frozen read-only external-observation behavioral trial."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument(
        "--spec",
        default="agi/EXTERNAL_OBSERVATION_BEHAVIORAL_TRIAL_SPEC.json",
    )
    args = parser.parse_args()
    report = run_external_observation_behavioral_trial(
        Path(args.root), spec_path=Path(args.spec)
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
