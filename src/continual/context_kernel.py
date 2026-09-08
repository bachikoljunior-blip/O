from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .context_observations import (
    ContextObservationError,
    verify_context_observation_ledger,
)
from .ci_source_observation import (
    CiSourceObservationError,
    verify_ci_source_observation,
)
from .effective_directives import (
    EffectiveDirectiveError,
    compile_effective_directives,
)
from .store import Store
from .work_source_observation import (
    WorkSourceObservationError,
    verify_work_source_observation,
)


class ContextKernelError(ValueError):
    """Raised when O cannot construct a trustworthy decision context."""


_CONTROL_PATHS = {
    "work_execution_state": Path("agi/WORK_EXECUTION_STATE.json"),
    "user_input_inbox": Path("agi/USER_INPUT_INBOX.json"),
    "effective_user_directives": Path("agi/USER_DIRECTIVE_EVENTS.json"),
    "work_strategy": Path("agi/WORK_STRATEGY.json"),
    "external_observations": Path("agi/CONTEXT_OBSERVATION_LEDGER.json"),
}

SEMANTIC_CONTEXT_COMPONENTS = frozenset(
    {"root", "execute", "task_evaluate", "consolidate_episode", "learn"}
)


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


def _utc_timestamp(value: Any, label: str) -> datetime:
    text = _required_text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContextKernelError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ContextKernelError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def validate_mandatory_work_source_freshness(
    state: Mapping[str, Any],
    *,
    observed_at: str,
) -> dict[str, Any]:
    """Fail closed when the local mandatory Work authority is not request-ready.

    This validates the exact source bytes available to request construction.  It
    intentionally does not claim that the caller observed the latest remote
    source revision; authoritative remote observation is a separate boundary.
    """

    exact = _required_mapping(state, "state")
    status = _required_text(exact.get("status"), "state.status")
    if status != "running":
        raise ContextKernelError("decision context requires a running Work lease")
    owner_kind = _required_text(exact.get("owner_kind"), "state.owner_kind")
    execution_id = _required_text(exact.get("execution_id"), "state.execution_id")
    generation = _required_int(
        exact.get("lease_generation"), "state.lease_generation", minimum=1
    )
    fence = _required_text(exact.get("fence_token"), "state.fence_token")
    stale_after = _required_int(
        exact.get("stale_after_seconds"), "state.stale_after_seconds", minimum=1
    )
    max_future_skew = _required_int(
        exact.get("max_future_skew_seconds", 120),
        "state.max_future_skew_seconds",
    )
    heartbeat = _utc_timestamp(exact.get("heartbeat_at"), "state.heartbeat_at")
    now = _utc_timestamp(observed_at, "request creation time")
    age = (now - heartbeat).total_seconds()
    if age > stale_after:
        raise ContextKernelError("decision context Work heartbeat is stale")
    if age < -max_future_skew:
        raise ContextKernelError("decision context Work heartbeat is future-skewed")
    return {
        "status": status,
        "owner_kind": owner_kind,
        "execution_id": execution_id,
        "lease_generation": generation,
        "fence_token": fence,
        "heartbeat_at": heartbeat.isoformat().replace("+00:00", "Z"),
        "stale_after_seconds": stale_after,
        "max_future_skew_seconds": max_future_skew,
        "observed_at": now.isoformat().replace("+00:00", "Z"),
        "age_seconds": age,
        "source_scope": "local_bytes_only_not_remote_revision_proof",
    }


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
        raw = absolute.read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ContextKernelError(
            f"malformed mandatory context source: {path.as_posix()}"
        ) from exc
    document = _required_mapping(document, path.as_posix())
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
    expected_component: str | None = None,
) -> dict[str, Any]:
    value = _required_mapping(manifest, "decision_context")
    supplied = value.pop("manifest_digest", None)
    if not isinstance(supplied, str) or not supplied:
        raise ContextKernelError("decision_context.manifest_digest is required")
    expected = store.stable_digest(value, length=64)
    if supplied != expected:
        raise ContextKernelError("decision context manifest digest mismatch")
    component = _required_text(value.get("component"), "decision_context.component")
    if component not in SEMANTIC_CONTEXT_COMPONENTS:
        raise ContextKernelError("unsupported decision context component")
    if expected_component is not None and component != expected_component:
        raise ContextKernelError("decision context component binding mismatch")
    sources = value.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ContextKernelError("decision context sources must be non-empty")
    source_ids = [source.get("source_id") for source in sources if isinstance(source, dict)]
    if len(source_ids) != len(sources) or len(set(source_ids)) != len(source_ids):
        raise ContextKernelError("decision context source ids must be unique")
    policy = _required_mapping(value.get("policy"), "decision_context.policy")
    mandatory = policy.get("mandatory_source_ids")
    if not isinstance(mandatory, list) or not all(
        isinstance(source_id, str) and source_id for source_id in mandatory
    ):
        raise ContextKernelError(
            "decision context mandatory_source_ids must be non-empty text"
        )
    if len(set(mandatory)) != len(mandatory):
        raise ContextKernelError(
            "decision context mandatory_source_ids must be unique"
        )
    required = set(mandatory)
    if set(source_ids) != required:
        raise ContextKernelError("decision context mandatory source set mismatch")
    source_clock = _required_mapping(
        value.get("source_clock"), "decision_context.source_clock"
    )
    if set(source_clock) != required:
        raise ContextKernelError("decision context source clock set mismatch")
    for source in sources:
        source_id = source["source_id"]
        clock = _required_mapping(
            source_clock.get(source_id),
            f"decision_context.source_clock.{source_id}",
        )
        expected_clock = {
            "version": source.get("version"),
            "content_digest": source.get("content_digest"),
            "git_blob_sha": source.get("git_blob_sha"),
        }
        if clock != expected_clock:
            raise ContextKernelError(
                f"decision context source clock binding mismatch: {source_id}"
            )
    value["manifest_digest"] = supplied
    return value


def build_decision_context(
    root: Path,
    *,
    run_id: str,
    component: str,
    payload_snapshot: Mapping[str, Any],
    store: Store | None = None,
) -> dict[str, Any] | None:
    """Build O's immutable semantic-component decision-control projection.

    Generic Work bridge users without any AGI control-plane files remain valid.
    Once any control source is present, however, the Context Kernel is enabled
    and every mandatory source must be present and mutually consistent.
    """

    root = root.resolve()
    store = store or Store(root)
    if component not in SEMANTIC_CONTEXT_COMPONENTS:
        raise ContextKernelError(f"unsupported decision context component: {component}")
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
    request_clock = store.utc_now()
    source_readiness = validate_mandatory_work_source_freshness(
        state,
        observed_at=request_clock,
    )
    try:
        authority_observation = verify_work_source_observation(
            root,
            run_id=run_id,
            state=state,
            state_blob_sha=state_identity["git_blob_sha"],
            now=request_clock,
        )
    except WorkSourceObservationError as exc:
        raise ContextKernelError(
            f"authoritative Work source observation failed: {exc}"
        ) from exc
    try:
        ci_observation = verify_ci_source_observation(
            root,
            run_id=run_id,
            state=state,
            now=request_clock,
        )
    except CiSourceObservationError as exc:
        raise ContextKernelError(
            f"decision-relevant CI source observation failed: {exc}"
        ) from exc
    inbox, inbox_identity = _load_source(
        root, store, "user_input_inbox", _CONTROL_PATHS["user_input_inbox"]
    )
    directive_ledger, directive_ledger_identity = _load_source(
        root,
        store,
        "effective_user_directives",
        _CONTROL_PATHS["effective_user_directives"],
    )
    strategy, strategy_identity = _load_source(
        root, store, "work_strategy", _CONTROL_PATHS["work_strategy"]
    )
    observation_ledger, observation_ledger_identity = _load_source(
        root,
        store,
        "external_observations",
        _CONTROL_PATHS["external_observations"],
    )
    snapshot_path = Path(".continual") / "runs" / run_id / "snapshot.json"
    snapshot, snapshot_identity = _load_source(
        root, store, "native_run_snapshot", snapshot_path
    )
    supplied_snapshot = _required_mapping(payload_snapshot, "payload.snapshot")

    if state.get("mode") != "work_o_engine_single_writer":
        raise ContextKernelError("unsupported work execution state mode")
    execution_id = source_readiness["execution_id"]
    owner_kind = source_readiness["owner_kind"]
    generation = source_readiness["lease_generation"]
    fence = source_readiness["fence_token"]
    heartbeat_at = _required_text(state.get("heartbeat_at"), "state.heartbeat_at")
    stale_after = source_readiness["stale_after_seconds"]
    max_future_skew = source_readiness["max_future_skew_seconds"]
    if state.get("active_run_id") != run_id:
        raise ContextKernelError("Work lease active_run_id does not match decision run")
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
    try:
        effective_directives = compile_effective_directives(
            inbox,
            directive_ledger,
            state=state,
            strategy=strategy,
            store=store,
        )
    except EffectiveDirectiveError as exc:
        raise ContextKernelError(
            f"effective directive compilation failed: {exc}"
        ) from exc
    try:
        verified_observations = verify_context_observation_ledger(
            root, observation_ledger
        )
    except ContextObservationError as exc:
        raise ContextKernelError(
            f"external observation verification failed: {exc}"
        ) from exc
    interpreted_at = _required_text(
        directive_ledger.get("source", {}).get("interpreted_at"),
        "directive ledger.source.interpreted_at",
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
    if (
        store.stable_digest(supplied_snapshot, length=64)
        != snapshot_identity["content_digest"]
    ):
        raise ContextKernelError(
            "payload snapshot content is not the exact durable run snapshot"
        )

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
    if authority_observation is not None:
        state_projection["authoritative_source_observation"] = deepcopy(
            authority_observation
        )
    inbox_projection = {
        "revision": inbox_revision,
        "highest_acknowledged_revision": acknowledged,
        "active_entry_catalog": active_catalog,
        "unacknowledged_entries": unacknowledged,
        "latest_active_direction": latest_direction,
        "supersede_resolution": {
            "mode": "typed_atom_level_effective_policy",
            "effective_policy_digest": effective_directives[
                "effective_policy_digest"
            ],
            "ledger_locator": _CONTROL_PATHS[
                "effective_user_directives"
            ].as_posix(),
        },
    }
    strategy_projection = {
        "optimization_objective": strategy_objective,
        "execution_rules": deepcopy(strategy.get("execution_rules", {})),
        "claim_boundary": deepcopy(strategy.get("claim_boundary", {})),
        "immediate_sequence": deepcopy(strategy.get("immediate_sequence", [])),
        "context_management": deepcopy(strategy.get("context_management", {})),
        "negative_evidence_scope_policy": deepcopy(
            strategy.get("negative_evidence_scope_policy", {})
        ),
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
    observation_projection = {
        "entries": [
            {
                "source_id": entry["source_id"],
                "observation_id": entry["observation_id"],
                "request_digest": entry["request_digest"],
                "receipt_digest": entry["receipt_digest"],
                "authoritative_locator": entry["authoritative_locator"],
                "source_version": deepcopy(entry["source_version"]),
                "observed_at": entry["observed_at"],
                "freshness": deepcopy(entry["freshness"]),
                "projection": deepcopy(entry["projection"]),
                "evidence_class": entry["evidence_class"],
                "unknowns": deepcopy(entry["unknowns"]),
            }
            for entry in verified_observations
        ]
    }
    if ci_observation is not None:
        observation_projection["entries"].append(deepcopy(ci_observation))
        observation_projection["entries"].sort(
            key=lambda entry: (entry["source_id"], entry["observation_id"])
        )
    observation_observed_at = max(
        entry["observed_at"] for entry in observation_projection["entries"]
    )

    sources = [
        _source_record(
            state_identity,
            version=(
                f"generation:{generation};execution:{execution_id};"
                f"heartbeat:{heartbeat_at}"
                + (
                    f";remote-receipt:{authority_observation['receipt_digest']}"
                    if authority_observation is not None
                    else ""
                )
            ),
            observed_at=heartbeat_at,
            projection=state_projection,
            include_reason="Bind every semantic decision to the sole writer, fence, inbox cursor, publication rule, and completion authority.",
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
                "max_future_skew_seconds": max_future_skew,
                "recheck_at_effect_or_recovery_boundary": True,
                **(
                    {
                        "authoritative_observation": deepcopy(
                            authority_observation
                        )
                    }
                    if authority_observation is not None
                    else {}
                ),
            },
        ),
        _source_record(
            inbox_identity,
            version=f"revision:{inbox_revision}",
            observed_at=_required_text(inbox.get("updated_at"), "inbox.updated_at"),
            projection=inbox_projection,
            include_reason="Prevent a live user direction known outside O from disappearing at the next semantic decision boundary.",
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
            directive_ledger_identity,
            version=(
                f"revision:{inbox_revision};policy:"
                f"{effective_directives['effective_policy_digest']}"
            ),
            observed_at=interpreted_at,
            projection=effective_directives,
            include_reason="Compile exact user-input bytes into O-owned typed effective policy without runtime free-text supersede inference.",
            selected_fields=list(effective_directives),
            excluded_fields=[
                "raw directive text (retained in authoritative inbox)",
                "superseded atom values (ids and superseders retained)",
                "unreviewed outer-session interpretation",
            ],
            invalidates_on=[
                "inbox source revision or digest change",
                "interpretation ledger content change",
                "atom source binding change",
                "effective policy digest change",
                "runtime authority contradiction",
            ],
            depends_on=[
                "user_input_inbox.content_digest",
                "work_execution_state.result_publication_policy",
                "work_strategy.execution_rules",
            ],
            freshness={
                "kind": "source_bound_interpretation",
                "source_revision": inbox_revision,
                "interpreted_at": interpreted_at,
            },
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
            depends_on=["effective_user_directives.effective_policy_digest"],
            freshness={"kind": "content_version", "updated_at": strategy_updated_at},
        ),
        _source_record(
            observation_ledger_identity,
            version=(
                "receipts:"
                + store.stable_digest(observation_projection, length=64)
            ),
            observed_at=observation_observed_at,
            projection=observation_projection,
            include_reason="Allow external facts to affect a semantic decision only after an O-requested, request-bound, mechanically verified receipt is ingested.",
            selected_fields=list(observation_projection),
            excluded_fields=[
                "raw connector responses",
                "unrequested outer-session facts",
                "secret-bearing or unbounded provider payloads",
            ],
            invalidates_on=[
                "observation ledger content change",
                "request or receipt digest change",
                "source version change",
                "freshness or invalidation policy change",
            ],
            depends_on=["native_run_snapshot.current_unit"],
            freshness={
                "kind": "verified_receipt_set",
                "entry_count": len(verified_observations),
            },
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
        "component": component,
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
    return verify_decision_context_manifest(
        manifest,
        store=store,
        expected_component=component,
    )


def build_root_decision_context(
    root: Path,
    *,
    run_id: str,
    payload_snapshot: Mapping[str, Any],
    store: Store | None = None,
) -> dict[str, Any] | None:
    """Compatibility wrapper for callers that explicitly construct Root context."""

    return build_decision_context(
        root,
        run_id=run_id,
        component="root",
        payload_snapshot=payload_snapshot,
        store=store,
    )


def _source_by_id(
    manifest: Mapping[str, Any], source_id: str
) -> dict[str, Any]:
    for value in manifest.get("sources", []):
        if isinstance(value, Mapping) and value.get("source_id") == source_id:
            return deepcopy(dict(value))
    raise ContextKernelError(f"decision context is missing source: {source_id}")


def _stable_ci_observation(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable CI fields that may authorize one merge action."""

    exact = _required_mapping(value, "CI source observation")
    freshness = _required_mapping(exact.get("freshness"), "CI source freshness")
    if freshness.get("kind") != "max_age":
        raise ContextKernelError("CI source freshness kind mismatch")
    return {
        "source_id": _required_text(exact.get("source_id"), "CI source_id"),
        "observation_id": _required_text(
            exact.get("observation_id"), "CI observation_id"
        ),
        "request_digest": _required_text(
            exact.get("request_digest"), "CI request_digest"
        ),
        "receipt_digest": _required_text(
            exact.get("receipt_digest"), "CI receipt_digest"
        ),
        "authoritative_locator": _required_text(
            exact.get("authoritative_locator"), "CI authoritative_locator"
        ),
        "source_version": deepcopy(
            _required_mapping(exact.get("source_version"), "CI source_version")
        ),
        "projection": deepcopy(
            _required_mapping(exact.get("projection"), "CI projection")
        ),
        "evidence_class": _required_text(
            exact.get("evidence_class"), "CI evidence_class"
        ),
        "unknowns": deepcopy(exact.get("unknowns")),
        "claim_scope": _required_text(
            exact.get("claim_scope"), "CI claim_scope"
        ),
        "freshness": {
            "kind": "max_age",
            "max_age_seconds": _required_int(
                freshness.get("max_age_seconds"),
                "CI freshness.max_age_seconds",
                minimum=1,
            ),
        },
    }


def _ci_merge_authorization(
    root: Path,
    *,
    run_id: str,
    state: Mapping[str, Any],
    manifest: Mapping[str, Any],
    action: Mapping[str, Any],
    store: Store,
) -> dict[str, Any] | None:
    """Cross-bind one GitHub merge action to fresh manifest CI evidence."""

    if action.get("kind") != "github_merge_pull_request":
        return None
    target = _required_mapping(action.get("target"), "merge action target")
    parameters = _required_mapping(
        action.get("parameters"), "merge action parameters"
    )
    if set(target) != {"repository", "pull_request"}:
        raise ContextKernelError(
            "merge action target must contain repository and pull_request"
        )
    if set(parameters) - {"expected_head_sha", "merge_method"}:
        raise ContextKernelError("merge action contains unsupported parameters")
    repository = _required_text(target.get("repository"), "merge repository")
    pull_request = _required_int(
        target.get("pull_request"), "merge pull_request", minimum=1
    )
    expected_head = _required_text(
        parameters.get("expected_head_sha"), "merge expected_head_sha"
    )
    if len(expected_head) != 40 or any(
        character not in "0123456789abcdef" for character in expected_head
    ):
        raise ContextKernelError("merge expected_head_sha must be a full SHA")
    merge_method = parameters.get("merge_method", "merge")
    if merge_method not in {"merge", "squash", "rebase"}:
        raise ContextKernelError("unsupported merge method")

    observations = _source_by_id(manifest, "external_observations")
    projection = _required_mapping(
        observations.get("projection"), "manifest observation projection"
    )
    entries = projection.get("entries")
    if not isinstance(entries, list):
        raise ContextKernelError("manifest observation entries must be an array")
    manifest_ci = [
        entry
        for entry in entries
        if isinstance(entry, Mapping)
        and entry.get("source_id") == "github_actions_ci"
    ]
    if len(manifest_ci) != 1:
        raise ContextKernelError(
            "merge action requires exactly one manifest CI observation"
        )
    try:
        current = verify_ci_source_observation(
            root,
            run_id=run_id,
            state=state,
            now=store.utc_now(),
        )
    except CiSourceObservationError as exc:
        raise ContextKernelError(
            f"merge CI source observation failed: {exc}"
        ) from exc
    if current is None:
        raise ContextKernelError("merge action requires CI source policy")
    bound = _stable_ci_observation(manifest_ci[0])
    current_bound = _stable_ci_observation(current)
    if bound != current_bound:
        raise ContextKernelError(
            "merge CI source changed since semantic decision"
        )
    policy = _required_mapping(
        state.get("ci_source_observation_policy"), "CI source policy"
    )
    if repository != policy.get("repository_full_name"):
        raise ContextKernelError("merge repository does not match CI source")
    if expected_head != current_bound["source_version"].get("exact_head_sha"):
        raise ContextKernelError("merge head does not match CI source")
    workflow = _required_mapping(
        current_bound["projection"].get("workflow_run"),
        "CI workflow run projection",
    )
    jobs = current_bound["projection"].get("required_jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ContextKernelError("CI required jobs projection is empty")
    return {
        "repository_full_name": repository,
        "pull_request": pull_request,
        "expected_head_sha": expected_head,
        "merge_method": merge_method,
        "observation_id": current_bound["observation_id"],
        "request_digest": current_bound["request_digest"],
        "receipt_digest": current_bound["receipt_digest"],
        "workflow_run_id": workflow.get("id"),
        "workflow_id": workflow.get("workflow_id"),
        "required_jobs": deepcopy(jobs),
        "claim_scope": current_bound["claim_scope"],
    }


def build_effect_dispatch_context(
    root: Path,
    *,
    request: Mapping[str, Any],
    action: Mapping[str, Any],
    store: Store | None = None,
) -> dict[str, Any] | None:
    """Build the stable critical context that must still hold at dispatch.

    Non-control-plane requests without a decision manifest keep their legacy
    behavior. Once any control source exists, an Execute request without the
    manifest cannot authorize a new effect; frozen history is not rewritten.
    A manifest-bearing Execute request is revalidated against the
    authoritative control files. Volatile heartbeat identity is
    intentionally excluded from the returned digest: its liveness is checked
    independently, while generation, execution, fence, status, user policy,
    strategy constraints, request, manifest, and action identity remain exact.
    """

    root = root.resolve()
    store = store or Store(root)
    exact_request = _required_mapping(request, "request")
    payload = _required_mapping(exact_request.get("payload"), "request.payload")
    raw_manifest = payload.get("decision_context")
    if raw_manifest is None:
        if any((root / path).is_file() for path in _CONTROL_PATHS.values()):
            raise ContextKernelError(
                "control-plane effect requires an Execute decision context"
            )
        return None
    manifest = verify_decision_context_manifest(
        _required_mapping(raw_manifest, "request decision_context"),
        store=store,
        expected_component="execute",
    )
    run_id = _required_text(exact_request.get("run_id"), "request.run_id")
    if manifest.get("run_id") != run_id:
        raise ContextKernelError("dispatch manifest run_id mismatch")

    state, _ = _load_source(
        root, store, "work_execution_state", _CONTROL_PATHS["work_execution_state"]
    )
    inbox, inbox_identity = _load_source(
        root, store, "user_input_inbox", _CONTROL_PATHS["user_input_inbox"]
    )
    ledger, ledger_identity = _load_source(
        root,
        store,
        "effective_user_directives",
        _CONTROL_PATHS["effective_user_directives"],
    )
    strategy, strategy_identity = _load_source(
        root, store, "work_strategy", _CONTROL_PATHS["work_strategy"]
    )

    state_source = _source_by_id(manifest, "work_execution_state")
    inbox_source = _source_by_id(manifest, "user_input_inbox")
    directives_source = _source_by_id(manifest, "effective_user_directives")
    strategy_source = _source_by_id(manifest, "work_strategy")
    state_projection = _required_mapping(
        state_source.get("projection"), "manifest work state projection"
    )

    status = _required_text(state.get("status"), "state.status")
    if status != "running":
        raise ContextKernelError("effect dispatch requires a running Work lease")
    execution_id = _required_text(state.get("execution_id"), "state.execution_id")
    owner_kind = _required_text(state.get("owner_kind"), "state.owner_kind")
    generation = _required_int(
        state.get("lease_generation"), "state.lease_generation", minimum=1
    )
    fence = _required_text(state.get("fence_token"), "state.fence_token")
    if state.get("active_run_id") != run_id:
        raise ContextKernelError("effect dispatch active run mismatch")
    inbox_state = _required_mapping(
        state.get("user_input_inbox"), "state.user_input_inbox"
    )
    acknowledged = _required_int(
        inbox_state.get("highest_acknowledged_revision"),
        "state highest acknowledged inbox revision",
    )
    inbox_revision = _required_int(inbox.get("revision"), "inbox.revision")
    if acknowledged != inbox_revision:
        raise ContextKernelError("effect dispatch has unacknowledged user input")

    stable_authority = {
        "status": status,
        "owner_kind": owner_kind,
        "execution_id": execution_id,
        "lease_generation": generation,
        "fence_token_digest": store.stable_digest(fence, length=64),
        "active_run_id": run_id,
        "highest_acknowledged_inbox_revision": acknowledged,
    }
    for key, value in stable_authority.items():
        if state_projection.get(key) != value:
            raise ContextKernelError(
                f"effect dispatch authority changed since semantic decision: {key}"
            )
    if state_projection.get("result_publication_policy") != state.get(
        "result_publication_policy"
    ):
        raise ContextKernelError(
            "effect dispatch publication constraint changed since semantic decision"
        )

    stale_after = _required_int(
        state.get("stale_after_seconds"), "state.stale_after_seconds", minimum=1
    )
    max_future_skew = _required_int(
        state.get("max_future_skew_seconds", 120),
        "state.max_future_skew_seconds",
    )
    manifest_freshness = _required_mapping(
        state_source.get("freshness"), "manifest work state freshness"
    )
    if manifest_freshness.get("stale_after_seconds") != stale_after:
        raise ContextKernelError(
            "effect dispatch heartbeat policy changed since semantic decision"
        )
    if manifest_freshness.get("max_future_skew_seconds", 120) != max_future_skew:
        raise ContextKernelError(
            "effect dispatch heartbeat policy changed since semantic decision"
        )
    heartbeat = _utc_timestamp(state.get("heartbeat_at"), "state.heartbeat_at")
    now = _utc_timestamp(store.utc_now(), "current time")
    age = (now - heartbeat).total_seconds()
    if age > stale_after:
        raise ContextKernelError("effect dispatch Work heartbeat is stale")
    if age < -max_future_skew:
        raise ContextKernelError("effect dispatch Work heartbeat is future-skewed")

    if inbox_source.get("content_digest") != inbox_identity["content_digest"]:
        raise ContextKernelError("effect dispatch user input changed")
    if directives_source.get("content_digest") != ledger_identity["content_digest"]:
        raise ContextKernelError("effect dispatch directive ledger changed")
    if strategy_source.get("content_digest") != strategy_identity["content_digest"]:
        raise ContextKernelError("effect dispatch strategy constraints changed")
    try:
        effective = compile_effective_directives(
            inbox,
            ledger,
            state=state,
            strategy=strategy,
            store=store,
        )
    except EffectiveDirectiveError as exc:
        raise ContextKernelError(
            f"effect dispatch directive compilation failed: {exc}"
        ) from exc
    directives_projection = _required_mapping(
        directives_source.get("projection"),
        "manifest effective directive projection",
    )
    if (
        directives_projection.get("effective_policy_digest")
        != effective["effective_policy_digest"]
    ):
        raise ContextKernelError("effect dispatch effective policy changed")

    exact_action = _required_mapping(action, "effect action")
    ci_merge = _ci_merge_authorization(
        root,
        run_id=run_id,
        state=state,
        manifest=manifest,
        action=exact_action,
        store=store,
    )
    context = {
        "schema_version": 1,
        "record_type": "effect_dispatch_context",
        "run_id": run_id,
        "invocation_id": _required_text(
            exact_request.get("invocation_id"), "request.invocation_id"
        ),
        "request_digest": _required_text(
            exact_request.get("request_digest"), "request.request_digest"
        ),
        "decision_context_manifest_digest": manifest["manifest_digest"],
        "stable_authority": stable_authority,
        "heartbeat_policy": {
            "stale_after_seconds": stale_after,
            "max_future_skew_seconds": max_future_skew,
        },
        "user_control": {
            "inbox_revision": inbox_revision,
            "inbox_content_digest": inbox_identity["content_digest"],
            "directive_ledger_content_digest": ledger_identity["content_digest"],
            "effective_policy_digest": effective["effective_policy_digest"],
        },
        "action_constraints": {
            "strategy_content_digest": strategy_identity["content_digest"],
            "execution_rules_digest": store.stable_digest(
                strategy.get("execution_rules", {}), length=64
            ),
            "publication_policy_digest": store.stable_digest(
                state.get("result_publication_policy", {}), length=64
            ),
            "action_digest": store.stable_digest(exact_action, length=64),
        },
    }
    if ci_merge is not None:
        context["ci_merge_authorization"] = ci_merge
    context["dispatch_context_digest"] = store.stable_digest(context, length=64)
    return context
