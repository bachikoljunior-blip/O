from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .store import Store


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
_BOUNDARIES = {
    "development",
    "retention_validation",
    "transfer_validation",
    "independent_evaluation",
}
_RISK_SIGNALS = {
    "context_accumulation_degradation",
    "stale_state_risk",
    "duplicate_search_risk",
    "evaluation_contamination_risk",
}
_FORBIDDEN_KEYS = {
    "chain_of_thought",
    "hidden_chain_of_thought",
    "hidden_reasoning",
    "private_reasoning",
    "scratchpad",
    "raw_system_prompt",
    "raw_prompt",
    "api_key",
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
    "private_key",
    "secret",
}
_SECRET_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b", re.I),
)
_LIST_FIELDS = {
    "applicability",
    "non_applicability",
    "supporting_evidence",
    "contradictory_evidence",
    "rejected_reasons",
    "depends_on",
    "conflicts_with",
    "tested_with",
    "incompatible_with",
    "supersedes",
    "source_refs",
}


class SelfApplicationError(ValueError):
    """Raised when an externally executed O development result is unsafe or malformed."""


class SelfApplicationConflict(SelfApplicationError):
    """Raised when one execution id is reused with different durable content."""


@dataclass(frozen=True)
class ContextBoundaryDecision:
    boundary: str
    mode: str
    logical_reset_required: bool
    fresh_execution_required: bool
    independent_execution_required: bool
    persist_before_switch: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["reason_codes"] = list(self.reason_codes)
        return value


def decide_context_boundary(
    boundary: str = "development",
    risk_signals: Iterable[str] = (),
) -> ContextBoundaryDecision:
    """Choose the least disruptive context boundary that still prevents contamination."""

    if boundary not in _BOUNDARIES:
        raise SelfApplicationError(f"unsupported context boundary: {boundary}")
    raw_signals = tuple(risk_signals)
    if any(not isinstance(signal, str) for signal in raw_signals):
        raise SelfApplicationError("context risk signals must be strings")
    signals = tuple(sorted(set(raw_signals)))
    unknown = sorted(set(signals) - _RISK_SIGNALS)
    if unknown:
        raise SelfApplicationError(f"unsupported context risk signals: {unknown}")

    if boundary == "independent_evaluation":
        return ContextBoundaryDecision(
            boundary=boundary,
            mode="independent_execution_required",
            logical_reset_required=True,
            fresh_execution_required=True,
            independent_execution_required=True,
            persist_before_switch=True,
            reason_codes=("independent_evaluation_boundary", *signals),
        )
    if boundary in {"retention_validation", "transfer_validation"}:
        return ContextBoundaryDecision(
            boundary=boundary,
            mode="fresh_execution_required",
            logical_reset_required=True,
            fresh_execution_required=True,
            independent_execution_required=False,
            persist_before_switch=True,
            reason_codes=(f"{boundary}_boundary", *signals),
        )
    if signals:
        return ContextBoundaryDecision(
            boundary=boundary,
            mode="logical_reset_required",
            logical_reset_required=True,
            fresh_execution_required=False,
            independent_execution_required=False,
            persist_before_switch=True,
            reason_codes=signals,
        )
    return ContextBoundaryDecision(
        boundary=boundary,
        mode="reuse_current_context",
        logical_reset_required=False,
        fresh_execution_required=False,
        independent_execution_required=False,
        persist_before_switch=False,
        reason_codes=("no_contamination_signal",),
    )


def _walk_safe(value: Any, path: str = "record") -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized = key.strip().lower().replace("-", "_")
            if normalized in _FORBIDDEN_KEYS:
                raise SelfApplicationError(f"{path}.{key} is forbidden durable content")
            _walk_safe(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _walk_safe(item, f"{path}[{index}]")
        return
    if isinstance(value, str):
        for pattern in _SECRET_PATTERNS:
            if pattern.search(value):
                raise SelfApplicationError(f"{path} contains secret-like material")


def _required_text(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SelfApplicationError(f"{key} must be a non-empty string")
    return value.strip()


def _list(record: Mapping[str, Any], key: str) -> list[Any]:
    value = record.get(key, [])
    if not isinstance(value, list):
        raise SelfApplicationError(f"{key} must be a list")
    return deepcopy(value)


def _safe_id(value: str, field: str) -> str:
    if not _ID.fullmatch(value):
        raise SelfApplicationError(
            f"{field} must contain only letters, digits, '.', '_' or '-' and be 3-128 characters"
        )
    return value


def _jsonl(records: Iterable[Mapping[str, Any]]) -> str:
    return "".join(
        json.dumps(dict(record), ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )


def _dedupe(values: Iterable[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        result.append(deepcopy(value))
    return result


def _candidate_id(store: Store, proposal: Mapping[str, Any], execution_id: str) -> str:
    requested = proposal.get("candidate_id")
    if isinstance(requested, str):
        return _safe_id(requested, "candidate_id")
    digest = store.stable_digest(
        {
            "execution_id": execution_id,
            "target_component": proposal.get("target_component"),
            "expected_scope": proposal.get("expected_scope"),
            "what_it_changes": proposal.get("what_it_changes"),
            "prompt_content": proposal.get("prompt_content"),
        },
        length=20,
    )
    return f"candidate-self-{digest}"


def _register_candidate(
    store: Store,
    root: Path,
    proposal: Mapping[str, Any],
    *,
    execution_id: str,
    source_ref: str,
    episode_id: str,
) -> str:
    target = proposal.get("target_component")
    scope = proposal.get("expected_scope")
    prompt_content = proposal.get("prompt_content")
    if not isinstance(target, str) or not target.strip():
        raise SelfApplicationError("candidate_proposal.target_component must be a non-empty string")
    if not isinstance(scope, str) or not scope.strip():
        raise SelfApplicationError("candidate_proposal.expected_scope must be a non-empty string")
    if not isinstance(prompt_content, str) or not prompt_content.strip():
        raise SelfApplicationError("candidate_proposal.prompt_content must be a non-empty string")

    candidate_id = _candidate_id(store, proposal, execution_id)
    cdir = root / ".continual" / "candidates" / candidate_id
    path = cdir / "candidate.json"
    existing = store.read_json(path, {})
    if not isinstance(existing, dict):
        raise SelfApplicationConflict(f"candidate is not an object: {candidate_id}")

    prompt_path = cdir / "prompt.md"
    normalized_prompt = prompt_content.rstrip() + "\n"
    if prompt_path.exists() and prompt_path.read_text(encoding="utf-8") != normalized_prompt:
        raise SelfApplicationConflict(f"candidate prompt changed for immutable id: {candidate_id}")

    candidate = deepcopy(existing)
    immutable = {
        "candidate_id": candidate_id,
        "target_component": target.strip(),
        "expected_scope": scope.strip(),
        "prompt_mode": str(proposal.get("prompt_mode", "overlay")),
    }
    if immutable["prompt_mode"] not in {"overlay", "replace"}:
        raise SelfApplicationError("candidate_proposal.prompt_mode must be overlay or replace")
    for key, value in immutable.items():
        prior = candidate.get(key)
        if prior is not None and prior != value:
            raise SelfApplicationConflict(f"candidate {candidate_id} changed immutable field {key}")
        candidate[key] = value

    candidate.setdefault("status", "candidate")
    candidate["prompt_path"] = prompt_path.relative_to(root).as_posix()
    candidate.setdefault("what_it_changes", proposal.get("what_it_changes", ""))
    candidate.setdefault("scope_states", {})
    if not isinstance(candidate["scope_states"], dict):
        raise SelfApplicationConflict(f"candidate scope_states is not an object: {candidate_id}")
    candidate["scope_states"].setdefault(scope.strip(), "candidate")
    candidate["scope_states"].setdefault("global", "untested")

    for key in _LIST_FIELDS:
        current = candidate.get(key, [])
        incoming = proposal.get(key, [])
        if not isinstance(current, list):
            current = [current]
        if not isinstance(incoming, list):
            incoming = [incoming]
        candidate[key] = _dedupe([*current, *incoming])
    candidate["source_refs"] = _dedupe([*candidate["source_refs"], source_ref])
    candidate["supporting_evidence"] = _dedupe(
        [
            *candidate["supporting_evidence"],
            {
                "episode_id": episode_id,
                "finding": "Repository-development execution produced this reusable scoped proposal.",
            },
        ]
    )

    store.atomic_text(prompt_path, normalized_prompt)
    store.atomic_json(path, candidate)

    index_path = root / ".continual" / "candidates" / "index.json"
    index = store.read_json(index_path, {"schema_version": 1, "candidates": []})
    if not isinstance(index, dict):
        raise SelfApplicationConflict("candidate index is not an object")
    items = index.setdefault("candidates", [])
    if not isinstance(items, list):
        raise SelfApplicationConflict("candidate index candidates must be a list")
    item = next(
        (
            current
            for current in items
            if isinstance(current, dict) and current.get("candidate_id") == candidate_id
        ),
        None,
    )
    if item is None:
        item = {"candidate_id": candidate_id}
        items.append(item)
    for key in (
        "target_component",
        "expected_scope",
        "status",
        "prompt_path",
        "prompt_mode",
        "depends_on",
        "conflicts_with",
        "scope_states",
    ):
        item[key] = deepcopy(candidate.get(key))
    index["candidates"] = sorted(
        (entry for entry in items if isinstance(entry, dict)),
        key=lambda entry: str(entry.get("candidate_id", "")),
    )
    index.setdefault(
        "notes",
        "Candidate adoption is scoped, evidence-driven, reversible, and evaluated immediately before affected units.",
    )
    index["schema_version"] = max(int(index.get("schema_version", 1)), 2)
    store.atomic_json(index_path, index)
    return candidate_id


def record_self_application(root: Path, raw_record: Mapping[str, Any]) -> dict[str, Any]:
    """Persist one task-chat O-development execution in the native continual layout.

    The caller supplies conclusions and evidence, not hidden reasoning. The produced evidence is
    always internal development evidence and is never admissible for the strict external AGI gate.
    """

    if not isinstance(raw_record, Mapping):
        raise SelfApplicationError("record must be an object")
    root = root.resolve()
    record = deepcopy(dict(raw_record))
    _walk_safe(record)

    execution_id = _safe_id(_required_text(record, "execution_id"), "execution_id")
    request = _required_text(record, "request")
    objective = _required_text(record, "objective")
    executor = record.get("executor")
    if not isinstance(executor, (str, dict)) or not executor:
        raise SelfApplicationError("executor must be a non-empty string or object")
    started_at = _required_text(record, "started_at")
    completed_at = _required_text(record, "completed_at")
    verdict = _required_text(record, "verdict").upper()
    if verdict not in {"PASS", "FAIL", "UNCERTAIN"}:
        raise SelfApplicationError("verdict must be PASS, FAIL or UNCERTAIN")
    scope = record.get("scope", "agi/repository-development")
    if not isinstance(scope, str) or not scope.strip():
        raise SelfApplicationError("scope must be a non-empty string")
    scope = scope.strip()

    context = record.get("context", {})
    if not isinstance(context, Mapping):
        raise SelfApplicationError("context must be an object")
    boundary = context.get("boundary", "development")
    risk_signals = context.get("risk_signals", [])
    if not isinstance(boundary, str) or not isinstance(risk_signals, list):
        raise SelfApplicationError("context boundary must be a string and risk_signals must be a list")
    decision = decide_context_boundary(boundary, risk_signals)
    logical_reset_used = bool(context.get("logical_reset_used", False))
    fresh_execution_used = bool(context.get("fresh_execution_used", False))
    independent_execution_used = bool(context.get("independent_execution_used", False))
    boundary_satisfied = (
        (not decision.logical_reset_required or logical_reset_used or fresh_execution_used)
        and (not decision.fresh_execution_required or fresh_execution_used)
        and (not decision.independent_execution_required or independent_execution_used)
    )
    if verdict == "PASS" and not boundary_satisfied:
        raise SelfApplicationError(
            "PASS is not allowed until the selected logical-reset/fresh/independent context boundary is satisfied"
        )
    context_record = {
        "physical_fresh_context_fixed_requirement": False,
        "repository_latest_main_is_durable_state": True,
        "boundary": boundary,
        "risk_signals": sorted(set(risk_signals)),
        "logical_reset_used": logical_reset_used,
        "fresh_execution_used": fresh_execution_used,
        "independent_execution_used": independent_execution_used,
        "boundary_satisfied": boundary_satisfied,
        "decision": decision.to_dict(),
    }

    store = Store(root)
    normalized_for_digest = deepcopy(record)
    normalized_for_digest["context"] = context_record
    payload_digest = store.stable_digest(normalized_for_digest, length=64)

    run_id = _safe_id(
        str(record.get("run_id") or f"run-self-{store.stable_digest(execution_id, length=20)}"),
        "run_id",
    )
    episode_id = _safe_id(
        str(record.get("episode_id") or f"episode-{run_id.removeprefix('run-')}"),
        "episode_id",
    )
    evidence_id = _safe_id(
        str(record.get("evidence_id") or f"self-application-{store.stable_digest(execution_id, length=20)}"),
        "evidence_id",
    )

    rd = store.run_dir(run_id)
    invocation_path = rd / "invocations" / "self-application.json"
    existing_invocation = store.read_json(invocation_path, {})
    if isinstance(existing_invocation, dict) and existing_invocation:
        prior_digest = existing_invocation.get("payload_digest")
        if prior_digest != payload_digest:
            raise SelfApplicationConflict(
                f"execution_id/run_id already has different durable content: {execution_id}/{run_id}"
            )
        if existing_invocation.get("status") == "complete":
            result = deepcopy(existing_invocation.get("result", {}))
            result["replayed"] = True
            return result

    rd.mkdir(parents=True, exist_ok=True)
    store.atomic_json(
        invocation_path,
        {
            "invocation_id": f"invoke-self-{store.stable_digest(execution_id)}",
            "component": "self_application",
            "status": "started",
            "execution_id": execution_id,
            "payload_digest": payload_digest,
            "started_at": started_at,
        },
    )

    actions = _list(record, "actions")
    decisions = _list(record, "decisions")
    observations = _list(record, "observations")
    failures = _list(record, "failures")
    unknowns = _list(record, "unknowns")
    artifacts = _list(record, "artifacts")
    validation = _list(record, "validation")
    claims = _list(record, "claims")
    unresolved = _list(record, "unresolved")
    success_conditions = _list(record, "success_conditions")

    local_learn_ref = (
        rd / "local-learn" / "self-application-execute.json"
    ).relative_to(root).as_posix()
    candidate_ids: list[str] = []
    proposal = record.get("candidate_proposal")
    local_learn: dict[str, Any]
    if proposal is None:
        local_learn = {
            "decision": "NO_CHANGE",
            "candidate_proposals": [],
            "observations": ["No reusable scoped change was proposed by this execution."],
        }
    else:
        if not isinstance(proposal, Mapping):
            raise SelfApplicationError("candidate_proposal must be an object")
        proposal_copy = deepcopy(dict(proposal))
        proposal_copy["candidate_id"] = _candidate_id(store, proposal_copy, execution_id)
        local_learn = {
            "decision": "PROPOSE_CANDIDATE",
            "candidate_proposals": [proposal_copy],
            "observations": [
                "The proposal remains inactive until exact-scope pre-application evaluation and regression evidence."
            ],
        }
    store.atomic_json(root / local_learn_ref, local_learn)
    if proposal is not None:
        candidate_ids.append(
            _register_candidate(
                store,
                root,
                proposal_copy,
                execution_id=execution_id,
                source_ref=local_learn_ref,
                episode_id=episode_id,
            )
        )

    evidence_ref = (
        root / ".continual" / "evidence" / "self-application" / f"{evidence_id}.json"
    ).relative_to(root).as_posix()
    episode_ref = (
        root / ".continual" / "episodes" / episode_id / "episode.json"
    ).relative_to(root).as_posix()

    fragments = {
        "0000-entry.json": {
            "component": "entry",
            "objective": objective,
            "success_conditions": success_conditions,
            "source": "task_chat_model_execution",
        },
        "0001-root.json": {
            "component": "root",
            "selected_component": "execute",
            "scope": scope,
            "context": context_record,
        },
        "0002-execute.json": {
            "component": "execute",
            "actions": actions,
            "decisions": decisions,
            "observations": observations,
            "failures": failures,
            "unknowns": unknowns,
            "artifacts": artifacts,
            "validation": validation,
        },
        "0003-task_evaluate.json": {
            "component": "task_evaluate",
            "verdict": verdict,
            "claims": claims,
            "unresolved": unresolved,
            "claim_tier": "internal_development",
            "external_claim_admissible": False,
        },
        "0004-consolidate_episode.json": {
            "component": "consolidate_episode",
            "episode_id": episode_id,
            "episode_ref": episode_ref,
        },
        "0005-learn.json": {
            "component": "learn",
            "decision": local_learn["decision"],
            "candidate_ids": candidate_ids,
            "source_ref": local_learn_ref,
        },
    }
    for name, fragment in fragments.items():
        store.atomic_json(rd / "fragments" / name, fragment)

    events = [
        {
            "type": "run_started",
            "at": started_at,
            "run_id": run_id,
            "execution_id": execution_id,
            "mode": "task-chat-model-self-application",
        },
        {
            "type": "context_boundary_selected",
            "at": started_at,
            "run_id": run_id,
            "decision": decision.to_dict(),
        },
        {
            "type": "execute_complete",
            "at": completed_at,
            "run_id": run_id,
            "artifacts": artifacts,
        },
        {
            "type": "task_evaluate",
            "at": completed_at,
            "run_id": run_id,
            "verdict": verdict,
        },
        {
            "type": "consolidate_episode",
            "at": completed_at,
            "run_id": run_id,
            "episode_id": episode_id,
        },
        {
            "type": "post_task_learn",
            "at": completed_at,
            "run_id": run_id,
            "decision": local_learn["decision"],
            "candidate_ids": candidate_ids,
        },
        {
            "type": "run_finished",
            "at": completed_at,
            "run_id": run_id,
            "episode_id": episode_id,
            "status": "finished",
        },
    ]
    store.atomic_text(rd / "events.jsonl", _jsonl(events))
    store.atomic_text(rd / "request.md", request.rstrip() + "\n")

    snapshot = {
        "schema_version": 1,
        "run_id": run_id,
        "execution_id": execution_id,
        "status": "finished",
        "phase": "self-application-finished",
        "revision": 1,
        "created_at": started_at,
        "updated_at": completed_at,
        "request": request,
        "objective": objective,
        "scope": scope,
        "executor": deepcopy(executor),
        "execution_mode": "task-chat-model-self-application",
        "context": context_record,
        "episode_id": episode_id,
        "task_verdict": verdict,
        "produced_candidates": candidate_ids,
        "artifacts": artifacts,
        "evidence_refs": [evidence_ref],
        "claim_tier": "internal_development",
        "external_claim_admissible": False,
    }
    store.atomic_json(rd / "snapshot.json", snapshot)

    episode = {
        "schema_version": 1,
        "episode_id": episode_id,
        "source_run_id": run_id,
        "execution_id": execution_id,
        "task": request,
        "objective": objective,
        "executor": deepcopy(executor),
        "mode": "task-chat-model-self-application",
        "outcome": record.get("outcome", "completed"),
        "task_verdict": verdict,
        "success_conditions": success_conditions,
        "actions": actions,
        "decisions": decisions,
        "observations": observations,
        "failures": failures,
        "unknowns": unknowns,
        "results": record.get("results", artifacts),
        "evidence_refs": [evidence_ref, *artifacts],
        "candidate_refs": candidate_ids,
        "claims": claims,
        "unresolved": unresolved,
        "context": context_record,
        "claim_tier": "internal_development",
        "external_claim_admissible": False,
    }
    episode_dir = root / ".continual" / "episodes" / episode_id
    store.atomic_json(episode_dir / "episode.json", episode)
    relations = [
        {
            "type": "source_run",
            "run_id": run_id,
            "at": completed_at,
        },
        {
            "type": "internal_evidence",
            "evidence_id": evidence_id,
            "evidence_ref": evidence_ref,
            "at": completed_at,
        },
        *(
            {
                "type": "produced_candidate",
                "candidate_id": candidate_id,
                "at": completed_at,
            }
            for candidate_id in candidate_ids
        ),
    ]
    store.atomic_text(episode_dir / "relations.jsonl", _jsonl(relations))

    evidence = {
        "schema_version": 1,
        "evidence_id": evidence_id,
        "kind": "self_application_repository_development",
        "evidence_tier": "internal_development",
        "admissible_for_agi_claim": False,
        "producer": deepcopy(executor),
        "execution_id": execution_id,
        "run_id": run_id,
        "episode_id": episode_id,
        "request_digest": store.stable_digest(request, length=64),
        "payload_digest": payload_digest,
        "context": context_record,
        "actions": actions,
        "decisions": decisions,
        "observations": observations,
        "failures": failures,
        "unknowns": unknowns,
        "artifacts": artifacts,
        "validation": validation,
        "claims": claims,
        "candidate_ids": candidate_ids,
        "repository": deepcopy(record.get("repository", {})),
        "strict_external_gate_unchanged": True,
        "claim_boundary": (
            "This task-chat self-application record is internal development evidence only. "
            "It cannot establish AGI or evaluator independence."
        ),
        "recorded_at": completed_at,
    }
    store.atomic_json(root / evidence_ref, evidence)

    result = {
        "execution_id": execution_id,
        "run_id": run_id,
        "episode_id": episode_id,
        "candidate_ids": candidate_ids,
        "evidence_id": evidence_id,
        "evidence_ref": evidence_ref,
        "context_decision": decision.to_dict(),
        "payload_digest": payload_digest,
        "replayed": False,
    }
    store.atomic_json(
        invocation_path,
        {
            "invocation_id": f"invoke-self-{store.stable_digest(execution_id)}",
            "component": "self_application",
            "status": "complete",
            "execution_id": execution_id,
            "payload_digest": payload_digest,
            "result": result,
            "completed_at": completed_at,
        },
    )
    return result
