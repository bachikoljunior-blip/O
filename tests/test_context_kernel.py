from __future__ import annotations

import hashlib
import json
import shutil
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from continual.context_kernel import (
    ContextKernelError,
    SEMANTIC_CONTEXT_COMPONENTS,
    build_effect_dispatch_context,
    validate_mandatory_work_source_freshness,
    verify_decision_context_manifest,
)
from continual.engine import Engine
from continual.context_observations import observation_ledger_entry
from continual.store import Store
from continual.work_effect_dispatch import (
    dispatch_work_effect,
    load_authorized_work_effect,
)
from continual.work_effects import (
    WorkEffectError,
    authorize_work_effect,
    prepare_work_effect,
)
from continual.work_session import WorkModelClient, WorkModelPending, WorkSessionError
from continual.work_source_observation import (
    prepare_work_source_observation,
    record_work_source_observation_receipt,
)


RUN_ID = "run-context-kernel-test"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _blob_sha(path: Path) -> str:
    raw = path.read_bytes()
    return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()  # noqa: S324


def _persisted_request_boundary(root: Path) -> dict[str, bytes]:
    bases = (
        root / ".continual" / "runs" / RUN_ID / "invocations",
        root / ".continual" / "work-model" / "invocations",
    )
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for base in bases
        if base.exists()
        for path in sorted(base.rglob("*"))
        if path.is_file()
    }


def _root(tmp_path: Path) -> tuple[Path, dict]:
    shutil.copytree(Path("prompts"), tmp_path / "prompts")
    inbox = {
        "schema_version": 1,
        "revision": 1,
        "policy": {
            "append_only_semantics": True,
            "development_writer_lease_required_to_read": False,
            "development_writer_lease_required_to_append": False,
            "append_requires_expected_revision": True,
            "secrets_allowed": False,
            "apply_only_at_safe_semantic_boundaries": True,
            "user_input_is_not_automatic_proof": True,
        },
        "entries": [
            {
                "sequence": 1,
                "id": "direction-context-v1",
                "received_at": "2026-08-23T00:00:00Z",
                "kind": "user_direction",
                "status": "active",
                "summary": "Put decision context under O control.",
                "directives": [
                    "An outside-known constraint must not silently disappear from O."
                ],
                "supersedes": [],
                "source": "test",
            }
        ],
        "updated_at": "2026-08-23T00:00:00Z",
    }
    state = {
        "schema_version": 1,
        "mode": "work_o_engine_single_writer",
        "status": "running",
        "owner_kind": "work_primary",
        "execution_id": "work-context-kernel-test",
        "lease_generation": 3,
        "fence_token": "opaque-fence-must-not-be-copied",
        "heartbeat_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "stale_after_seconds": 900,
        "active_run_id": RUN_ID,
        "user_input_inbox": {
            "path": "agi/USER_INPUT_INBOX.json",
            "highest_acknowledged_revision": 1,
            "application_note": "Context direction is active; this owner is sole writer.",
        },
        "result_publication_policy": {
            "destination": "main",
            "rule": "isolated branch then exact-head CI",
            "excludes": ["force_push"],
        },
        "primary_run_contract": {
            "normal_completion_condition": "user_level_objective_met or explicit_user_stop; strict gate is optional verification machinery"
        },
    }
    strategy = {
        "schema_version": 1,
        "optimization_objective": "Minimize elapsed time to the actual objective.",
        "execution_rules": {
            "validated_execution_results_destination": "main",
            "main_integration_rule": "isolated branch -> exact-head CI -> merge",
        },
        "claim_boundary": {"agi_claim_supported": False},
        "immediate_sequence": ["Build the Root manifest slice."],
        "context_management": {
            "decision_authority": "O Engine owns decision context.",
            "raw_authority": "source systems",
        },
        "updated_at": "2026-08-23T00:00:02Z",
    }
    store = Store(tmp_path)
    entry = inbox["entries"][0]
    ledger = {
        "schema_version": 1,
        "source": {
            "path": "agi/USER_INPUT_INBOX.json",
            "revision": 1,
            "content_digest": store.stable_digest(inbox, length=64),
            "interpreted_at": "2026-08-23T00:00:00Z",
        },
        "atoms": [
            {
                "atom_id": atom_id,
                "source_entry_id": entry["id"],
                "source_entry_digest": store.stable_digest(entry, length=64),
                "source_directive_indices": [0],
                "slot": slot,
                "cardinality": cardinality,
                "value": value,
                "precedence": 1,
                "supersedes": [],
            }
            for atom_id, slot, cardinality, value in [
                (
                    "test-primary",
                    "execution.primary",
                    "single",
                    "chatgpt_work_primary",
                ),
                (
                    "test-main-writer",
                    "execution.main_writer",
                    "single",
                    "single_fenced_primary",
                ),
                (
                    "test-publication",
                    "publication.mode",
                    "single",
                    "isolated_exact_head_ci_then_serial_main",
                ),
                (
                    "test-completion",
                    "completion.condition",
                    "single",
                    "user_objective_or_explicit_stop",
                ),
                (
                    "test-context-authority",
                    "context.decision_authority",
                    "single",
                    "O Engine",
                ),
                (
                    "test-context-constraint",
                    "context.constraints",
                    "many",
                    "outside_constraint_must_be_ingested",
                ),
            ]
        ],
    }
    snapshot = {
        "run_id": RUN_ID,
        "revision": 7,
        "status": "continue",
        "phase": "root_pending",
        "current_component": "task_evaluate",
        "current_unit": "unit-before-context",
        "task_completion_verdict": "FAIL",
        "unit_completion_verdict": "PASS",
        "last_result_ref": "artifacts/previous.json",
        "updated_at": "2026-08-23T00:00:03Z",
    }
    _write_json(tmp_path / "agi" / "USER_INPUT_INBOX.json", inbox)
    _write_json(tmp_path / "agi" / "USER_DIRECTIVE_EVENTS.json", ledger)
    _write_json(tmp_path / "agi" / "WORK_EXECUTION_STATE.json", state)
    _write_json(tmp_path / "agi" / "WORK_STRATEGY.json", strategy)
    _write_json(
        tmp_path / ".continual" / "runs" / RUN_ID / "snapshot.json", snapshot
    )
    observation_id = "observation-test-main-state"
    observation_dir = (
        tmp_path
        / ".continual"
        / "runs"
        / RUN_ID
        / "context-observations"
        / observation_id
    )
    request = {
        "schema_version": 1,
        "record_type": "context_observation_request",
        "run_id": RUN_ID,
        "observation_id": observation_id,
        "invocation_id": "invoke-context-test",
        "work_request_digest": "frozen-work-request-digest",
        "executor_binding": "context-kernel-test-session",
        "model_identity": "context-kernel-test-model",
        "source": {
            "kind": "github_file",
            "repository_full_name": "example/context-test",
            "path": "agi/WORK_EXECUTION_STATE.json",
            "ref": "1" * 40,
            "expected_commit_sha": "1" * 40,
        },
        "selected_fields": ["status"],
        "freshness": {
            "kind": "immutable_version",
            "invalidates_on": ["commit identity mismatch"],
        },
        "evidence_class": "operator_connector_readback",
        "operation": "read",
    }
    request["requested_at"] = "2026-08-23T00:00:02Z"
    request["request_digest"] = store.stable_digest(request, length=64)
    receipt = {
        "schema_version": 1,
        "record_type": "context_observation_receipt",
        "run_id": RUN_ID,
        "observation_id": observation_id,
        "request_digest": request["request_digest"],
        "executor_binding": request["executor_binding"],
        "model_identity": request["model_identity"],
        "source": deepcopy(request["source"]),
        "source_version": {"commit_sha": "1" * 40, "blob_sha": "2" * 40},
        "projection": {"status": "running"},
        "status": "succeeded",
        "unknowns": [],
        "evidence_class": "operator_connector_readback",
    }
    receipt["observed_at"] = "2026-08-23T00:00:02Z"
    receipt["receipt_digest"] = store.stable_digest(receipt, length=64)
    _write_json(observation_dir / "request.json", request)
    _write_json(observation_dir / "receipt.json", receipt)
    ledger_entry = observation_ledger_entry(
        tmp_path,
        run_id=RUN_ID,
        observation_id=observation_id,
        source_id="github_test_work_state",
    )
    ledger_entry["run_id"] = RUN_ID
    _write_json(
        tmp_path / "agi" / "CONTEXT_OBSERVATION_LEDGER.json",
        {"schema_version": 1, "entries": [ledger_entry]},
    )
    return tmp_path, snapshot


def _client(root: Path) -> WorkModelClient:
    return WorkModelClient(
        root,
        run_id=RUN_ID,
        executor_binding="context-kernel-test-session",
        model_identity="context-kernel-test-model",
    )


def _enable_authoritative_policy(root: Path) -> tuple[dict, str]:
    state_path = root / "agi" / "WORK_EXECUTION_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["heartbeat_at"] = Store(root).utc_now()
    state["authoritative_source_observation_policy"] = {
        "required": True,
        "repository_full_name": "example/context-test",
        "ref": "main",
        "max_age_seconds": 300,
        "executor_binding": "context-kernel-test-session",
    }
    _write_json(state_path, state)
    return state, _blob_sha(state_path)


def _record_authoritative_receipt(root: Path) -> tuple[dict, dict]:
    state, blob = _enable_authoritative_policy(root)
    request = prepare_work_source_observation(
        root,
        run_id=RUN_ID,
        state=state,
        state_blob_sha=blob,
        expected_commit_sha="a" * 40,
        model_identity="context-kernel-test-model",
    )
    store = Store(root)
    receipt = record_work_source_observation_receipt(
        root,
        run_id=RUN_ID,
        observation_id=request["observation_id"],
        request_digest=request["request_digest"],
        executor_binding="context-kernel-test-session",
        model_identity="context-kernel-test-model",
        commit_sha="a" * 40,
        blob_sha=blob,
        projection={
            "status": state["status"],
            "owner_kind": state["owner_kind"],
            "execution_id": state["execution_id"],
            "lease_generation": state["lease_generation"],
            "fence_token_digest": store.stable_digest(
                state["fence_token"], length=64
            ),
            "heartbeat_at": state["heartbeat_at"],
        },
        observed_at=store.utc_now(),
    )
    return request, receipt


def _freeze_root(client: WorkModelClient, snapshot: dict) -> tuple[dict, bytes]:
    with pytest.raises(WorkModelPending) as pending:
        client.call(
            "root",
            {"snapshot": deepcopy(snapshot), "last_result": {"verdict": "FAIL"}},
            prompt_path="prompts/root.md",
        )
    path = (
        client.invocation_root / pending.value.invocation_id / "request.json"
    )
    return json.loads(path.read_text(encoding="utf-8")), path.read_bytes()


def _freeze_component(
    client: WorkModelClient,
    snapshot: dict,
    component: str,
) -> tuple[dict, Path]:
    with pytest.raises(WorkModelPending) as pending:
        client.call(
            component,
            {"snapshot": deepcopy(snapshot), "unit": {"scope": "test"}},
            prompt_path=f"prompts/{component}.md",
        )
    path = client.invocation_root / pending.value.invocation_id / "request.json"
    return json.loads(path.read_text(encoding="utf-8")), path


def _effect_action(path: str = "guarded.txt") -> dict:
    return {
        "kind": "github_update_file",
        "target": {
            "repository": "owner/repo",
            "branch": "work/context-guard",
            "path": path,
        },
        "parameters": {
            "expected_blob_sha": "a" * 40,
            "content_digest": "b" * 64,
        },
    }


def _context_authorized_effect(
    root: Path, snapshot: dict, *, effect_id: str
):
    request, request_path = _freeze_component(
        _client(root), snapshot, "execute"
    )
    _write_json(
        root
        / ".continual"
        / "runs"
        / RUN_ID
        / "invocations"
        / "invoke-native-effect-test.json",
        {
            "status": "awaiting_work_model",
            "component": "execute",
            "work_invocation_id": request["invocation_id"],
            "work_request_ref": request_path.relative_to(root).as_posix(),
            "work_request_digest": request["request_digest"],
        },
    )
    state_path = root / "agi" / "WORK_EXECUTION_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["heartbeat_at"] = Store(root).utc_now()
    _write_json(state_path, state)
    plan = prepare_work_effect(
        root,
        run_id=RUN_ID,
        effect_id=effect_id,
        invocation_id=request["invocation_id"],
        action=_effect_action(),
        executor_binding="context-kernel-test-session",
        model_identity="context-kernel-test-model",
    )
    authorization = authorize_work_effect(
        root,
        run_id=RUN_ID,
        effect_id=effect_id,
        invocation_id=request["invocation_id"],
        request_digest=request["request_digest"],
        action=_effect_action(),
        executor_binding="context-kernel-test-session",
        model_identity="context-kernel-test-model",
    )
    typed = load_authorized_work_effect(
        root,
        run_id=RUN_ID,
        effect_id=effect_id,
        executor_binding="context-kernel-test-session",
        model_identity="context-kernel-test-model",
    )
    return request, plan, authorization, typed


def test_root_manifest_is_deterministic_minimal_and_o_owned(tmp_path: Path) -> None:
    root, snapshot = _root(tmp_path)
    client = _client(root)

    request, before = _freeze_root(client, snapshot)
    manifest = request["payload"]["decision_context"]
    verified = verify_decision_context_manifest(
        manifest,
        store=Store(root),
        expected_component="root",
    )
    assert verified == manifest
    assert manifest["policy"]["decision_authority"] == "O Engine"
    assert manifest["policy"]["copy_all_raw_context"] is False
    assert [source["source_id"] for source in manifest["sources"]] == [
        "work_execution_state",
        "user_input_inbox",
        "effective_user_directives",
        "work_strategy",
        "external_observations",
        "native_run_snapshot",
    ]
    request_text = json.dumps(request, ensure_ascii=False)
    assert "opaque-fence-must-not-be-copied" not in request_text
    assert "An outside-known constraint must not silently disappear from O." in request_text
    assert "outer_session_untracked_memory" in request_text

    tampered = deepcopy(manifest)
    tampered["sources"][0]["projection"]["lease_generation"] = 99
    with pytest.raises(ContextKernelError, match="manifest digest mismatch"):
        verify_decision_context_manifest(tampered, store=Store(root))

    inconsistent = deepcopy(manifest)
    inconsistent["source_clock"]["work_execution_state"]["version"] = "other"
    body = deepcopy(inconsistent)
    body.pop("manifest_digest")
    inconsistent["manifest_digest"] = Store(root).stable_digest(body, length=64)
    with pytest.raises(ContextKernelError, match="source clock binding mismatch"):
        verify_decision_context_manifest(inconsistent, store=Store(root))

    rebound = deepcopy(manifest)
    rebound["component"] = "execute"
    rebound_body = deepcopy(rebound)
    rebound_body.pop("manifest_digest")
    rebound["manifest_digest"] = Store(root).stable_digest(rebound_body, length=64)
    with pytest.raises(ContextKernelError, match="component binding mismatch"):
        verify_decision_context_manifest(
            rebound,
            store=Store(root),
            expected_component="root",
        )

    replay, after = _freeze_root(client, snapshot)
    assert replay["invocation_id"] == request["invocation_id"]
    assert replay["request_digest"] == request["request_digest"]
    assert after == before


def test_root_manifest_fails_closed_on_partial_or_malformed_control_plane(
    tmp_path: Path,
) -> None:
    root, snapshot = _root(tmp_path)
    (root / "agi" / "WORK_STRATEGY.json").unlink()
    with pytest.raises(WorkSessionError, match="partial Context Kernel control plane"):
        _freeze_root(_client(root), snapshot)

    _root(tmp_path / "second")
    second = tmp_path / "second"
    state_path = second / "agi" / "WORK_EXECUTION_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.pop("fence_token")
    _write_json(state_path, state)
    durable_snapshot = json.loads(
        (
            second
            / ".continual"
            / "runs"
            / RUN_ID
            / "snapshot.json"
        ).read_text(encoding="utf-8")
    )
    with pytest.raises(WorkSessionError, match="state.fence_token"):
        _freeze_root(_client(second), durable_snapshot)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda value, now: value.__setitem__("status", "released"), "running Work lease"),
        (
            lambda value, now: value.__setitem__(
                "heartbeat_at", (now - timedelta(seconds=901)).isoformat()
            ),
            "heartbeat is stale",
        ),
        (
            lambda value, now: value.__setitem__(
                "heartbeat_at", (now + timedelta(seconds=121)).isoformat()
            ),
            "future-skewed",
        ),
        (lambda value, now: value.__setitem__("heartbeat_at", "not-a-time"), "ISO-8601"),
        (lambda value, now: value.__setitem__("heartbeat_at", "2026-08-23T00:00:00"), "timezone"),
        (lambda value, now: value.pop("execution_id"), "state.execution_id"),
        (lambda value, now: value.pop("lease_generation"), "state.lease_generation"),
        (lambda value, now: value.pop("fence_token"), "state.fence_token"),
    ],
)
def test_mandatory_work_source_freshness_fails_closed(
    tmp_path: Path,
    mutation,
    match: str,
) -> None:
    root, _ = _root(tmp_path)
    state = json.loads(
        (root / "agi" / "WORK_EXECUTION_STATE.json").read_text(encoding="utf-8")
    )
    now = datetime.now(UTC)
    state["heartbeat_at"] = now.isoformat()
    mutation(state, now)

    with pytest.raises(ContextKernelError, match=match):
        validate_mandatory_work_source_freshness(
            state,
            observed_at=now.isoformat(),
        )


def test_mandatory_work_source_freshness_accepts_exact_fresh_authority(
    tmp_path: Path,
) -> None:
    root, _ = _root(tmp_path)
    state = json.loads(
        (root / "agi" / "WORK_EXECUTION_STATE.json").read_text(encoding="utf-8")
    )
    now = datetime.now(UTC)
    state["heartbeat_at"] = (now - timedelta(seconds=30)).isoformat()

    readiness = validate_mandatory_work_source_freshness(
        state,
        observed_at=now.isoformat(),
    )

    assert readiness["execution_id"] == state["execution_id"]
    assert readiness["lease_generation"] == state["lease_generation"]
    assert readiness["fence_token"] == state["fence_token"]
    assert readiness["age_seconds"] == pytest.approx(30)
    assert readiness["source_scope"] == "local_bytes_only_not_remote_revision_proof"


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda value, now: value.__setitem__("status", "released"), "running Work lease"),
        (
            lambda value, now: value.__setitem__(
                "heartbeat_at", (now - timedelta(seconds=901)).isoformat()
            ),
            "heartbeat is stale",
        ),
        (
            lambda value, now: value.__setitem__(
                "heartbeat_at", (now + timedelta(seconds=121)).isoformat()
            ),
            "future-skewed",
        ),
        (lambda value, now: value.__setitem__("heartbeat_at", "not-a-time"), "ISO-8601"),
        (lambda value, now: value.__setitem__("heartbeat_at", "2026-08-23T00:00:00"), "timezone"),
        (lambda value, now: value.pop("execution_id"), "state.execution_id"),
        (lambda value, now: value.pop("lease_generation"), "state.lease_generation"),
        (lambda value, now: value.pop("fence_token"), "state.fence_token"),
    ],
)
def test_unready_source_rejects_before_native_or_work_request_mutation(
    tmp_path: Path,
    mutation,
    match: str,
) -> None:
    root, snapshot = _root(tmp_path)
    state_path = root / "agi" / "WORK_EXECUTION_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    now = datetime.now(UTC)
    state["heartbeat_at"] = now.isoformat()
    mutation(state, now)
    _write_json(state_path, state)
    client = _client(root)
    engine = Engine(root, model=client)

    before = _persisted_request_boundary(root)
    with pytest.raises(WorkSessionError, match=match):
        engine._call_component_direct(
            RUN_ID,
            "root",
            {"snapshot": deepcopy(snapshot), "last_result": {"verdict": "FAIL"}},
            prompt_path="prompts/root.md",
        )
    assert _persisted_request_boundary(root) == before


def test_required_authoritative_observation_rejects_atomically_when_missing(
    tmp_path: Path,
) -> None:
    root, snapshot = _root(tmp_path)
    _enable_authoritative_policy(root)
    engine = Engine(root, model=_client(root))
    before = _persisted_request_boundary(root)

    with pytest.raises(
        WorkSessionError,
        match="authoritative Work source observation failed.*matching fresh",
    ):
        engine._call_component_direct(
            RUN_ID,
            "root",
            {"snapshot": deepcopy(snapshot), "last_result": {"verdict": "FAIL"}},
            prompt_path="prompts/root.md",
        )

    assert _persisted_request_boundary(root) == before


def test_precommitted_authoritative_observation_binds_frozen_manifest(
    tmp_path: Path,
) -> None:
    root, snapshot = _root(tmp_path)
    request, receipt = _record_authoritative_receipt(root)

    frozen, _ = _freeze_root(_client(root), snapshot)
    source = frozen["payload"]["decision_context"]["sources"][0]
    bound = source["projection"]["authoritative_source_observation"]

    assert bound["observation_id"] == request["observation_id"]
    assert bound["request_digest"] == request["request_digest"]
    assert bound["receipt_digest"] == receipt["receipt_digest"]
    assert source["version"].endswith(
        f";remote-receipt:{receipt['receipt_digest']}"
    )
    assert source["freshness"]["authoritative_observation"] == bound
    assert bound["claim_scope"].endswith("not_linearizable_latest_proof")


def test_authoritative_receipt_does_not_authorize_advanced_state_bytes(
    tmp_path: Path,
) -> None:
    root, snapshot = _root(tmp_path)
    _record_authoritative_receipt(root)
    state_path = root / "agi" / "WORK_EXECUTION_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["heartbeat_at"] = (
        datetime.now(UTC) + timedelta(seconds=1)
    ).isoformat()
    _write_json(state_path, state)
    engine = Engine(root, model=_client(root))
    before = _persisted_request_boundary(root)

    with pytest.raises(WorkSessionError, match="matching fresh"):
        engine._call_component_direct(
            RUN_ID,
            "root",
            {"snapshot": deepcopy(snapshot), "last_result": {"verdict": "FAIL"}},
            prompt_path="prompts/root.md",
        )

    assert _persisted_request_boundary(root) == before


def test_bound_receipt_replay_never_revalidates_advanced_authority(
    tmp_path: Path,
) -> None:
    root, snapshot = _root(tmp_path)
    _record_authoritative_receipt(root)
    client = _client(root)
    payload = {"snapshot": deepcopy(snapshot), "unit": {"scope": "test"}}
    with pytest.raises(WorkModelPending) as first:
        client.call("execute", payload, prompt_path="prompts/execute.md")
    request_path = root / first.value.request_ref
    frozen_bytes = request_path.read_bytes()
    state_path = root / "agi" / "WORK_EXECUTION_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["heartbeat_at"] = "2000-01-01T00:00:00Z"
    _write_json(state_path, state)

    with pytest.raises(WorkModelPending) as resumed:
        client.resume_bound(
            "execute",
            payload,
            prompt_path="prompts/execute.md",
            invocation_id=first.value.invocation_id,
            request_ref=first.value.request_ref,
            request_digest=first.value.request_digest,
        )

    assert request_path.read_bytes() == frozen_bytes
    assert resumed.value.invocation_id == first.value.invocation_id


def test_bound_replay_does_not_revalidate_later_stale_source(tmp_path: Path) -> None:
    root, snapshot = _root(tmp_path)
    client = _client(root)
    payload = {"snapshot": deepcopy(snapshot), "unit": {"scope": "test"}}
    with pytest.raises(WorkModelPending) as first:
        client.call("execute", payload, prompt_path="prompts/execute.md")
    request_path = root / first.value.request_ref
    frozen_bytes = request_path.read_bytes()
    state_path = root / "agi" / "WORK_EXECUTION_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["heartbeat_at"] = "2000-01-01T00:00:00Z"
    _write_json(state_path, state)

    with pytest.raises(WorkModelPending) as resumed:
        client.resume_bound(
            "execute",
            payload,
            prompt_path="prompts/execute.md",
            invocation_id=first.value.invocation_id,
            request_ref=first.value.request_ref,
            request_digest=first.value.request_digest,
        )

    assert request_path.read_bytes() == frozen_bytes
    assert resumed.value.invocation_id == first.value.invocation_id


def test_root_manifest_fails_closed_on_inbox_binding_disagreement(
    tmp_path: Path,
) -> None:
    root, snapshot = _root(tmp_path)
    state_path = root / "agi" / "WORK_EXECUTION_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["pending_user_input"] = {
        "revision": 1,
        "inbox_blob_sha": "0" * 40,
    }
    _write_json(state_path, state)

    with pytest.raises(WorkSessionError, match="inbox blob binding mismatch"):
        _freeze_root(_client(root), snapshot)


def test_root_manifest_rejects_outer_snapshot_with_matching_version(
    tmp_path: Path,
) -> None:
    root, snapshot = _root(tmp_path)
    injected = deepcopy(snapshot)
    injected["phase"] = "completed"

    with pytest.raises(WorkSessionError, match="exact durable run snapshot"):
        _freeze_root(_client(root), injected)


def test_frozen_request_survives_source_advance_and_next_root_changes(
    tmp_path: Path,
) -> None:
    root, snapshot = _root(tmp_path)
    client = _client(root)
    first, first_bytes = _freeze_root(client, snapshot)
    first_path = client.invocation_root / first["invocation_id"] / "request.json"
    outside_only = "Never dispatch a destructive effect without a fresh revocation check."
    assert outside_only not in json.dumps(first, ensure_ascii=False)

    inbox_path = root / "agi" / "USER_INPUT_INBOX.json"
    inbox = json.loads(inbox_path.read_text(encoding="utf-8"))
    inbox["revision"] = 2
    inbox["updated_at"] = "2026-08-23T00:01:00Z"
    inbox["entries"].append(
        {
            "sequence": 2,
            "id": "direction-revocation-v2",
            "received_at": "2026-08-23T00:01:00Z",
            "kind": "user_direction",
            "status": "active",
            "summary": "Bind destructive effects to current revocations.",
            "directives": [outside_only],
            "supersedes": [],
            "source": "test",
        }
    )
    _write_json(inbox_path, inbox)
    ledger_path = root / "agi" / "USER_DIRECTIVE_EVENTS.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["source"]["revision"] = 2
    ledger["source"]["content_digest"] = Store(root).stable_digest(inbox, length=64)
    ledger["source"]["interpreted_at"] = "2026-08-23T00:01:00Z"
    ledger["atoms"].append(
        {
            "atom_id": "test-effect-revocation",
            "source_entry_id": "direction-revocation-v2",
            "source_entry_digest": Store(root).stable_digest(
                inbox["entries"][1], length=64
            ),
            "source_directive_indices": [0],
            "slot": "effect.revocation",
            "cardinality": "many",
            "value": "fresh_revocation_check",
            "precedence": 2,
            "supersedes": [],
        }
    )
    _write_json(ledger_path, ledger)

    second, _ = _freeze_root(client, snapshot)
    assert first_path.read_bytes() == first_bytes
    assert second["invocation_id"] != first["invocation_id"]
    assert second["request_digest"] != first["request_digest"]
    assert (
        second["payload"]["decision_context"]["source_clock"]["user_input_inbox"]
        != first["payload"]["decision_context"]["source_clock"]["user_input_inbox"]
    )
    assert outside_only in json.dumps(second, ensure_ascii=False)
    inbox_projection = next(
        source["projection"]
        for source in second["payload"]["decision_context"]["sources"]
        if source["source_id"] == "user_input_inbox"
    )
    assert inbox_projection["unacknowledged_entries"][0]["id"] == (
        "direction-revocation-v2"
    )


def test_bound_resume_keeps_exact_pending_root_after_source_advance(
    tmp_path: Path,
) -> None:
    root, snapshot = _root(tmp_path)
    client = _client(root)
    payload = {"snapshot": deepcopy(snapshot), "last_result": {"verdict": "FAIL"}}
    with pytest.raises(WorkModelPending) as first:
        client.call("root", payload, prompt_path="prompts/root.md")

    state_path = root / "agi" / "WORK_EXECUTION_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["heartbeat_at"] = Store(root).utc_now()
    _write_json(state_path, state)

    with pytest.raises(WorkModelPending) as resumed:
        client.resume_bound(
            "root",
            payload,
            prompt_path="prompts/root.md",
            invocation_id=first.value.invocation_id,
            request_ref=first.value.request_ref,
            request_digest=first.value.request_digest,
        )
    assert resumed.value.invocation_id == first.value.invocation_id
    assert resumed.value.request_digest == first.value.request_digest
    assert len(list(client.invocation_root.glob("*/request.json"))) == 1


@pytest.mark.parametrize("component", sorted(SEMANTIC_CONTEXT_COMPONENTS))
def test_outer_payload_cannot_inject_a_competing_decision_context(
    tmp_path: Path,
    component: str,
) -> None:
    root, snapshot = _root(tmp_path)
    with pytest.raises(WorkSessionError, match="may not inject"):
        _client(root).call(
            component,
            {"snapshot": snapshot, "decision_context": {"authority": "outer"}},
            prompt_path=f"prompts/{component}.md",
        )


def test_all_semantic_components_bind_manifest_and_next_source_clock(
    tmp_path: Path,
) -> None:
    root, snapshot = _root(tmp_path)
    client = _client(root)
    first: dict[str, tuple[dict, Path, bytes]] = {}
    for component in sorted(SEMANTIC_CONTEXT_COMPONENTS):
        request, path = _freeze_component(client, snapshot, component)
        manifest = request["payload"]["decision_context"]
        assert manifest["component"] == component
        assert verify_decision_context_manifest(
            manifest,
            store=Store(root),
            expected_component=component,
        ) == manifest
        first[component] = (request, path, path.read_bytes())

    state_path = root / "agi" / "WORK_EXECUTION_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["heartbeat_at"] = Store(root).utc_now()
    _write_json(state_path, state)

    for component in sorted(SEMANTIC_CONTEXT_COMPONENTS):
        previous, path, frozen_bytes = first[component]
        current, _ = _freeze_component(client, snapshot, component)
        assert path.read_bytes() == frozen_bytes
        assert current["invocation_id"] != previous["invocation_id"]
        assert current["request_digest"] != previous["request_digest"]
        assert (
            current["payload"]["decision_context"]["source_clock"][
                "work_execution_state"
            ]
            != previous["payload"]["decision_context"]["source_clock"][
                "work_execution_state"
            ]
        )


@pytest.mark.parametrize("component", sorted(SEMANTIC_CONTEXT_COMPONENTS))
def test_bound_resume_keeps_exact_pending_semantic_request_after_source_advance(
    tmp_path: Path,
    component: str,
) -> None:
    root, snapshot = _root(tmp_path)
    client = _client(root)
    payload = {"snapshot": deepcopy(snapshot), "unit": {"scope": "test"}}
    with pytest.raises(WorkModelPending) as first:
        client.call(component, payload, prompt_path=f"prompts/{component}.md")

    request_path = root / first.value.request_ref
    frozen_bytes = request_path.read_bytes()
    state_path = root / "agi" / "WORK_EXECUTION_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["heartbeat_at"] = Store(root).utc_now()
    _write_json(state_path, state)

    with pytest.raises(WorkModelPending) as resumed:
        client.resume_bound(
            component,
            payload,
            prompt_path=f"prompts/{component}.md",
            invocation_id=first.value.invocation_id,
            request_ref=first.value.request_ref,
            request_digest=first.value.request_digest,
        )
    assert request_path.read_bytes() == frozen_bytes
    assert resumed.value.invocation_id == first.value.invocation_id
    assert resumed.value.request_digest == first.value.request_digest


def test_effect_dispatch_context_binds_manifest_and_allows_heartbeat_refresh(
    tmp_path: Path,
) -> None:
    root, snapshot = _root(tmp_path)
    request, plan, authorization, typed = _context_authorized_effect(
        root, snapshot, effect_id="context-bound-v1"
    )
    manifest = request["payload"]["decision_context"]
    context = plan["dispatch_context"]
    assert context["decision_context_manifest_digest"] == manifest["manifest_digest"]
    assert context["action_constraints"]["action_digest"] == plan["action_digest"]
    assert context["dispatch_context_digest"] == authorization[
        "dispatch_context_digest"
    ]
    assert typed.dispatch_context_digest == context["dispatch_context_digest"]
    persisted = json.dumps(plan, ensure_ascii=False)
    assert "opaque-fence-must-not-be-copied" not in persisted
    assert context["stable_authority"]["fence_token_digest"]

    state_path = root / "agi" / "WORK_EXECUTION_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["heartbeat_at"] = Store(root).utc_now()
    _write_json(state_path, state)
    calls: list[dict] = []
    result = dispatch_work_effect(
        root,
        authorization=typed,
        callback=lambda envelope: calls.append(envelope)
        or {"verified_readback": True},
    )
    assert result["dispatched"] is True
    assert len(calls) == 1


def test_control_plane_effect_without_execute_manifest_fails_closed(
    tmp_path: Path,
) -> None:
    root, _ = _root(tmp_path)
    with pytest.raises(ContextKernelError, match="requires an Execute decision context"):
        build_effect_dispatch_context(
            root,
            request={
                "run_id": RUN_ID,
                "invocation_id": "invoke-legacy-effect",
                "request_digest": "f" * 64,
                "payload": {"snapshot": {"run_id": RUN_ID}},
            },
            action=_effect_action(),
        )


@pytest.mark.parametrize(
    ("source", "mutation", "match"),
    [
        (
            "state",
            lambda value: value.__setitem__("status", "released"),
            "running Work lease",
        ),
        (
            "state",
            lambda value: value.__setitem__("lease_generation", 4),
            "authority changed",
        ),
        (
            "state",
            lambda value: value.__setitem__("fence_token", "replacement-fence"),
            "authority changed",
        ),
        (
            "state",
            lambda value: value.__setitem__(
                "heartbeat_at", "2000-01-01T00:00:00Z"
            ),
            "heartbeat is stale",
        ),
        (
            "state",
            lambda value: value.__setitem__(
                "heartbeat_at", "2999-01-01T00:00:00Z"
            ),
            "heartbeat is future-skewed",
        ),
        (
            "state",
            lambda value: value.__setitem__("max_future_skew_seconds", 3600),
            "heartbeat policy changed",
        ),
        (
            "state",
            lambda value: value["result_publication_policy"].__setitem__(
                "rule", "changed publication constraint"
            ),
            "publication constraint changed",
        ),
        (
            "inbox",
            lambda value: value.__setitem__("updated_at", "2026-08-23T00:05:00Z"),
            "user input changed",
        ),
        (
            "ledger",
            lambda value: value.__setitem__("updated_at", "2026-08-23T00:06:00Z"),
            "directive ledger changed",
        ),
        (
            "strategy",
            lambda value: value["execution_rules"].__setitem__(
                "main_integration_rule", "changed constraint"
            ),
            "strategy constraints changed",
        ),
    ],
)
def test_effect_dispatch_rechecks_critical_sources_before_claim_or_callback(
    tmp_path: Path,
    source: str,
    mutation,
    match: str,
) -> None:
    root, snapshot = _root(tmp_path)
    _, _, _, typed = _context_authorized_effect(
        root, snapshot, effect_id="revoked-before-dispatch-v1"
    )
    paths = {
        "state": root / "agi" / "WORK_EXECUTION_STATE.json",
        "inbox": root / "agi" / "USER_INPUT_INBOX.json",
        "ledger": root / "agi" / "USER_DIRECTIVE_EVENTS.json",
        "strategy": root / "agi" / "WORK_STRATEGY.json",
    }
    path = paths[source]
    value = json.loads(path.read_text(encoding="utf-8"))
    mutation(value)
    _write_json(path, value)
    calls = 0

    def provider(_: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"verified_readback": True}

    with pytest.raises(WorkEffectError, match=match):
        dispatch_work_effect(root, authorization=typed, callback=provider)
    assert calls == 0
    assert not (
        root
        / ".continual"
        / "runs"
        / RUN_ID
        / "external-effects"
        / "revoked-before-dispatch-v1"
        / "dispatch.json"
    ).exists()


def test_completed_context_bound_effect_replay_never_rechecks_or_redispatches(
    tmp_path: Path,
) -> None:
    root, snapshot = _root(tmp_path)
    _, _, _, typed = _context_authorized_effect(
        root, snapshot, effect_id="completed-context-bound-v1"
    )
    calls = 0

    def provider(_: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"verified_readback": True}

    first = dispatch_work_effect(root, authorization=typed, callback=provider)
    assert calls == 1
    state_path = root / "agi" / "WORK_EXECUTION_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "released"
    _write_json(state_path, state)
    replay = dispatch_work_effect(root, authorization=typed, callback=provider)
    assert replay["replayed"] is True
    assert replay["receipt_digest"] == first["receipt_digest"]
    assert calls == 1
