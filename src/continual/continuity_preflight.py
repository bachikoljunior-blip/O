from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


_POLICY_REVISION = 28
_POLICY_PATHS = (
    "agi/USER_INPUT_INBOX.json",
    "agi/USER_DIRECTIVE_EVENTS.json",
    "agi/WORK_STRATEGY.json",
)
_REQUIRED_ATOMS = {
    "r27-start-of-run-discretionary-stop-preflight",
    "r27-repair-unauthorized-stop-before-new-work",
    "r28-eliminate-and-validate-cause-before-resume",
    "r28-resume-fails-closed-without-causal-remediation",
}
_STOP_SIGNALS = (
    "approval",
    "permission",
    "discretion",
    "voluntary",
    "reassess",
    "uncertainty",
    "saturation",
)


class ContinuityPreflightError(ValueError):
    """Raised before Work resume when causal stop remediation is not proven."""


@dataclass(frozen=True)
class PriorStopClassification:
    """Bounded, deterministic classification of an active prior-stop record."""

    kind: str
    reason: str
    evidence_refs: tuple[str, ...] = ()


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContinuityPreflightError(f"{field} must be an object")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContinuityPreflightError(f"{field} must be a non-empty string")
    return value.strip()


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContinuityPreflightError(f"cannot read continuity policy: {path}") from exc
    if not isinstance(value, dict):
        raise ContinuityPreflightError(f"continuity policy must be an object: {path}")
    return value


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fence_digest(value: Any) -> str:
    return hashlib.sha256(_text(value, "state.fence_token").encode("utf-8")).hexdigest()


def _timestamp(value: Any, field: str) -> datetime:
    text = _text(value, field)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ContinuityPreflightError(f"{field} must be valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContinuityPreflightError(f"{field} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _nonempty_refs(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ContinuityPreflightError(f"{field} must contain evidence references")
    return [item.strip() for item in value]


def _optional_refs(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        return None
    return tuple(item.strip() for item in value)


def _legitimate_stop(state: Mapping[str, Any]) -> PriorStopClassification | None:
    termination = state.get("termination")
    if not isinstance(termination, Mapping) or termination.get("active") is not True:
        return None
    kind = termination.get("kind")
    legitimate_kinds = {
        "hard_platform_safety_prohibition",
        "secret_or_account_holder_only_blocker",
        "fresh_different_writer_detected",
        "running_exact_head_workflow",
        "user_level_objective_met",
    }
    if kind not in legitimate_kinds:
        return None
    refs = _optional_refs(termination.get("evidence_refs"))
    valid = refs is not None
    if kind in {
        "hard_platform_safety_prohibition",
        "secret_or_account_holder_only_blocker",
    }:
        valid = valid and termination.get("non_overridable") is True
    elif kind == "fresh_different_writer_detected":
        valid = (
            valid
            and isinstance(termination.get("different_execution_id"), str)
            and bool(termination["different_execution_id"].strip())
            and termination.get("fresh_activity_observed") is True
        )
    elif kind == "running_exact_head_workflow":
        head = termination.get("exact_head_sha")
        valid = (
            valid
            and termination.get("workflow_status") in {"queued", "in_progress"}
            and isinstance(termination.get("workflow_run_id"), (str, int))
            and bool(str(termination["workflow_run_id"]).strip())
            and isinstance(head, str)
            and re.fullmatch(r"[0-9a-f]{40}", head) is not None
        )
    elif kind == "user_level_objective_met":
        valid = (
            valid
            and state.get("status") == "completed"
            and termination.get("normal_completion") is True
            and termination.get("user_objective_met") is True
        )
    if not valid:
        return PriorStopClassification(
            "malformed_legitimate_stop",
            f"{kind} lacks required structured corroboration",
        )
    return PriorStopClassification(
        "legitimate_non_discretionary_stop",
        str(kind),
        refs or (),
    )


def classify_prior_stop(state: Mapping[str, Any]) -> PriorStopClassification:
    """Classify only the bounded stop categories used by the revision-28 guard.

    Legitimate stops must be active and structurally corroborated. Merely placing
    safety-like words in an error string cannot override discretionary-stop repair.
    """

    legitimate = _legitimate_stop(state)
    if legitimate is not None:
        return legitimate
    relevant = {
        "termination": state.get("termination"),
        "refire_failure": (
            state.get("refire", {}).get("failure")
            if isinstance(state.get("refire"), Mapping)
            else None
        ),
    }
    serialized = json.dumps(relevant, ensure_ascii=False, sort_keys=True).lower()
    if any(signal in serialized for signal in _STOP_SIGNALS):
        return PriorStopClassification(
            "discretionary_stop_detected",
            "bounded discretionary-stop signal detected",
        )
    return PriorStopClassification("no_stop_detected", "no bounded stop signal detected")


def _detected_discretionary_stop(state: Mapping[str, Any]) -> bool:
    return classify_prior_stop(state).kind == "discretionary_stop_detected"


def assert_work_resume_continuity_preflight(
    root: Path,
    *,
    run_id: str,
    executor_binding: str,
    model_identity: str,
) -> dict[str, Any]:
    """Fail closed unless a detected prior stop cause was eliminated before resume.

    Repositories without O's authoritative Work execution state are unaffected. Once
    inbox revision 28 is active, however, every resume is bound to a generation- and
    fence-specific causal preflight. A heartbeat, lease rotation, blocker restatement,
    or status rewrite cannot satisfy this check.
    """

    root = root.resolve()
    state_path = root / "agi" / "WORK_EXECUTION_STATE.json"
    if not state_path.exists():
        return {"required": False, "reason": "no_authoritative_work_state"}

    paths = {relative: root / relative for relative in _POLICY_PATHS}
    missing = [relative for relative, path in paths.items() if not path.exists()]
    if missing:
        raise ContinuityPreflightError(
            "continuity policy files are missing: " + ", ".join(missing)
        )
    state = _load(state_path)
    inbox = _load(paths["agi/USER_INPUT_INBOX.json"])
    ledger = _load(paths["agi/USER_DIRECTIVE_EVENTS.json"])
    strategy = _load(paths["agi/WORK_STRATEGY.json"])
    revision = inbox.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool):
        raise ContinuityPreflightError("inbox revision must be an integer")
    if revision < _POLICY_REVISION:
        return {"required": False, "reason": "causal_preflight_policy_not_active"}
    if ledger.get("source", {}).get("revision") != revision:
        raise ContinuityPreflightError("directive ledger is not bound to current inbox revision")
    atoms = ledger.get("atoms")
    if not isinstance(atoms, list):
        raise ContinuityPreflightError("directive ledger atoms must be a list")
    atom_ids = {
        item.get("atom_id") for item in atoms if isinstance(item, Mapping)
    }
    missing_atoms = sorted(_REQUIRED_ATOMS - atom_ids)
    if missing_atoms:
        raise ContinuityPreflightError(
            "causal continuity policy atoms are missing: " + ", ".join(missing_atoms)
        )
    rules = _mapping(strategy.get("execution_rules"), "strategy.execution_rules")
    required_true = (
        "start_of_run_continuity_preflight_required",
        "repair_discretionary_stop_before_unrelated_work",
        "eliminate_discretionary_stop_cause_before_resume",
    )
    if not all(rules.get(key) is True for key in required_true):
        raise ContinuityPreflightError("causal continuity strategy is not enabled")
    if rules.get("resume_without_validated_causal_remediation") is not False:
        raise ContinuityPreflightError("strategy does not fail closed against unsafe resume")

    preflight = _mapping(
        state.get("start_of_run_continuity_preflight"),
        "state.start_of_run_continuity_preflight",
    )
    if preflight.get("schema_version") != 1:
        raise ContinuityPreflightError("continuity preflight schema_version must equal 1")
    if preflight.get("policy_revision") != revision:
        raise ContinuityPreflightError("continuity preflight policy revision mismatch")
    if preflight.get("execution_id") != state.get("execution_id"):
        raise ContinuityPreflightError("continuity preflight execution_id mismatch")
    if preflight.get("lease_generation") != state.get("lease_generation"):
        raise ContinuityPreflightError("continuity preflight lease generation mismatch")
    if preflight.get("fence_token_digest") != _fence_digest(state.get("fence_token")):
        raise ContinuityPreflightError("continuity preflight fence mismatch")
    if preflight.get("run_id") != run_id or state.get("active_run_id") != run_id:
        raise ContinuityPreflightError("continuity preflight native run mismatch")
    if preflight.get("executor_binding") != executor_binding:
        raise ContinuityPreflightError("continuity preflight executor binding mismatch")
    if preflight.get("model_identity") != model_identity:
        raise ContinuityPreflightError("continuity preflight model identity mismatch")
    _timestamp(preflight.get("evaluated_at"), "continuity preflight evaluated_at")
    bindings = _mapping(preflight.get("policy_bindings"), "preflight.policy_bindings")
    for relative, path in paths.items():
        if bindings.get(relative) != _digest(path):
            raise ContinuityPreflightError(
                f"continuity preflight policy binding mismatch: {relative}"
            )
    _text(preflight.get("idempotency_key"), "preflight.idempotency_key")
    if preflight.get("resume_authorized") is not True:
        raise ContinuityPreflightError("continuity preflight has not authorized resume")

    prior_stop = classify_prior_stop(state)
    if prior_stop.kind == "malformed_legitimate_stop":
        raise ContinuityPreflightError(prior_stop.reason)
    if prior_stop.kind == "legitimate_non_discretionary_stop":
        raise ContinuityPreflightError(
            "legitimate non-discretionary stop remains active: " + prior_stop.reason
        )
    detected = prior_stop.kind == "discretionary_stop_detected"
    classification = preflight.get("classification")
    if detected:
        if classification != "discretionary_stop_cause_eliminated_and_validated":
            raise ContinuityPreflightError(
                "detected discretionary-stop cause lacks eliminated-and-validated classification"
            )
        causes = preflight.get("root_causes")
        if not isinstance(causes, list) or not causes:
            raise ContinuityPreflightError("causal remediation requires root_causes")
        for index, cause in enumerate(causes):
            cause = _mapping(cause, f"preflight.root_causes[{index}]")
            _text(cause.get("cause_id"), f"preflight.root_causes[{index}].cause_id")
            _text(cause.get("mechanism"), f"preflight.root_causes[{index}].mechanism")
            _nonempty_refs(
                cause.get("evidence_refs"),
                f"preflight.root_causes[{index}].evidence_refs",
            )
        remediations = preflight.get("remediations")
        if not isinstance(remediations, list) or not remediations:
            raise ContinuityPreflightError("causal remediation evidence is required")
        for index, remediation in enumerate(remediations):
            remediation = _mapping(remediation, f"preflight.remediations[{index}]")
            if remediation.get("status") not in {"implemented", "merged"}:
                raise ContinuityPreflightError("causal remediation is not implemented")
            _nonempty_refs(
                remediation.get("artifact_refs"),
                f"preflight.remediations[{index}].artifact_refs",
            )
        validations = preflight.get("validations")
        if not isinstance(validations, list) or not validations:
            raise ContinuityPreflightError("causal remediation validation is required")
        for index, validation in enumerate(validations):
            validation = _mapping(validation, f"preflight.validations[{index}]")
            if validation.get("status") != "passed":
                raise ContinuityPreflightError("causal remediation validation did not pass")
            _nonempty_refs(
                validation.get("evidence_refs"),
                f"preflight.validations[{index}].evidence_refs",
            )
        guard = _mapping(preflight.get("recurrence_guard"), "preflight.recurrence_guard")
        if guard.get("status") != "enforced":
            raise ContinuityPreflightError("causal recurrence guard is not enforced")
        _text(guard.get("entrypoint"), "preflight.recurrence_guard.entrypoint")
    elif classification != "no_discretionary_stop_detected":
        raise ContinuityPreflightError("continuity preflight classification mismatch")
    _nonempty_refs(preflight.get("evidence_refs"), "preflight.evidence_refs")
    return {
        "required": True,
        "policy_revision": revision,
        "classification": classification,
        "prior_stop_classification": prior_stop.kind,
        "execution_id": state.get("execution_id"),
        "lease_generation": state.get("lease_generation"),
        "resume_authorized": True,
    }
