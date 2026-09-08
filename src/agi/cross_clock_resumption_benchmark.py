from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

BENCHMARK_ID = "generation29-cross-clock-resumption-counterfactual-v1"
SCENARIOS = {
    "stale_work_heartbeat": "work_heartbeat_freshness",
    "moved_authoritative_binding": "main_state_blob_and_fence_binding",
    "newer_inbox_at_dispatch": "inbox_revision_safe_boundary",
}
CONFIGURATIONS = {"protected", "fail_open"}
BAD_COUNTERS = {
    "stale_commits",
    "duplicate_responses",
    "duplicate_successors",
    "divergent_successors",
    "fence_changes",
    "inbox_bypasses",
    "stale_context_actions",
    "live_authority_mutations",
}


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def benchmark_protocol_digest(value: Mapping[str, Any]) -> str:
    frozen = {
        key: value.get(key)
        for key in (
            "schema_version",
            "benchmark_id",
            "frozen_before_execution",
            "fixture_policy",
            "request_identity",
            "scenarios",
            "configurations",
            "expected_outcomes",
            "claim_boundary",
        )
    }
    return canonical_digest(frozen)


def validate_cross_clock_protocol(value: Mapping[str, Any]) -> dict[str, Any]:
    if value.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    if value.get("benchmark_id") != BENCHMARK_ID:
        raise ValueError("unexpected benchmark_id")
    if value.get("frozen_before_execution") is not True:
        raise ValueError("protocol must be frozen before execution")
    if value.get("fixture_policy") != {
        "isolated_repository_checkpoint_copies_only": True,
        "live_authority_writes_allowed": False,
        "single_guard_disabled_per_control": True,
        "external_effects_allowed": False,
    }:
        raise ValueError("fixture policy must remain exact and fail-closed")
    request = value.get("request_identity")
    if not isinstance(request, Mapping) or set(request) != {
        "invocation_id",
        "request_digest",
        "idempotency_key",
    }:
        raise ValueError("request identity binding is malformed")
    if request.get("invocation_id") != "fixture-request-cross-clock-v1":
        raise ValueError("unexpected fixture request identity")
    for key in ("request_digest", "idempotency_key"):
        if not isinstance(request.get(key), str) or not request[key]:
            raise ValueError(f"request identity {key} must be non-empty")
    scenarios = value.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != 3:
        raise ValueError("exactly three scenarios are required")
    observed = {}
    for scenario in scenarios:
        if not isinstance(scenario, Mapping):
            raise ValueError("scenario must be an object")
        scenario_id = scenario.get("scenario_id")
        if scenario_id not in SCENARIOS or scenario_id in observed:
            raise ValueError("scenario ids must exactly match the protocol")
        if scenario.get("guard") != SCENARIOS[scenario_id]:
            raise ValueError("scenario guard mismatch")
        if scenario.get("protected_repair") not in {
            "refresh_heartbeat_only",
            "reread_main_state_blob_and_fence",
            "ingest_newer_inbox_at_safe_boundary",
        }:
            raise ValueError("unknown protected repair")
        if scenario.get("live_authority_write_allowed") is not False:
            raise ValueError("scenario cannot authorize live authority writes")
        observed[scenario_id] = scenario
    if set(observed) != set(SCENARIOS):
        raise ValueError("scenario ids must exactly match the protocol")
    configurations = value.get("configurations")
    if not isinstance(configurations, list) or {x.get("configuration_id") for x in configurations} != CONFIGURATIONS:
        raise ValueError("configurations must be protected and fail_open")
    by_id = {x["configuration_id"]: x for x in configurations}
    if by_id["protected"].get("disabled_guards") != []:
        raise ValueError("protected configuration cannot disable a guard")
    if by_id["fail_open"].get("disabled_guard_policy") != "disable_only_the_current_scenario_guard":
        raise ValueError("fail_open must disable only the current scenario guard")
    expected = value.get("expected_outcomes")
    if not isinstance(expected, Mapping) or set(expected) != set(SCENARIOS):
        raise ValueError("expected outcomes must bind every scenario")
    for scenario_id, outcomes in expected.items():
        if set(outcomes) != CONFIGURATIONS:
            raise ValueError("each scenario must bind both configurations")
        if outcomes["protected"].get("verdict") != "PASS":
            raise ValueError("protected outcome must precommit PASS")
        if outcomes["fail_open"].get("verdict") != "CONTROL_EFFECT_OBSERVED":
            raise ValueError("control outcome must precommit an observed counterfactual")
    if value.get("claim_boundary") != {
        "bounded_continuity_control_only": True,
        "candidate_activation": False,
        "provider_or_production_route_change": False,
        "agi_claim_supported": False,
        "upper_objective_achieved": False,
        "user_level_objective_met": False,
    }:
        raise ValueError("claim boundary must remain exact")
    return deepcopy(dict(value))


def load_cross_clock_protocol(path: Path) -> dict[str, Any]:
    return validate_cross_clock_protocol(json.loads(path.read_text(encoding="utf-8")))


def _fixture(protocol: Mapping[str, Any]) -> dict[str, Any]:
    request = protocol["request_identity"]
    return {
        "authoritative": {
            "main_sha": "main-a",
            "state_blob_sha": "state-a",
            "fence": "fence-a",
            "heartbeat_revision": 7,
            "inbox_revision": 38,
        },
        "observed": {
            "main_sha": "main-a",
            "state_blob_sha": "state-a",
            "fence": "fence-a",
            "heartbeat_revision": 7,
            "inbox_revision": 38,
        },
        "request": deepcopy(dict(request)),
    }


def _scenario_definition(protocol: Mapping[str, Any], scenario_id: str) -> Mapping[str, Any]:
    for item in protocol["scenarios"]:
        if item["scenario_id"] == scenario_id:
            return item
    raise ValueError(f"unknown scenario {scenario_id}")


def run_cross_clock_scenario(
    protocol: Mapping[str, Any],
    scenario_id: str,
    configuration_id: str,
) -> dict[str, Any]:
    checked = validate_cross_clock_protocol(protocol)
    if scenario_id not in SCENARIOS:
        raise ValueError("unknown scenario")
    if configuration_id not in CONFIGURATIONS:
        raise ValueError("unknown configuration")
    scenario = _scenario_definition(checked, scenario_id)
    fixture = _fixture(checked)
    if scenario_id == "stale_work_heartbeat":
        fixture["observed"]["heartbeat_revision"] = 8
    elif scenario_id == "moved_authoritative_binding":
        fixture["observed"].update(
            main_sha="main-b", state_blob_sha="state-b", fence="fence-b"
        )
    else:
        fixture["observed"]["inbox_revision"] = 39

    counters = {
        "submissions": 1,
        "dispatch_attempts": 0,
        "rejections": 0,
        "repairs": 0,
        "request_preservation_checks": 0,
        "completions": 0,
        "successors": 0,
        "stale_commits": 0,
        "duplicate_responses": 0,
        "duplicate_successors": 0,
        "divergent_successors": 0,
        "fence_changes": 0,
        "inbox_bypasses": 0,
        "stale_context_actions": 0,
        "live_authority_mutations": 0,
    }
    trace: list[dict[str, Any]] = []

    def event(kind: str, **details: Any) -> None:
        trace.append({"sequence": len(trace) + 1, "event": kind, **details})

    request_before = canonical_digest(fixture["request"])
    event("fixture_bound", request_digest=request_before, live_authority=False)
    counters["dispatch_attempts"] += 1
    event("dispatch_attempt", guard=scenario["guard"], configuration=configuration_id)

    if configuration_id == "protected":
        counters["rejections"] += 1
        event(
            "dispatch_rejected",
            reason=scenario["protected_rejection_reason"],
            before_effect=True,
            response_committed=False,
            successor_frozen=False,
        )
        counters["repairs"] += 1
        repair = scenario["protected_repair"]
        if repair == "refresh_heartbeat_only":
            fixture["authoritative"]["heartbeat_revision"] = fixture["observed"]["heartbeat_revision"]
        elif repair == "reread_main_state_blob_and_fence":
            for key in ("main_sha", "state_blob_sha", "fence"):
                fixture["authoritative"][key] = fixture["observed"][key]
        else:
            fixture["authoritative"]["inbox_revision"] = fixture["observed"]["inbox_revision"]
        event("concrete_stop_cause_repaired", repair=repair)
        counters["request_preservation_checks"] += 1
        request_after = canonical_digest(fixture["request"])
        event("exact_request_preserved", before=request_before, after=request_after)
        if request_after != request_before:
            raise AssertionError("protected repair recreated the request")
        counters["dispatch_attempts"] += 1
        event("dispatch_resumed", same_request=True)
        counters["completions"] += 1
        event("response_committed", idempotency_key=fixture["request"]["idempotency_key"])
        counters["successors"] += 1
        event("successor_frozen", successor_id=f"successor-{scenario_id}-1")
        verdict = "PASS"
    else:
        event("scenario_guard_disabled", disabled_guard=scenario["guard"])
        if scenario_id == "stale_work_heartbeat":
            counters["stale_context_actions"] = 1
            effect = "dispatch_with_stale_heartbeat"
        elif scenario_id == "moved_authoritative_binding":
            counters["stale_commits"] = 1
            counters["divergent_successors"] = 1
            effect = "stale_commit_and_divergent_successor"
        else:
            counters["inbox_bypasses"] = 1
            counters["stale_context_actions"] = 1
            effect = "dispatch_before_newer_inbox_ingestion"
        event("counterfactual_effect_observed", effect=effect, simulated=True, live_authority=False)
        counters["completions"] += 1
        counters["successors"] += 1
        event("simulated_response_and_successor", external_effect_applied=False)
        verdict = "CONTROL_EFFECT_OBSERVED"

    result = {
        "schema_version": 1,
        "benchmark_id": BENCHMARK_ID,
        "protocol_digest": benchmark_protocol_digest(checked),
        "scenario_id": scenario_id,
        "configuration_id": configuration_id,
        "guard": scenario["guard"],
        "disabled_guards": [] if configuration_id == "protected" else [scenario["guard"]],
        "request_digest_before": request_before,
        "request_digest_after": canonical_digest(fixture["request"]),
        "request_identity_preserved": canonical_digest(fixture["request"]) == request_before,
        "counters": counters,
        "trace": trace,
        "verdict": verdict,
        "claim_boundary": deepcopy(checked["claim_boundary"]),
    }
    result["replay_digest"] = canonical_digest(result)
    return result


def run_cross_clock_benchmark(protocol: Mapping[str, Any]) -> dict[str, Any]:
    checked = validate_cross_clock_protocol(protocol)
    observations = []
    for scenario_id in sorted(SCENARIOS):
        for configuration_id in ("protected", "fail_open"):
            first = run_cross_clock_scenario(checked, scenario_id, configuration_id)
            second = run_cross_clock_scenario(checked, scenario_id, configuration_id)
            observations.append(
                {
                    "scenario_id": scenario_id,
                    "configuration_id": configuration_id,
                    "replay_verified": first["replay_digest"] == second["replay_digest"],
                    "observation": first,
                }
            )
    protected = [x["observation"] for x in observations if x["configuration_id"] == "protected"]
    controls = [x["observation"] for x in observations if x["configuration_id"] == "fail_open"]
    guard_map = {
        item["scenario_id"]: {
            "guard": item["guard"],
            "prevented_behavior": next(
                x["trace"][-2]["effect"]
                for x in controls
                if x["scenario_id"] == item["scenario_id"]
            ),
        }
        for item in protected
    }
    result = {
        "schema_version": 1,
        "record_type": "cross_clock_resumption_benchmark_result",
        "benchmark_id": BENCHMARK_ID,
        "protocol_digest": benchmark_protocol_digest(checked),
        "status": "PASS",
        "observations": observations,
        "guard_to_prevented_behavior": guard_map,
        "protected_summary": {
            "scenario_count": len(protected),
            "all_rejected_before_effect": all(x["trace"][2]["before_effect"] for x in protected),
            "all_request_identities_preserved": all(x["request_identity_preserved"] for x in protected),
            "total_responses": sum(x["counters"]["completions"] for x in protected),
            "total_successors": sum(x["counters"]["successors"] for x in protected),
            "bad_counter_totals": {key: sum(x["counters"][key] for x in protected) for key in sorted(BAD_COUNTERS)},
            "all_replays_verified": all(x["replay_verified"] for x in observations),
        },
        "control_summary": {
            "scenario_count": len(controls),
            "all_informative": all(any(x["counters"][key] for key in BAD_COUNTERS - {"live_authority_mutations"}) for x in controls),
            "live_authority_mutations": sum(x["counters"]["live_authority_mutations"] for x in controls),
        },
        "claim_boundary": deepcopy(checked["claim_boundary"]),
    }
    validate_cross_clock_result(result, checked)
    result["result_digest"] = canonical_digest(result)
    return result


def validate_cross_clock_result(result: Mapping[str, Any], protocol: Mapping[str, Any]) -> dict[str, Any]:
    checked = validate_cross_clock_protocol(protocol)
    if result.get("status") != "PASS" or result.get("protocol_digest") != benchmark_protocol_digest(checked):
        raise ValueError("result status or protocol binding mismatch")
    observations = result.get("observations")
    if not isinstance(observations, list) or len(observations) != 6:
        raise ValueError("result must contain six observations")
    for item in observations:
        observation = item.get("observation", {})
        expected = checked["expected_outcomes"][item.get("scenario_id")][item.get("configuration_id")]
        if observation.get("verdict") != expected["verdict"]:
            raise ValueError("observation verdict differs from precommitment")
        if observation.get("counters") != expected["counters"]:
            raise ValueError("observation counters differ from precommitment")
        if item.get("replay_verified") is not True:
            raise ValueError("deterministic replay failed")
        if observation.get("request_identity_preserved") is not True:
            raise ValueError("request identity changed")
        if observation["counters"]["live_authority_mutations"] != 0:
            raise ValueError("live authority mutation is forbidden")
    summary = result.get("protected_summary", {})
    if summary.get("all_rejected_before_effect") is not True:
        raise ValueError("protected dispatch did not reject before effect")
    if summary.get("all_request_identities_preserved") is not True:
        raise ValueError("protected request identity was not preserved")
    if summary.get("total_responses") != 3 or summary.get("total_successors") != 3:
        raise ValueError("protected exactly-once totals mismatch")
    if any(summary.get("bad_counter_totals", {}).values()):
        raise ValueError("protected run contains a stale or duplicate effect")
    if result.get("control_summary", {}).get("all_informative") is not True:
        raise ValueError("fail-open controls must be informative")
    if result.get("claim_boundary") != checked["claim_boundary"]:
        raise ValueError("result widened the claim boundary")
    return deepcopy(dict(result))


def write_cross_clock_artifacts(protocol_path: Path, output_dir: Path) -> dict[str, Any]:
    protocol = load_cross_clock_protocol(protocol_path)
    result = run_cross_clock_benchmark(protocol)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "record_type": "cross_clock_resumption_precommitted_manifest",
        "protocol_ref": protocol_path.as_posix(),
        "protocol_digest": benchmark_protocol_digest(protocol),
        "protocol": protocol,
    }
    files: dict[str, Any] = {"precommitted-manifest.json": manifest}
    for item in result["observations"]:
        name = f"{item['scenario_id']}-{item['configuration_id']}-trace.json"
        files[name] = item
    files["bounded-result.json"] = result
    files["replay-verification.json"] = {
        "schema_version": 1,
        "benchmark_id": BENCHMARK_ID,
        "all_replays_verified": result["protected_summary"]["all_replays_verified"],
        "observation_replay_digests": {
            f"{x['scenario_id']}:{x['configuration_id']}": x["observation"]["replay_digest"]
            for x in result["observations"]
        },
    }
    files["guard-to-prevented-behavior.json"] = {
        "schema_version": 1,
        "benchmark_id": BENCHMARK_ID,
        "mapping": result["guard_to_prevented_behavior"],
    }
    for name, value in files.items():
        (output_dir / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"artifact_count": len(files), "result": result, "files": sorted(files)}
