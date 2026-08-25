from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .store import Store


class PairedRouteIsolationError(ValueError):
    """Raised when a paired route precommit fails closed."""


CLAIM_SCOPE = (
    "internal_paired_route_isolation_precommit_"
    "not_behavioral_family_or_completion_evidence"
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
_SECRET_TEXT = re.compile(
    r"(?:\bBearer\s+[A-Za-z0-9._~+/=-]{12,}|\b(?:ghp|github_pat|sk)-[A-Za-z0-9_-]{12,})",
    re.IGNORECASE,
)
_FORBIDDEN_KEYS = {
    "api_key",
    "authorization",
    "chain_of_thought",
    "cookie",
    "credentials",
    "hidden_reasoning",
    "password",
    "raw_system_prompt",
    "scratchpad",
    "secret",
    "system_prompt",
}
_STATUS = "PRECOMMITTED_AWAITING_ISOLATED_CHILDREN"
_STABLE_AUTHORITY_KEYS = {
    "status",
    "owner_kind",
    "execution_id",
    "lease_generation",
    "fence_token_digest",
    "highest_acknowledged_inbox_revision",
}
_PREPARED_AUTHORITY_KEYS = _STABLE_AUTHORITY_KEYS | {
    "heartbeat_at",
    "stale_after_seconds",
}
_PRECOMMIT_KEYS = {
    "schema_version",
    "record_type",
    "comparison_id",
    "status",
    "run_id",
    "routes",
    "scenarios",
    "execution_order",
    "shared_budget",
    "tool_permissions",
    "executor_class",
    "model_class",
    "authority",
    "claim_scope",
    "finalization_requirements",
    "prepared_authority",
    "prepared_at",
    "precommit_digest",
}
_FINALIZATION_REQUIREMENTS = {
    "required_child_count": 6,
    "require_all_exact_child_bindings": True,
    "require_all_immutable_child_responses": True,
    "require_post_response_rubric_reveals": True,
    "require_deterministic_judgments": True,
    "permit_precommit_score_or_route_claim": False,
}
_RESPONSE_KEYS = {
    "schema_version",
    "record_type",
    "run_id",
    "comparison_id",
    "precommit_digest",
    "scenario_id",
    "route_id",
    "binding_digest",
    "response",
    "authority",
    "recorded_at",
    "claim_scope",
    "response_digest",
}
_FINALIZATION_KEYS = {
    "schema_version",
    "record_type",
    "run_id",
    "comparison_id",
    "precommit_digest",
    "status",
    "required_child_count",
    "response_digests",
    "judgments",
    "route_summaries",
    "authority",
    "finalized_at",
    "claim_scope",
    "finalization_digest",
}
_FINALIZED_STATUS = "FINALIZED_DETERMINISTIC_EXACT_JUDGMENTS"


def _text(value: Any, label: str, *, maximum: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PairedRouteIsolationError(f"{label} must be non-empty text")
    if maximum is not None and len(value.encode("utf-8")) > maximum:
        raise PairedRouteIsolationError(f"{label} exceeds its byte budget")
    return value


def _identifier(value: Any, label: str) -> str:
    text = _text(value, label)
    if _IDENTIFIER.fullmatch(text) is None:
        raise PairedRouteIsolationError(f"{label} has an invalid identifier")
    return text


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise PairedRouteIsolationError(
            f"{label} must be a lowercase SHA-256 digest"
        )
    return value


def _integer(
    value: Any,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise PairedRouteIsolationError(
            f"{label} must be an integer in [{minimum}, {maximum}]"
        )
    return value


def _timestamp(value: Any, label: str) -> datetime:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PairedRouteIsolationError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise PairedRouteIsolationError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PairedRouteIsolationError("value must be bounded canonical JSON") from exc


def _walk_public(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or not key.strip():
                raise PairedRouteIsolationError(
                    f"public JSON keys must be non-empty text at {path}"
                )
            normalized = key.strip().lower()
            if normalized in _FORBIDDEN_KEYS:
                raise PairedRouteIsolationError(
                    f"forbidden private field at {path}.{key}"
                )
            _walk_public(child, f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for index, child in enumerate(value):
            _walk_public(child, f"{path}[{index}]")
        return
    if isinstance(value, str) and _SECRET_TEXT.search(value):
        raise PairedRouteIsolationError(f"secret-like text is forbidden at {path}")


def _relative_ref(value: Any, label: str) -> str:
    text = _text(value, label, maximum=512)
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or text.startswith("./"):
        raise PairedRouteIsolationError(f"{label} must be a safe repository-relative ref")
    return text


def _authority(
    state: Mapping[str, Any],
    store: Store,
    *,
    now: str,
) -> dict[str, Any]:
    if not isinstance(state, Mapping) or state.get("status") != "running":
        raise PairedRouteIsolationError("authority status must be running")
    generation = state.get("lease_generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise PairedRouteIsolationError("lease_generation must be positive")
    stale_after = state.get("stale_after_seconds")
    if (
        isinstance(stale_after, bool)
        or not isinstance(stale_after, int)
        or stale_after < 1
    ):
        raise PairedRouteIsolationError("stale_after_seconds must be positive")
    inbox = state.get("user_input_inbox")
    if not isinstance(inbox, Mapping):
        raise PairedRouteIsolationError("user_input_inbox authority is missing")
    revision = inbox.get("highest_acknowledged_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise PairedRouteIsolationError("inbox revision must be non-negative")
    heartbeat_text = _text(state.get("heartbeat_at"), "heartbeat_at")
    heartbeat = _timestamp(heartbeat_text, "heartbeat_at")
    current = _timestamp(now, "now")
    age = (current - heartbeat).total_seconds()
    if age < -120:
        raise PairedRouteIsolationError("authority heartbeat is future-skewed")
    if age > stale_after:
        raise PairedRouteIsolationError("authority heartbeat is stale")
    return {
        "status": "running",
        "owner_kind": _text(state.get("owner_kind"), "owner_kind"),
        "execution_id": _text(state.get("execution_id"), "execution_id"),
        "lease_generation": generation,
        "fence_token_digest": store.stable_digest(
            _text(state.get("fence_token"), "fence_token"), length=64
        ),
        "highest_acknowledged_inbox_revision": revision,
        "heartbeat_at": heartbeat_text,
        "stale_after_seconds": stale_after,
    }


def _stable_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(item)
        for key, item in value.items()
        if key not in {"heartbeat_at", "stale_after_seconds"}
    }


def _loaded_authority(
    value: Mapping[str, Any],
    *,
    prepared: bool,
) -> dict[str, Any]:
    expected = _PREPARED_AUTHORITY_KEYS if prepared else _STABLE_AUTHORITY_KEYS
    if not isinstance(value, Mapping) or set(value) != expected:
        raise PairedRouteIsolationError("stored authority has an unexpected schema")
    if value.get("status") != "running":
        raise PairedRouteIsolationError("stored authority status must be running")
    generation = _integer(
        value.get("lease_generation"),
        "stored lease_generation",
        minimum=1,
        maximum=2**63 - 1,
    )
    revision = _integer(
        value.get("highest_acknowledged_inbox_revision"),
        "stored inbox revision",
        minimum=0,
        maximum=2**63 - 1,
    )
    authority = {
        "status": "running",
        "owner_kind": _text(value.get("owner_kind"), "stored owner_kind"),
        "execution_id": _text(value.get("execution_id"), "stored execution_id"),
        "lease_generation": generation,
        "fence_token_digest": _digest(
            value.get("fence_token_digest"), "stored fence_token_digest"
        ),
        "highest_acknowledged_inbox_revision": revision,
    }
    if prepared:
        heartbeat = _text(value.get("heartbeat_at"), "stored heartbeat_at")
        _timestamp(heartbeat, "stored heartbeat_at")
        authority["heartbeat_at"] = heartbeat
        authority["stale_after_seconds"] = _integer(
            value.get("stale_after_seconds"),
            "stored stale_after_seconds",
            minimum=1,
            maximum=2**31 - 1,
        )
    return authority


def _route(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "route_id",
        "context_ref",
        "context_digest",
    }:
        raise PairedRouteIsolationError("route has an unexpected schema")
    return {
        "route_id": _identifier(value.get("route_id"), "route_id"),
        "context_ref": _relative_ref(value.get("context_ref"), "context_ref"),
        "context_digest": _digest(value.get("context_digest"), "context_digest"),
    }


def _commitment(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "judge_kind",
        "judge_version",
        "commitment_digest",
        "success_threshold",
    }:
        raise PairedRouteIsolationError("rubric commitment has an unexpected schema")
    if value.get("judge_kind") != "exact_canonical_json":
        raise PairedRouteIsolationError("judge_kind must be exact_canonical_json")
    if value.get("judge_version") != "exact-canonical-json-v1":
        raise PairedRouteIsolationError("judge_version is unsupported")
    threshold = value.get("success_threshold")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise PairedRouteIsolationError("success_threshold must be numeric")
    if float(threshold) != 1.0:
        raise PairedRouteIsolationError("exact judge success_threshold must be 1.0")
    return {
        "judge_kind": "exact_canonical_json",
        "judge_version": "exact-canonical-json-v1",
        "commitment_digest": _digest(
            value.get("commitment_digest"), "commitment_digest"
        ),
        "success_threshold": 1.0,
    }


def _scenario(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "scenario_id",
        "instruction",
        "input",
        "answer_format",
        "response_pointer",
        "rubric_commitment",
    }:
        raise PairedRouteIsolationError("scenario has an unexpected schema")
    scenario = {
        "scenario_id": _identifier(value.get("scenario_id"), "scenario_id"),
        "instruction": _text(
            value.get("instruction"), "scenario instruction", maximum=4096
        ),
        "input": deepcopy(value.get("input")),
        "answer_format": value.get("answer_format"),
        "response_pointer": deepcopy(value.get("response_pointer")),
        "rubric_commitment": _commitment(value.get("rubric_commitment", {})),
    }
    if scenario["answer_format"] != "canonical_json":
        raise PairedRouteIsolationError("answer_format must be canonical_json")
    if scenario["response_pointer"] != ["result", "behavioral_answer"]:
        raise PairedRouteIsolationError(
            "response_pointer must target result.behavioral_answer"
        )
    _walk_public(scenario["input"], "scenario.input")
    if len(_canonical_bytes(scenario["input"])) > 8192:
        raise PairedRouteIsolationError("scenario input exceeds its byte budget")
    return scenario


def _budget(value: Mapping[str, Any]) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != {
        "max_response_bytes",
        "max_model_calls",
        "max_tool_calls",
        "timeout_seconds",
    }:
        raise PairedRouteIsolationError("shared_budget has an unexpected schema")
    return {
        "max_response_bytes": _integer(
            value.get("max_response_bytes"),
            "max_response_bytes",
            minimum=1,
            maximum=16384,
        ),
        "max_model_calls": _integer(
            value.get("max_model_calls"),
            "max_model_calls",
            minimum=1,
            maximum=64,
        ),
        "max_tool_calls": _integer(
            value.get("max_tool_calls"),
            "max_tool_calls",
            minimum=0,
            maximum=128,
        ),
        "timeout_seconds": _integer(
            value.get("timeout_seconds"),
            "timeout_seconds",
            minimum=1,
            maximum=3600,
        ),
    }


def _permissions(value: Sequence[Any]) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PairedRouteIsolationError("tool_permissions must be an array")
    permissions = [
        _identifier(item, f"tool_permissions[{index}]")
        for index, item in enumerate(value)
    ]
    if not permissions or len(permissions) > 16:
        raise PairedRouteIsolationError("tool_permissions count is outside the safe bound")
    if len(set(permissions)) != len(permissions):
        raise PairedRouteIsolationError("tool_permissions must be unique")
    return permissions


def _order(
    value: Sequence[Mapping[str, Any]],
    *,
    scenario_ids: set[str],
    route_ids: set[str],
) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PairedRouteIsolationError("execution_order must be an array")
    order: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) != {"scenario_id", "route_ids"}:
            raise PairedRouteIsolationError(
                f"execution_order[{index}] has an unexpected schema"
            )
        scenario_id = _identifier(raw.get("scenario_id"), "execution_order scenario_id")
        raw_routes = raw.get("route_ids")
        if not isinstance(raw_routes, list):
            raise PairedRouteIsolationError("execution_order route_ids must be an array")
        routes = [
            _identifier(item, "execution_order route_id") for item in raw_routes
        ]
        if len(routes) != 2 or set(routes) != route_ids:
            raise PairedRouteIsolationError(
                "each execution_order row must contain both routes exactly once"
            )
        order.append({"scenario_id": scenario_id, "route_ids": routes})
    observed_scenarios = [item["scenario_id"] for item in order]
    if set(observed_scenarios) != scenario_ids or len(observed_scenarios) != len(
        scenario_ids
    ):
        raise PairedRouteIsolationError(
            "execution_order must contain every scenario exactly once"
        )
    first_counts = {
        route_id: sum(row["route_ids"][0] == route_id for row in order)
        for route_id in route_ids
    }
    if max(first_counts.values()) - min(first_counts.values()) > 1:
        raise PairedRouteIsolationError("execution_order is not position balanced")
    return order


def compute_rubric_commitment_digest(
    *,
    scenario_id: str,
    expected_answer: Any,
    nonce: str,
    store: Store | None = None,
) -> str:
    """Compute a sealed exact-judge commitment without persisting its reveal."""

    exact_scenario_id = _identifier(scenario_id, "scenario_id")
    exact_nonce = _text(nonce, "rubric nonce", maximum=512)
    if len(exact_nonce.encode("utf-8")) < 32:
        raise PairedRouteIsolationError("rubric nonce must contain at least 32 bytes")
    _walk_public(expected_answer, "expected_answer")
    if len(_canonical_bytes(expected_answer)) > 4096:
        raise PairedRouteIsolationError("expected answer exceeds its byte budget")
    exact_store = store or Store(Path("."))
    return exact_store.stable_digest(
        {
            "scenario_id": exact_scenario_id,
            "judge_kind": "exact_canonical_json",
            "judge_version": "exact-canonical-json-v1",
            "expected_answer": deepcopy(expected_answer),
            "nonce": exact_nonce,
            "success_threshold": 1.0,
        },
        length=64,
    )


def _directory(root: Path, run_id: str, comparison_id: str) -> Path:
    return (
        root.resolve()
        / ".continual"
        / "runs"
        / run_id
        / "paired-route-isolation"
        / comparison_id
    )


def _read_record(path: Path, store: Store) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise PairedRouteIsolationError("missing or malformed paired route precommit") from exc
    if not isinstance(value, dict) or value.get("record_type") != "paired_route_precommit":
        raise PairedRouteIsolationError("malformed paired route precommit")
    body = deepcopy(value)
    supplied = body.pop("precommit_digest", None)
    if supplied != store.stable_digest(body, length=64):
        raise PairedRouteIsolationError("tampered paired route precommit")
    if set(value) != _PRECOMMIT_KEYS:
        raise PairedRouteIsolationError(
            "paired route precommit has an unexpected schema"
        )
    return value


def _write_once(path: Path, value: Mapping[str, Any], store: Store) -> dict[str, Any]:
    if path.exists():
        existing = _read_record(path, store)
        if (
            existing.get("comparison_id") != value.get("comparison_id")
            or _frozen_fields(existing) != _frozen_fields(value)
        ):
            raise PairedRouteIsolationError("immutable paired route precommit conflict")
        return existing
    encoded = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _write_once(path, value, store)
    with os.fdopen(fd, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return deepcopy(dict(value))


def _atomic_create(path: Path, value: Mapping[str, Any]) -> bool:
    encoded = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return True


def _frozen_fields(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(value[key])
        for key in (
            "run_id",
            "routes",
            "scenarios",
            "execution_order",
            "shared_budget",
            "tool_permissions",
            "executor_class",
            "model_class",
            "authority",
            "claim_scope",
            "finalization_requirements",
        )
    }


def prepare_paired_route_isolation(
    root: Path,
    *,
    run_id: str,
    state: Mapping[str, Any],
    routes: Sequence[Mapping[str, Any]],
    scenarios: Sequence[Mapping[str, Any]],
    execution_order: Sequence[Mapping[str, Any]],
    shared_budget: Mapping[str, Any],
    tool_permissions: Sequence[str],
    executor_class: str,
    model_class: str,
    now: str | None = None,
) -> dict[str, Any]:
    """Freeze a two-route, three-scenario comparison before any child output."""

    root = root.resolve()
    store = Store(root)
    current = now or store.utc_now()
    authority = _authority(state, store, now=current)
    if isinstance(routes, (str, bytes)) or not isinstance(routes, Sequence):
        raise PairedRouteIsolationError("routes must be an array")
    exact_routes = [_route(route) for route in routes]
    if len(exact_routes) != 2:
        raise PairedRouteIsolationError("exactly two routes are required")
    route_ids = {route["route_id"] for route in exact_routes}
    if len(route_ids) != 2:
        raise PairedRouteIsolationError("route ids must be unique")
    if len({route["context_ref"] for route in exact_routes}) != 2:
        raise PairedRouteIsolationError("route context refs must be distinct")
    if len({route["context_digest"] for route in exact_routes}) != 2:
        raise PairedRouteIsolationError("route context digests must be distinct")
    if isinstance(scenarios, (str, bytes)) or not isinstance(scenarios, Sequence):
        raise PairedRouteIsolationError("scenarios must be an array")
    exact_scenarios = [_scenario(scenario) for scenario in scenarios]
    if len(exact_scenarios) != 3:
        raise PairedRouteIsolationError("exactly three scenarios are required")
    scenario_ids = {scenario["scenario_id"] for scenario in exact_scenarios}
    if len(scenario_ids) != 3:
        raise PairedRouteIsolationError("scenario ids must be unique")
    exact_order = _order(
        execution_order,
        scenario_ids=scenario_ids,
        route_ids=route_ids,
    )
    frozen = {
        "run_id": _identifier(run_id, "run_id"),
        "routes": exact_routes,
        "scenarios": exact_scenarios,
        "execution_order": exact_order,
        "shared_budget": _budget(shared_budget),
        "tool_permissions": _permissions(tool_permissions),
        "executor_class": _identifier(executor_class, "executor_class"),
        "model_class": _identifier(model_class, "model_class"),
        "authority": _stable_authority(authority),
        "claim_scope": CLAIM_SCOPE,
        "finalization_requirements": deepcopy(_FINALIZATION_REQUIREMENTS),
    }
    _walk_public(frozen, "precommit")
    comparison_id = "paired-route-" + store.stable_digest(frozen, length=24)
    path = _directory(root, frozen["run_id"], comparison_id) / "precommit.json"
    body = {
        "schema_version": 1,
        "record_type": "paired_route_precommit",
        "comparison_id": comparison_id,
        "status": _STATUS,
        **frozen,
        "prepared_authority": authority,
        "prepared_at": current,
    }
    body["precommit_digest"] = store.stable_digest(body, length=64)
    return _write_once(path, body, store)


def _load_precommit(
    root: Path,
    *,
    run_id: str,
    comparison_id: str,
) -> tuple[Store, dict[str, Any]]:
    root = root.resolve()
    store = Store(root)
    exact_run = _identifier(run_id, "run_id")
    exact_comparison = _identifier(comparison_id, "comparison_id")
    value = _read_record(
        _directory(root, exact_run, exact_comparison) / "precommit.json", store
    )
    if value.get("run_id") != exact_run or value.get("comparison_id") != exact_comparison:
        raise PairedRouteIsolationError("paired route precommit identity mismatch")
    if value.get("schema_version") != 1:
        raise PairedRouteIsolationError("paired route precommit schema is unsupported")
    if value.get("status") != _STATUS or value.get("claim_scope") != CLAIM_SCOPE:
        raise PairedRouteIsolationError("paired route precommit scope mismatch")
    routes = value.get("routes")
    scenarios = value.get("scenarios")
    if not isinstance(routes, list) or not isinstance(scenarios, list):
        raise PairedRouteIsolationError("stored routes and scenarios must be arrays")
    exact_routes = [_route(route) for route in routes]
    exact_scenarios = [_scenario(scenario) for scenario in scenarios]
    if len(exact_routes) != 2 or len({route["route_id"] for route in exact_routes}) != 2:
        raise PairedRouteIsolationError("stored precommit must contain two unique routes")
    if len({route["context_ref"] for route in exact_routes}) != 2 or len(
        {route["context_digest"] for route in exact_routes}
    ) != 2:
        raise PairedRouteIsolationError("stored route contexts must be distinct")
    if len(exact_scenarios) != 3 or len(
        {scenario["scenario_id"] for scenario in exact_scenarios}
    ) != 3:
        raise PairedRouteIsolationError(
            "stored precommit must contain three unique scenarios"
        )
    exact_order = _order(
        value.get("execution_order"),
        scenario_ids={scenario["scenario_id"] for scenario in exact_scenarios},
        route_ids={route["route_id"] for route in exact_routes},
    )
    exact_authority = _loaded_authority(value.get("authority"), prepared=False)
    prepared_authority = _loaded_authority(
        value.get("prepared_authority"), prepared=True
    )
    if _stable_authority(prepared_authority) != exact_authority:
        raise PairedRouteIsolationError("prepared authority does not match frozen authority")
    _timestamp(value.get("prepared_at"), "prepared_at")
    frozen = {
        "run_id": _identifier(value.get("run_id"), "stored run_id"),
        "routes": exact_routes,
        "scenarios": exact_scenarios,
        "execution_order": exact_order,
        "shared_budget": _budget(value.get("shared_budget")),
        "tool_permissions": _permissions(value.get("tool_permissions")),
        "executor_class": _identifier(
            value.get("executor_class"), "stored executor_class"
        ),
        "model_class": _identifier(value.get("model_class"), "stored model_class"),
        "authority": exact_authority,
        "claim_scope": value.get("claim_scope"),
        "finalization_requirements": value.get("finalization_requirements"),
    }
    if _frozen_fields(value) != frozen:
        raise PairedRouteIsolationError("stored precommit is not canonical")
    _walk_public(frozen, "stored_precommit")
    expected_id = "paired-route-" + store.stable_digest(frozen, length=24)
    if expected_id != exact_comparison:
        raise PairedRouteIsolationError("paired route precommit frozen identity mismatch")
    if value.get("finalization_requirements") != _FINALIZATION_REQUIREMENTS:
        raise PairedRouteIsolationError("paired route finalization requirements mismatch")
    return store, value


def paired_route_child_binding(
    root: Path,
    *,
    run_id: str,
    comparison_id: str,
    scenario_id: str,
    route_id: str,
) -> dict[str, Any]:
    """Materialize one route-isolated child binding without judge reveal data."""

    store, precommit = _load_precommit(
        root, run_id=run_id, comparison_id=comparison_id
    )
    exact_scenario = _identifier(scenario_id, "scenario_id")
    exact_route = _identifier(route_id, "route_id")
    scenarios = [
        item for item in precommit["scenarios"] if item["scenario_id"] == exact_scenario
    ]
    routes = [item for item in precommit["routes"] if item["route_id"] == exact_route]
    orders = [
        item
        for item in precommit["execution_order"]
        if item["scenario_id"] == exact_scenario
    ]
    if len(scenarios) != 1 or len(routes) != 1 or len(orders) != 1:
        raise PairedRouteIsolationError("unknown scenario or route child binding")
    position = orders[0]["route_ids"].index(exact_route)
    binding = {
        "schema_version": 1,
        "record_type": "paired_route_child_binding",
        "run_id": precommit["run_id"],
        "comparison_id": precommit["comparison_id"],
        "precommit_digest": precommit["precommit_digest"],
        "scenario": deepcopy(scenarios[0]),
        "route": deepcopy(routes[0]),
        "execution_position": position,
        "execution_width": 2,
        "shared_budget": deepcopy(precommit["shared_budget"]),
        "tool_permissions": deepcopy(precommit["tool_permissions"]),
        "executor_class": precommit["executor_class"],
        "model_class": precommit["model_class"],
        "response_contract": {
            "answer_format": "canonical_json",
            "response_pointer": ["result", "behavioral_answer"],
            "private_reasoning_forbidden": True,
            "secrets_forbidden": True,
        },
        "claim_scope": CLAIM_SCOPE,
    }
    binding["binding_digest"] = store.stable_digest(binding, length=64)
    return binding


def verify_paired_route_precommit(
    root: Path,
    *,
    run_id: str,
    comparison_id: str,
) -> dict[str, Any]:
    """Verify the precommit and derive all six non-overlapping child bindings."""

    _, precommit = _load_precommit(
        root, run_id=run_id, comparison_id=comparison_id
    )
    bindings = [
        paired_route_child_binding(
            root,
            run_id=run_id,
            comparison_id=comparison_id,
            scenario_id=row["scenario_id"],
            route_id=route_id,
        )
        for row in precommit["execution_order"]
        for route_id in row["route_ids"]
    ]
    digests = [binding["binding_digest"] for binding in bindings]
    if len(bindings) != 6 or len(set(digests)) != 6:
        raise PairedRouteIsolationError("paired route child bindings are incomplete")
    return {
        "precommit": precommit,
        "child_bindings": bindings,
        "required_child_count": 6,
        "observations": [],
        "scores": [],
        "comparison_ready": False,
        "claim_scope": CLAIM_SCOPE,
    }


def _response_path(
    root: Path,
    *,
    run_id: str,
    comparison_id: str,
    scenario_id: str,
    route_id: str,
) -> Path:
    return (
        _directory(root, run_id, comparison_id)
        / "responses"
        / f"{scenario_id}--{route_id}.json"
    )


def _read_response(path: Path, store: Store) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise PairedRouteIsolationError(
            "missing or malformed paired route child response"
        ) from exc
    if not isinstance(value, dict) or set(value) != _RESPONSE_KEYS:
        raise PairedRouteIsolationError(
            "paired route child response has an unexpected schema"
        )
    if value.get("schema_version") != 1 or value.get("record_type") != (
        "paired_route_child_response"
    ):
        raise PairedRouteIsolationError("paired route child response type mismatch")
    body = deepcopy(value)
    supplied = body.pop("response_digest", None)
    if supplied != store.stable_digest(body, length=64):
        raise PairedRouteIsolationError("tampered paired route child response")
    _timestamp(value.get("recorded_at"), "recorded_at")
    _loaded_authority(value.get("authority"), prepared=False)
    response = value.get("response")
    if not isinstance(response, Mapping) or set(response) != {"result"}:
        raise PairedRouteIsolationError("child response must contain only result")
    result = response.get("result")
    if not isinstance(result, Mapping) or set(result) != {"behavioral_answer"}:
        raise PairedRouteIsolationError(
            "child result must contain only behavioral_answer"
        )
    _walk_public(response, "child_response")
    return value


def _current_finalization_authority(
    state: Mapping[str, Any],
    store: Store,
    precommit: Mapping[str, Any],
    *,
    now: str,
) -> dict[str, Any]:
    current = _authority(state, store, now=now)
    prepared = _loaded_authority(precommit.get("authority"), prepared=False)
    if current["highest_acknowledged_inbox_revision"] != prepared[
        "highest_acknowledged_inbox_revision"
    ]:
        raise PairedRouteIsolationError(
            "current inbox revision differs from frozen authority"
        )
    if current["lease_generation"] < prepared["lease_generation"]:
        raise PairedRouteIsolationError(
            "current lease generation predates frozen authority"
        )
    return _stable_authority(current)


def record_paired_route_child_response(
    root: Path,
    *,
    run_id: str,
    comparison_id: str,
    scenario_id: str,
    route_id: str,
    binding_digest: str,
    response: Mapping[str, Any],
    state: Mapping[str, Any],
    now: str | None = None,
) -> dict[str, Any]:
    """Record one exact child response once, without any rubric reveal data."""

    root = root.resolve()
    store, precommit = _load_precommit(
        root, run_id=run_id, comparison_id=comparison_id
    )
    current = now or store.utc_now()
    authority = _current_finalization_authority(
        state, store, precommit, now=current
    )
    binding = paired_route_child_binding(
        root,
        run_id=run_id,
        comparison_id=comparison_id,
        scenario_id=scenario_id,
        route_id=route_id,
    )
    exact_binding_digest = _digest(binding_digest, "binding_digest")
    if exact_binding_digest != binding["binding_digest"]:
        raise PairedRouteIsolationError("child binding digest mismatch")
    exact_response = deepcopy(response)
    if not isinstance(exact_response, Mapping) or set(exact_response) != {"result"}:
        raise PairedRouteIsolationError("child response must contain only result")
    result = exact_response.get("result")
    if not isinstance(result, Mapping) or set(result) != {"behavioral_answer"}:
        raise PairedRouteIsolationError(
            "child result must contain only behavioral_answer"
        )
    _walk_public(exact_response, "child_response")
    if len(_canonical_bytes(exact_response)) > precommit["shared_budget"][
        "max_response_bytes"
    ]:
        raise PairedRouteIsolationError("child response exceeds its byte budget")
    value = {
        "schema_version": 1,
        "record_type": "paired_route_child_response",
        "run_id": precommit["run_id"],
        "comparison_id": precommit["comparison_id"],
        "precommit_digest": precommit["precommit_digest"],
        "scenario_id": binding["scenario"]["scenario_id"],
        "route_id": binding["route"]["route_id"],
        "binding_digest": exact_binding_digest,
        "response": exact_response,
        "authority": authority,
        "recorded_at": current,
        "claim_scope": CLAIM_SCOPE,
    }
    value["response_digest"] = store.stable_digest(value, length=64)
    path = _response_path(
        root,
        run_id=precommit["run_id"],
        comparison_id=precommit["comparison_id"],
        scenario_id=value["scenario_id"],
        route_id=value["route_id"],
    )
    if _atomic_create(path, value):
        return deepcopy(value)
    existing = _read_response(path, store)
    if existing["response_digest"] != value["response_digest"]:
        comparable_existing = deepcopy(existing)
        comparable_value = deepcopy(value)
        for item in (comparable_existing, comparable_value):
            item.pop("recorded_at", None)
            item.pop("authority", None)
            item.pop("response_digest", None)
        if comparable_existing != comparable_value:
            raise PairedRouteIsolationError("immutable child response conflict")
    return existing


def _read_all_responses(
    root: Path,
    store: Store,
    precommit: Mapping[str, Any],
) -> list[dict[str, Any]]:
    expected = [
        (row["scenario_id"], route_id)
        for row in precommit["execution_order"]
        for route_id in row["route_ids"]
    ]
    paths = [
        _response_path(
            root,
            run_id=precommit["run_id"],
            comparison_id=precommit["comparison_id"],
            scenario_id=scenario_id,
            route_id=route_id,
        )
        for scenario_id, route_id in expected
    ]
    if len(paths) != 6 or not all(path.is_file() for path in paths):
        raise PairedRouteIsolationError(
            "all six immutable child responses are required before rubric reveal"
        )
    responses = [_read_response(path, store) for path in paths]
    observed = [(item["scenario_id"], item["route_id"]) for item in responses]
    if observed != expected or len({item["response_digest"] for item in responses}) != 6:
        raise PairedRouteIsolationError("exact child response set mismatch")
    for response in responses:
        binding = paired_route_child_binding(
            root,
            run_id=precommit["run_id"],
            comparison_id=precommit["comparison_id"],
            scenario_id=response["scenario_id"],
            route_id=response["route_id"],
        )
        if response["binding_digest"] != binding["binding_digest"]:
            raise PairedRouteIsolationError("stored child binding digest mismatch")
        if response["precommit_digest"] != precommit["precommit_digest"]:
            raise PairedRouteIsolationError("stored response precommit mismatch")
    return responses


def finalize_paired_route_comparison(
    root: Path,
    *,
    run_id: str,
    comparison_id: str,
    rubric_reveals: Mapping[str, Mapping[str, Any]],
    state: Mapping[str, Any],
    now: str | None = None,
) -> dict[str, Any]:
    """Verify private reveals only after six responses and persist judgments only."""

    root = root.resolve()
    store, precommit = _load_precommit(
        root, run_id=run_id, comparison_id=comparison_id
    )
    responses = _read_all_responses(root, store, precommit)
    current = now or store.utc_now()
    authority = _current_finalization_authority(
        state, store, precommit, now=current
    )
    scenario_ids = [item["scenario_id"] for item in precommit["scenarios"]]
    if not isinstance(rubric_reveals, Mapping) or set(rubric_reveals) != set(
        scenario_ids
    ):
        raise PairedRouteIsolationError("all and only three rubric reveals are required")
    expected_answers: dict[str, Any] = {}
    for scenario in precommit["scenarios"]:
        scenario_id = scenario["scenario_id"]
        reveal = rubric_reveals[scenario_id]
        if not isinstance(reveal, Mapping) or set(reveal) != {
            "expected_answer",
            "nonce",
        }:
            raise PairedRouteIsolationError("rubric reveal has an unexpected schema")
        expected_answer = deepcopy(reveal.get("expected_answer"))
        _walk_public(expected_answer, "expected_answer")
        supplied = compute_rubric_commitment_digest(
            scenario_id=scenario_id,
            expected_answer=expected_answer,
            nonce=reveal.get("nonce"),
            store=store,
        )
        if supplied != scenario["rubric_commitment"]["commitment_digest"]:
            raise PairedRouteIsolationError("rubric reveal commitment mismatch")
        expected_answers[scenario_id] = expected_answer
    judgments = []
    for response in responses:
        answer = response["response"]["result"]["behavioral_answer"]
        passed = _canonical_bytes(answer) == _canonical_bytes(
            expected_answers[response["scenario_id"]]
        )
        judgments.append(
            {
                "scenario_id": response["scenario_id"],
                "route_id": response["route_id"],
                "response_digest": response["response_digest"],
                "passed": passed,
                "score": 1.0 if passed else 0.0,
                "judge_kind": "exact_canonical_json",
                "judge_version": "exact-canonical-json-v1",
            }
        )
    route_summaries = []
    for route in precommit["routes"]:
        route_judgments = [
            item for item in judgments if item["route_id"] == route["route_id"]
        ]
        passed_count = sum(item["passed"] for item in route_judgments)
        route_summaries.append(
            {
                "route_id": route["route_id"],
                "scenario_count": 3,
                "passed_count": passed_count,
                "exact_score": passed_count / 3,
            }
        )
    value = {
        "schema_version": 1,
        "record_type": "paired_route_finalization",
        "run_id": precommit["run_id"],
        "comparison_id": precommit["comparison_id"],
        "precommit_digest": precommit["precommit_digest"],
        "status": _FINALIZED_STATUS,
        "required_child_count": 6,
        "response_digests": [item["response_digest"] for item in responses],
        "judgments": judgments,
        "route_summaries": route_summaries,
        "authority": authority,
        "finalized_at": current,
        "claim_scope": (
            "exact_two_route_three_scenario_judgments_only_"
            "not_behavioral_family_or_completion_evidence"
        ),
    }
    _walk_public(value, "finalization")
    value["finalization_digest"] = store.stable_digest(value, length=64)
    path = _directory(root, precommit["run_id"], precommit["comparison_id"]) / (
        "finalization.json"
    )
    if _atomic_create(path, value):
        return deepcopy(value)
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise PairedRouteIsolationError("malformed paired route finalization") from exc
    if not isinstance(existing, dict) or set(existing) != _FINALIZATION_KEYS:
        raise PairedRouteIsolationError("paired route finalization has unexpected schema")
    body = deepcopy(existing)
    supplied = body.pop("finalization_digest", None)
    if supplied != store.stable_digest(body, length=64):
        raise PairedRouteIsolationError("tampered paired route finalization")
    comparable_existing = deepcopy(existing)
    comparable_value = deepcopy(value)
    for item in (comparable_existing, comparable_value):
        item.pop("finalized_at", None)
        item.pop("authority", None)
        item.pop("finalization_digest", None)
    if comparable_existing != comparable_value:
        raise PairedRouteIsolationError("immutable paired route finalization conflict")
    return existing
