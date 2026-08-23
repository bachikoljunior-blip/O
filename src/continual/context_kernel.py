from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .store import Store


class ContextKernelError(ValueError):
    """Raised when O cannot construct a trustworthy decision context."""


_CONTROL_PATHS = {
    "work_execution_state": Path("agi/WORK_EXECUTION_STATE.json"),
    "user_input_inbox": Path("agi/USER_INPUT_INBOX.json"),
    "work_strategy": Path("agi/WORK_STRATEGY.json"),
}


def _required_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContextKernelError(f"{label} must be an object")
    return deepcopy(dict(value))


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContextKernelError(f"{label} must be non-empty text")
    return value


def _required_int(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContextKernelError(f"{label} must be an integer >= {minimum}")
    return value


def _git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()  # noqa: S324 - Git object ID


def _load_source(
    root: Path,
    store: Store,
    source_id: str,
    path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    absolute = root / path
    if not absolute.is_file():
        raise ContextKernelError(f"missing mandatory context source: {path.as_posix()}")
    try:
        document = store.read_json(absolute, None)
    except (OSError, ValueError) as exc:
        raise ContextKernelError(
            f"malformed mandatory context source: {path.as_posix()}"
        ) from exc
    document = _required_mapping(document, path.as_posix())
    raw = absolute.read_bytes()
    identity = {
        "source_id": source_id,
        "authoritative_locator": path.as_posix(),
        "content_digest": store.stable_digest(document, length=64),
        "content_digest_algorithm": "sha256-canonical-json",
        "git_blob_sha": _git_blob_sha(raw),
    }
    return document, identity


def _source_record(
    identity: Mapping[str, Any],
    *,
    version: str,
    observed_at: str,
    projection: Mapping[str, Any],
    include_reason: str,
    selected_fields: list[str],
    excluded_fields: list[str],
    invalidates_on: list[str],
    depends_on: list[str],
    freshness: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        **deepcopy(dict(identity)),
        "role": "mandatory_control_context",
        "decision": "included",
        "include_reason": include_reason,
        "version": version,
        "observed_at": observed_at,
        "freshness": deepcopy(dict(freshness)),
        "depends_on": list(depends_on),
        "invalidates_on": list(invalidates_on),
        "selected_fields": list(selected_fields),
        "excluded_fields": list(excluded_fields),
        "projection": deepcopy(dict(projection)),
    }


def _validate_inbox(
    inbox: dict[str, Any],
    *,
    acknowledged_revision: int,
) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    revision = _required_int(inbox.get("revision"), "inbox.revision")
    if acknowledged_revision > revision:
        raise ContextKernelError(
            "acknowledged inbox revision exceeds authoritative inbox revision"
        )
    entries = inbox.get("entries")
    if not isinstance(entries, list):
        raise ContextKernelError("inbox.entries must be an array")
    active: list[dict[str, Any]] = []
    seen_sequences: set[int] = set()
    seen_ids: set[str] = set()
    for index, raw_entry in enumerate(entries):
        entry = _required_mapping(raw_entry, f"inbox.entries[{index}]")
        sequence = _required_int(
            entry.get("sequence"), f"inbox.entries[{index}].sequence", minimum=1
        )
        entry_id = _required_text(entry.get("id"), f"inbox.entries[{index}].id")
        if sequence in seen_sequences or entry_id in seen_ids:
            raise ContextKernelError("inbox entry sequence and id must be unique")
        if sequence > revision:
            raise ContextKernelError("inbox entry sequence exceeds inbox revision")
        seen_sequences.add(sequence)
        seen_ids.add(entry_id)
        directives = entry.get("directives", [])
        supersedes = entry.get("supersedes", [])
        if not isinstance(directives, list) or not all(
            isinstance(item, str) and item.strip() for item in directives
        ):
            raise ContextKernelError(f"inbox entry {entry_id} directives are malformed")
        if not isinstance(supersedes, list) or not all(
            isinstance(item, str) and item.strip() for item in supersedes
        ):
            raise ContextKernelError(f"inbox entry {entry_id} supersedes are malformed")
        if entry.get("status") == "active":
            active.append(entry)
    active.sort(key=lambda item: item["sequence"])
    catalog = [
        {
            "sequence": entry["sequence"],
            "id": entry["id"],
            "kind": entry.get("kind"),
            "summary": entry.get("summary"),
            "supersedes": deepcopy(entry.get("supersedes", [])),
        }
        for entry in active
    ]
    unacknowledged = [
        {
            "sequence": entry["sequence"],
            "id": entry["id"],
            "summary": entry.get("summary"),
            "directives": deepcopy(entry.get("directives", [])),
            "supersedes": deepcopy(entry.get("supersedes", [])),
        }
        for entry in active
        if entry["sequence"] > acknowledged_revision
    ]
    latest = active[-1] if active else {}
    latest_projection = {
        "sequence": latest.get("sequence"),
        "id": latest.get("id"),
        "summary": latest.get("summary"),
        "directives": deepcopy(latest.get("directives", [])),
        "supersedes": deepcopy(latest.get("supersedes", [])),
    }
    return revision, catalog, unacknowledged, latest_projection


def verify_decision_context_manifest(
    manifest: Mapping[str, Any],
    *,
    store: Store,
) -> dict[str, Any]:
    value = _required_mapping(manifest, "decision_context")
    supplied = value.pop("manifest_digest", None)
    if not isinstance(supplied, str) or not supplied:
        raise ContextKernelError("decision_context.manifest_digest is required")
    expected = store.stable_digest(value, length=64)
    if supplied != expected:
        raise ContextKernelError("decision context manifest digest mismatch")
    sources = value.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ContextKernelError("decision context sources must be non-empty")
    source_ids = [source.get("source_id") for source in sources if isinstance(source, dict)]
    if len(source_ids) != len(sources) or len(set(source_ids)) != len(source_ids):
        raise ContextKernelError("decision context source ids must be unique")
    required = set(value.get("policy", {}).get("mandatory_source_ids", []))
    if set(source_ids) != required:
        raise ContextKernelError("decision context mandatory source set mismatch")
    value["manifest_digest"] = supplied
    return value


def build_root_decision_context(
    root: Path,
    *,
    run_id: str,
    payload_snapshot: Mapping[str, Any],
    store: Store | None = None,
) -> dict[str, Any] | None:
    """Build O's immutable Root decision-control projection.

    Generic Work bridge users without any AGI control-plane files remain valid.
    Once any control source is present, however, the Context Kernel is enabled
    and every mandatory source must be present and mutually consistent.
    """

    root = root.resolve()
    store = store or Store(root)
    present = {
        source_id: (root / path).is_file()
        for source_id, path in _CONTROL_PATHS.items()
    }
    if not any(present.values()):
        return None
    missing = [source_id for source_id, exists in present.items() if not exists]
    if missing:
        raise ContextKernelError(
            "partial Context Kernel control plane; missing: " + ", ".join(missing)
        )

    state, state_identity = _load_source(
        root, store, "work_execution_state", _CONTROL_PATHS["work_execution_state"]
    )
    inbox, inbox_identity = _load_source(
        root, store, "user_input_inbox", _CONTROL_PATHS["user_input_inbox"]
    )
    strategy, strategy_identity = _load_source(
        root, store, "work_strategy", _CONTROL_PATHS["work_strategy"]
    )
    snapshot_path = Path(".continual") / "runs" / run_id / "snapshot.json"
    snapshot, snapshot_identity = _load_source(
        root, store, "native_run_snapshot", snapshot_path
    )
    supplied_snapshot = _required_mapping(payload_snapshot, "payload.snapshot")

    if state.get("mode") != "work_o_engine_single_writer":
        raise ContextKernelError("unsupported work execution state mode")
    if state.get("status") != "running":
        raise ContextKernelError("Root decision context requires a running Work lease")
    execution_id = _required_text(state.get("execution_id"), "state.execution_id")
    owner_kind = _required_text(state.get("owner_kind"), "state.owner_kind")
    generation = _required_int(
        state.get("lease_generation"), "state.lease_generation", minimum=1
    )
    fence = _required_text(state.get("fence_token"), "state.fence_token")
    heartbeat_at = _required_text(state.get("heartbeat_at"), "state.heartbeat_at")
    stale_after = _required_int(
        state.get("stale_after_seconds"), "state.stale_after_seconds", minimum=1
    )
    if state.get("active_run_id") != run_id:
        raise ContextKernelError("Work lease active_run_id does not match Root run")
    inbox_state = _required_mapping(
        state.get("user_input_inbox"), "state.user_input_inbox"
    )
    if inbox_state.get("path") != _CONTROL_PATHS["user_input_inbox"].as_posix():
        raise ContextKernelError("Work lease inbox path mismatch")
    acknowledged = _required_int(
        inbox_state.get("highest_acknowledged_revision"),
        "state.user_input_inbox.highest_acknowledged_revision",
    )
    inbox_revision, active_catalog, unacknowledged, latest_direction = _validate_inbox(
        inbox, acknowledged_revision=acknowledged
    )
    pending_input = state.get("pending_user_input")
    if isinstance(pending_input, Mapping) and pending_input.get("revision") == inbox_revision:
        bound_blob = pending_input.get("inbox_blob_sha")
        if bound_blob is not None and bound_blob != inbox_identity["git_blob_sha"]:
            raise ContextKernelError("Work lease inbox blob binding mismatch")

    strategy_objective = _required_text(
        strategy.get("objective") or strategy.get("optimization_objective"),
        "strategy.objective",
    )
    strategy_updated_at = _required_text(
        strategy.get("updated_at"), "strategy.updated_at"
    )
    if snapshot.get("run_id") != run_id or supplied_snapshot.get("run_id") != run_id:
        raise ContextKernelError("native snapshot run_id mismatch")
    snapshot_revision = _required_int(snapshot.get("revision"), "snapshot.revision")
    if supplied_snapshot.get("revision") != snapshot_revision:
        raise ContextKernelError("payload snapshot revision is not the durable run revision")
    snapshot_updated_at = _required_text(
        snapshot.get("updated_at"), "snapshot.updated_at"
    )
    if supplied_snapshot.get("updated_at") != snapshot_updated_at:
        raise ContextKernelError("payload snapshot timestamp is not the durable run timestamp")

    state_projection = {
        "status": state["status"],
        "owner_kind": owner_kind,
        "execution_id": execution_id,
        "lease_generation": generation,
        "fence_token_digest": store.stable_digest(fence, length=64),
        "active_run_id": run_id,
        "highest_acknowledged_inbox_revision": acknowledged,
        "effective_user_input_interpretation": inbox_state.get("application_note"),
        "result_publication_policy": deepcopy(state.get("result_publication_policy")),
        "normal_completion_condition": state.get("primary_run_contract", {}).get(
            "normal_completion_condition"
        ),
    }
    inbox_projection = {
        "revision": inbox_revision,
        "highest_acknowledged_revision": acknowledged,
        "active_entry_catalog": active_catalog,
        "unacknowledged_entries": unacknowledged,
        "latest_active_direction": latest_direction,
        "supersede_resolution": {
            "mode": "canonical_state_interpretation_plus_raw_relationships",
            "canonical_interpretation": inbox_state.get("application_note"),
            "warning": "Entry-level supersedes may be partial; do not delete unaffected directives.",
        },
    }
    strategy_projection = {
        "optimization_objective": strategy_objective,
        "execution_rules": deepcopy(strategy.get("execution_rules", {})),
        "claim_boundary": deepcopy(strategy.get("claim_boundary", {})),
        "immediate_sequence": deepcopy(strategy.get("immediate_sequence", [])),
        "context_management": deepcopy(strategy.get("context_management", {})),
    }
    snapshot_projection = {
        "run_id": run_id,
        "revision": snapshot_revision,
        "status": snapshot.get("status"),
        "phase": snapshot.get("phase"),
        "current_component": snapshot.get("current_component"),
        "current_unit": snapshot.get("current_unit"),
        "task_completion_verdict": snapshot.get("task_completion_verdict"),
        "unit_completion_verdict": snapshot.get("unit_completion_verdict"),
        "last_result_ref": snapshot.get("last_result_ref"),
    }

    sources = [
        _source_record(
            state_identity,
            version=f"generation:{generation};execution:{execution_id};heartbeat:{heartbeat_at}",
            observed_at=heartbeat_at,
            projection=state_projection,
            include_reason="Bind every Root decision to the sole writer, fence, inbox cursor, publication rule, and completion authority.",
            selected_fields=list(state_projection),
            excluded_fields=[
                "full historical integration detail",
                "raw fence token (digest retained)",
                "redundant validation transcripts",
            ],
            invalidates_on=[
                "execution_id change",
                "lease_generation change",
                "fence token change",
                "status change",
                "heartbeat/source content change",
            ],
            depends_on=[],
            freshness={
                "kind": "lease_heartbeat",
                "stale_after_seconds": stale_after,
                "recheck_at_effect_or_recovery_boundary": True,
            },
        ),
        _source_record(
            inbox_identity,
            version=f"revision:{inbox_revision}",
            observed_at=_required_text(inbox.get("updated_at"), "inbox.updated_at"),
            projection=inbox_projection,
            include_reason="Prevent a live user direction known outside O from disappearing at the next Root decision boundary.",
            selected_fields=list(inbox_projection),
            excluded_fields=[
                "full directives for acknowledged non-latest entries (catalog and canonical interpretation retained)",
                "inactive entry payloads",
            ],
            invalidates_on=[
                "inbox revision change",
                "entry status change",
                "supersede relationship change",
                "content digest change",
            ],
            depends_on=["work_execution_state.highest_acknowledged_inbox_revision"],
            freshness={"kind": "append_only_revision", "latest_revision": inbox_revision},
        ),
        _source_record(
            strategy_identity,
            version=f"updated_at:{strategy_updated_at}",
            observed_at=strategy_updated_at,
            projection=strategy_projection,
            include_reason="Keep the terminal objective, execution policy, claim boundary, and selected work sequence inside O's decision context.",
            selected_fields=list(strategy_projection),
            excluded_fields=[
                "full historical bottleneck detail",
                "resolved deferrals",
                "raw benchmark transcripts",
            ],
            invalidates_on=[
                "optimization objective change",
                "execution rule change",
                "claim boundary change",
                "context-management policy change",
                "content digest change",
            ],
            depends_on=["user_input_inbox.supersede_resolution"],
            freshness={"kind": "content_version", "updated_at": strategy_updated_at},
        ),
        _source_record(
            snapshot_identity,
            version=f"revision:{snapshot_revision}",
            observed_at=snapshot_updated_at,
            projection=snapshot_projection,
            include_reason="Bind the decision to the exact native O continuation rather than outer-session recollection.",
            selected_fields=list(snapshot_projection),
            excluded_fields=[
                "duplicate full snapshot payload (already frozen separately)",
                "historical event bodies",
            ],
            invalidates_on=[
                "snapshot revision change",
                "phase change",
                "current unit change",
                "result reference change",
            ],
            depends_on=["work_execution_state.active_run_id"],
            freshness={"kind": "monotonic_revision", "revision": snapshot_revision},
        ),
    ]
    manifest = {
        "schema_version": 1,
        "kernel": "o-decision-context-control-plane-v1",
        "component": "root",
        "run_id": run_id,
        "policy": {
            "raw_authority_location": "authoritative source systems",
            "decision_authority": "O Engine",
            "outer_session_role": "observation_and_effect_executor_only",
            "external_fact_may_affect_decision_only_after_o_ingestion": True,
            "copy_all_raw_context": False,
            "mandatory_source_ids": [source["source_id"] for source in sources],
            "optional_context": "selected recursively after mandatory control context",
        },
        "source_clock": {
            source["source_id"]: {
                "version": source["version"],
                "content_digest": source["content_digest"],
                "git_blob_sha": source["git_blob_sha"],
            }
            for source in sources
        },
        "sources": sources,
        "excluded_context": [
            {
                "source_id": "outer_session_untracked_memory",
                "decision": "excluded",
                "reason": "No provenance, revision, replay, or invalidation binding; observations must first become O receipts or canonical source updates.",
            },
            {
                "source_id": "all_raw_repository_and_provider_data",
                "decision": "excluded",
                "reason": "Duplicating every payload would create stale competing authority, context overload, and unnecessary secret exposure.",
            },
        ],
        "invalidation": {
            "frozen_request_rule": "A persisted request keeps this exact manifest for replay.",
            "next_boundary_rule": "Any source-clock change produces a new manifest and a new request identity.",
            "effect_rule": "Critical lease, user-revocation, and action-constraint sources require a fresh dispatch-time check before side effects.",
        },
    }
    manifest["manifest_digest"] = store.stable_digest(manifest, length=64)
    return verify_decision_context_manifest(manifest, store=store)
