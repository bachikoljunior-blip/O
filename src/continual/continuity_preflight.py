from __future__ import annotations

import hashlib
import json
import re
import subprocess
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
_INVOCATION_ID = re.compile(r"^invoke-[0-9a-f]{24}$")
_GIT_OBJECT_SHA = re.compile(r"^[0-9a-f]{40}$")


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


def _git_blob_digest(path: Path, field: str) -> str:
    try:
        payload = path.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise ContinuityPreflightError(f"cannot read {field}: {path}") from exc
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


def _safe_reference(root: Path, reference: str, field: str) -> Path:
    candidate = Path(reference)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ContinuityPreflightError(f"{field} escapes the repository")
    resolved = (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise ContinuityPreflightError(f"{field} escapes the repository")
    return resolved


def _git_blob_at_commit(
    root: Path,
    commit_sha: str,
    reference: str,
    field: str,
) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-tree", "-z", commit_sha, "--", reference],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContinuityPreflightError(
            f"cannot verify {field} in continuation source commit"
        ) from exc
    entries = [entry for entry in completed.stdout.split(b"\0") if entry]
    if completed.returncode != 0 or len(entries) != 1:
        raise ContinuityPreflightError(
            f"{field} is not present in continuation source commit"
        )
    try:
        metadata, raw_path = entries[0].split(b"\t", 1)
        mode, object_type, blob_sha = metadata.decode("ascii").split()
        actual_path = raw_path.decode("utf-8")
    except (UnicodeError, ValueError) as exc:
        raise ContinuityPreflightError(
            f"cannot parse {field} from continuation source commit"
        ) from exc
    if (
        mode not in {"100644", "100755"}
        or object_type != "blob"
        or _GIT_OBJECT_SHA.fullmatch(blob_sha) is None
        or actual_path != reference
    ):
        raise ContinuityPreflightError(
            f"{field} has an invalid continuation source tree entry"
        )
    return blob_sha


def _assert_source_commit_on_remote_main(root: Path, commit_sha: str) -> None:
    remote_main = "refs/remotes/origin/main"
    try:
        remote_ref = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", f"{remote_main}^{{commit}}"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        ancestor = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "merge-base",
                "--is-ancestor",
                commit_sha,
                remote_main,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContinuityPreflightError(
            "cannot verify continuation source against origin/main"
        ) from exc
    if remote_ref.returncode != 0:
        raise ContinuityPreflightError(
            "origin/main is unavailable for continuation source verification"
        )
    if ancestor.returncode != 0:
        raise ContinuityPreflightError(
            "continuation source commit is not reachable from origin/main"
        )


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


def _assert_remote_durable_continuation(
    root: Path,
    *,
    state: Mapping[str, Any],
    run_id: str,
    executor_binding: str,
    model_identity: str,
) -> dict[str, Any]:
    """Bind a pending semantic resume to bytes proven durable on remote main.

    The outer recovery process performs the authoritative remote readback and
    records its exact commit/blob proof in ``continuation_durability``.  This
    local entry guard independently recomputes both Git blob identities and
    cross-checks every request/snapshot binding before native mutation.  A
    state-only heartbeat therefore cannot make a local-only frozen request
    resumable.
    """

    exact_value = state.get("exact_continuation")
    if exact_value is None:
        return {"required": False, "reason": "no_exact_continuation"}
    exact = _mapping(exact_value, "state.exact_continuation")
    pending_id = exact.get("pending_work_invocation_id")
    if pending_id is None:
        return {"required": False, "reason": "no_pending_work_invocation"}
    if not isinstance(pending_id, str) or _INVOCATION_ID.fullmatch(pending_id) is None:
        raise ContinuityPreflightError(
            "state.exact_continuation.pending_work_invocation_id is malformed"
        )

    expected_request_ref = (
        f".continual/work-model/invocations/{pending_id}/request.json"
    )
    if exact.get("pending_request_ref") != expected_request_ref:
        raise ContinuityPreflightError("pending Work request reference mismatch")
    request_path = root / expected_request_ref
    request = _load(request_path)
    if request.get("invocation_id") != pending_id:
        raise ContinuityPreflightError("pending Work request identity mismatch")
    request_digest = _text(
        exact.get("pending_request_digest"),
        "state.exact_continuation.pending_request_digest",
    )
    if request.get("request_digest") != request_digest:
        raise ContinuityPreflightError("pending Work request digest mismatch")
    if request.get("run_id") != run_id:
        raise ContinuityPreflightError("pending Work request run mismatch")
    if request.get("executor_binding") != executor_binding:
        raise ContinuityPreflightError("pending Work request executor binding mismatch")
    if request.get("model_identity") != model_identity:
        raise ContinuityPreflightError("pending Work request model identity mismatch")
    request_blob = _text(
        exact.get("pending_request_blob_sha"),
        "state.exact_continuation.pending_request_blob_sha",
    )
    if _GIT_OBJECT_SHA.fullmatch(request_blob) is None:
        raise ContinuityPreflightError("pending Work request blob SHA is malformed")
    if _git_blob_digest(request_path, "pending Work request") != request_blob:
        raise ContinuityPreflightError("pending Work request blob mismatch")

    snapshot_ref = _text(
        exact.get("run_snapshot_ref"),
        "state.exact_continuation.run_snapshot_ref",
    )
    snapshot_path = _safe_reference(
        root,
        snapshot_ref,
        "state.exact_continuation.run_snapshot_ref",
    )
    snapshot = _load(snapshot_path)
    if snapshot.get("run_id") != run_id:
        raise ContinuityPreflightError("continuation snapshot run mismatch")
    if snapshot.get("revision") != exact.get("snapshot_revision"):
        raise ContinuityPreflightError("continuation snapshot revision mismatch")
    if snapshot.get("phase") != exact.get("native_phase"):
        raise ContinuityPreflightError("continuation snapshot phase mismatch")
    snapshot_blob = _text(
        exact.get("snapshot_blob_sha"),
        "state.exact_continuation.snapshot_blob_sha",
    )
    if _GIT_OBJECT_SHA.fullmatch(snapshot_blob) is None:
        raise ContinuityPreflightError("continuation snapshot blob SHA is malformed")
    if _git_blob_digest(snapshot_path, "continuation snapshot") != snapshot_blob:
        raise ContinuityPreflightError("continuation snapshot blob mismatch")
    if exact.get("snapshot_branch") != "main":
        raise ContinuityPreflightError("continuation snapshot is not bound to main")
    snapshot_head = _text(
        exact.get("snapshot_head_sha"),
        "state.exact_continuation.snapshot_head_sha",
    )
    if _GIT_OBJECT_SHA.fullmatch(snapshot_head) is None:
        raise ContinuityPreflightError("continuation snapshot head SHA is malformed")

    proof = _mapping(
        state.get("continuation_durability"),
        "state.continuation_durability",
    )
    if proof.get("schema_version") != 1:
        raise ContinuityPreflightError("continuation durability schema_version must equal 1")
    if proof.get("status") != "remote_main_readback_verified":
        raise ContinuityPreflightError("continuation is not remote-main durable")
    if proof.get("verified_remote_readback") is not True:
        raise ContinuityPreflightError("continuation remote readback is not verified")
    if proof.get("execution_id") != state.get("execution_id"):
        raise ContinuityPreflightError("continuation durability execution mismatch")
    if proof.get("lease_generation") != state.get("lease_generation"):
        raise ContinuityPreflightError("continuation durability generation mismatch")
    if proof.get("fence_token_digest") != _fence_digest(state.get("fence_token")):
        raise ContinuityPreflightError("continuation durability fence mismatch")
    if proof.get("source_main_sha") != snapshot_head:
        raise ContinuityPreflightError("continuation durability main commit mismatch")
    _assert_source_commit_on_remote_main(root, snapshot_head)
    if (
        _git_blob_at_commit(
            root,
            snapshot_head,
            expected_request_ref,
            "pending Work request",
        )
        != request_blob
    ):
        raise ContinuityPreflightError(
            "pending Work request source-commit blob mismatch"
        )
    if (
        _git_blob_at_commit(
            root,
            snapshot_head,
            snapshot_ref,
            "continuation snapshot",
        )
        != snapshot_blob
    ):
        raise ContinuityPreflightError(
            "continuation snapshot source-commit blob mismatch"
        )
    if proof.get("pending_work_invocation_id") != pending_id:
        raise ContinuityPreflightError("continuation durability invocation mismatch")
    if proof.get("pending_request_ref") != expected_request_ref:
        raise ContinuityPreflightError("continuation durability request reference mismatch")
    if proof.get("pending_request_digest") != request_digest:
        raise ContinuityPreflightError("continuation durability request digest mismatch")
    if proof.get("pending_request_blob_sha") != request_blob:
        raise ContinuityPreflightError("continuation durability request blob mismatch")
    if proof.get("run_snapshot_ref") != snapshot_ref:
        raise ContinuityPreflightError("continuation durability snapshot reference mismatch")
    if proof.get("snapshot_blob_sha") != snapshot_blob:
        raise ContinuityPreflightError("continuation durability snapshot blob mismatch")
    if proof.get("snapshot_revision") != snapshot.get("revision"):
        raise ContinuityPreflightError("continuation durability snapshot revision mismatch")
    if proof.get("native_phase") != snapshot.get("phase"):
        raise ContinuityPreflightError("continuation durability snapshot phase mismatch")
    _timestamp(proof.get("verified_at"), "continuation durability verified_at")
    return {
        "required": True,
        "status": "remote_main_readback_verified",
        "source_main_sha": snapshot_head,
        "pending_work_invocation_id": pending_id,
        "pending_request_blob_sha": request_blob,
        "snapshot_blob_sha": snapshot_blob,
    }


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

    continuation_durability = _assert_remote_durable_continuation(
        root,
        state=state,
        run_id=run_id,
        executor_binding=executor_binding,
        model_identity=model_identity,
    )

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
        "continuation_durability": continuation_durability,
        "resume_authorized": True,
    }
