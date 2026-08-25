from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .paired_route_isolation import (
    PairedRouteIsolationError,
    prepare_paired_route_isolation,
)


PUBLIC_INPUTS_REF = (
    "agi/evaluations/revision22_action_adherence/public_inputs.json"
)
EXPECTED_SCOPE = "agi/context-kernel/held-out-action-adherence-revision22-v1"
EXPECTED_INVOCATION = "invoke-248450c08547af10470c50e6"
EXPECTED_ROUTE_IDS = ("current-context-kernel", "manifest-free-control")
EXPECTED_SCENARIO_IDS = (
    "stale-authority-effect",
    "safe-supersede-boundary",
    "fresh-path-disjoint-main-advance",
)
_DIGEST_CHARS = set("0123456789abcdef")


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PairedRouteIsolationError(f"{label} is not readable canonical JSON") from exc
    if not isinstance(value, dict):
        raise PairedRouteIsolationError(f"{label} must contain an object")
    return value


def _sha256_bytes(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise PairedRouteIsolationError("route context is not readable") from exc


def _commitment(value: Any, scenario_id: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PairedRouteIsolationError(
            f"rubric commitment is missing for {scenario_id}"
        )
    digest = value.get("commitment_digest")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in _DIGEST_CHARS for character in digest)
    ):
        raise PairedRouteIsolationError(
            f"rubric commitment digest is invalid for {scenario_id}"
        )
    exact = dict(value)
    if set(exact) != {
        "judge_kind",
        "judge_version",
        "commitment_digest",
        "success_threshold",
    }:
        raise PairedRouteIsolationError(
            f"rubric commitment schema is invalid for {scenario_id}"
        )
    return exact


def load_revision22_public_inputs(
    root: Path,
    *,
    rubric_commitments: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Load public inputs and attach only already-sealed rubric commitments.

    Expected answers and nonces are deliberately not accepted here. A trusted
    coordinator keeps them outside every child-visible public input and reveals
    them only after all six immutable responses exist.
    """

    root = root.resolve()
    value = _load_object(root / PUBLIC_INPUTS_REF, "revision-22 public inputs")
    if value.get("schema_version") != 1:
        raise PairedRouteIsolationError("public input schema is unsupported")
    if value.get("record_type") != "revision22_paired_route_public_inputs":
        raise PairedRouteIsolationError("public input record type mismatch")
    if value.get("scope") != EXPECTED_SCOPE:
        raise PairedRouteIsolationError("public input scope mismatch")
    if value.get("frozen_invocation_id") != EXPECTED_INVOCATION:
        raise PairedRouteIsolationError("frozen invocation binding mismatch")
    if value.get("rubric_boundary") != {
        "commitments_required_before_precommit": True,
        "answers_and_nonces_absent_from_public_inputs": True,
        "reveal_only_after_all_six_immutable_child_responses": True,
    }:
        raise PairedRouteIsolationError("rubric boundary mismatch")

    routes = value.get("routes")
    scenarios = value.get("scenarios")
    if not isinstance(routes, list) or [row.get("route_id") for row in routes] != list(
        EXPECTED_ROUTE_IDS
    ):
        raise PairedRouteIsolationError("exact revision-22 routes are required")
    if not isinstance(scenarios, list) or [
        row.get("scenario_id") for row in scenarios
    ] != list(EXPECTED_SCENARIO_IDS):
        raise PairedRouteIsolationError("exact revision-22 scenarios are required")
    if set(rubric_commitments) != set(EXPECTED_SCENARIO_IDS):
        raise PairedRouteIsolationError("all and only three rubric commitments are required")

    exact_routes = []
    for route in routes:
        ref = route.get("context_ref")
        if not isinstance(ref, str) or ref.startswith("/") or ".." in Path(ref).parts:
            raise PairedRouteIsolationError("route context ref is unsafe")
        context = _load_object(root / ref, "route context")
        if context.get("route_id") != route["route_id"]:
            raise PairedRouteIsolationError("route context identity mismatch")
        exact_routes.append({**route, "context_digest": _sha256_bytes(root / ref)})

    exact_scenarios = []
    for scenario in scenarios:
        scenario_id = scenario["scenario_id"]
        expected_commitment = {
            "judge_kind": scenario.pop("judge_kind"),
            "judge_version": scenario.pop("judge_version"),
            "commitment_digest": rubric_commitments[scenario_id].get(
                "commitment_digest"
            ),
            "success_threshold": scenario.pop("success_threshold"),
        }
        supplied = _commitment(rubric_commitments[scenario_id], scenario_id)
        if supplied != expected_commitment:
            raise PairedRouteIsolationError(
                f"rubric judge binding mismatch for {scenario_id}"
            )
        exact_scenarios.append({**scenario, "rubric_commitment": supplied})

    return {
        "routes": exact_routes,
        "scenarios": exact_scenarios,
        "execution_order": deepcopy(value["execution_order"]),
        "shared_budget": deepcopy(value["shared_budget"]),
        "tool_permissions": deepcopy(value["tool_permissions"]),
        "executor_class": value["executor_class"],
        "model_class": value["model_class"],
        "claim_boundary": value["claim_boundary"],
    }


def prepare_revision22_paired_route_isolation(
    root: Path,
    *,
    run_id: str,
    state: Mapping[str, Any],
    rubric_commitments: Mapping[str, Mapping[str, Any]],
    now: str | None = None,
) -> dict[str, Any]:
    """Prepare the frozen unit without exposing a rubric answer or nonce."""

    inputs = load_revision22_public_inputs(
        root, rubric_commitments=rubric_commitments
    )
    return prepare_paired_route_isolation(
        root,
        run_id=run_id,
        state=state,
        routes=inputs["routes"],
        scenarios=inputs["scenarios"],
        execution_order=inputs["execution_order"],
        shared_budget=inputs["shared_budget"],
        tool_permissions=inputs["tool_permissions"],
        executor_class=inputs["executor_class"],
        model_class=inputs["model_class"],
        now=now,
    )
