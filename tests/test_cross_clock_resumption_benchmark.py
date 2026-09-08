from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from agi.cross_clock_resumption_benchmark import (
    BAD_COUNTERS,
    benchmark_protocol_digest,
    load_cross_clock_protocol,
    run_cross_clock_benchmark,
    run_cross_clock_scenario,
    validate_cross_clock_protocol,
    validate_cross_clock_result,
    write_cross_clock_artifacts,
)

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "agi" / "CROSS_CLOCK_RESUMPTION_BENCHMARK.json"


def _protocol() -> dict:
    return json.loads(PROTOCOL.read_text(encoding="utf-8"))


def test_checked_in_protocol_is_frozen_and_fail_closed() -> None:
    value = load_cross_clock_protocol(PROTOCOL)
    assert value["frozen_before_execution"] is True
    assert value["fixture_policy"]["isolated_repository_checkpoint_copies_only"] is True
    assert value["fixture_policy"]["live_authority_writes_allowed"] is False
    assert len(benchmark_protocol_digest(value)) == 64


@pytest.mark.parametrize("scenario_id", [
    "stale_work_heartbeat",
    "moved_authoritative_binding",
    "newer_inbox_at_dispatch",
])
def test_protected_scenarios_reject_repair_and_resume_exactly_once(scenario_id: str) -> None:
    result = run_cross_clock_scenario(_protocol(), scenario_id, "protected")
    assert result["verdict"] == "PASS"
    assert result["request_identity_preserved"] is True
    assert result["trace"][2]["event"] == "dispatch_rejected"
    assert result["trace"][2]["before_effect"] is True
    assert result["counters"]["rejections"] == 1
    assert result["counters"]["repairs"] == 1
    assert result["counters"]["completions"] == 1
    assert result["counters"]["successors"] == 1
    assert all(result["counters"][key] == 0 for key in BAD_COUNTERS)


def test_fail_open_controls_are_single_guard_and_informative() -> None:
    value = _protocol()
    results = {
        scenario: run_cross_clock_scenario(value, scenario, "fail_open")
        for scenario in (
            "stale_work_heartbeat",
            "moved_authoritative_binding",
            "newer_inbox_at_dispatch",
        )
    }
    assert results["stale_work_heartbeat"]["counters"]["stale_context_actions"] == 1
    assert results["moved_authoritative_binding"]["counters"]["stale_commits"] == 1
    assert results["moved_authoritative_binding"]["counters"]["divergent_successors"] == 1
    assert results["newer_inbox_at_dispatch"]["counters"]["inbox_bypasses"] == 1
    for result in results.values():
        assert len(result["disabled_guards"]) == 1
        assert result["counters"]["live_authority_mutations"] == 0
        assert result["verdict"] == "CONTROL_EFFECT_OBSERVED"


def test_benchmark_replays_byte_stably_and_preserves_claim_boundary() -> None:
    value = _protocol()
    first = run_cross_clock_benchmark(value)
    second = run_cross_clock_benchmark(value)
    assert first["result_digest"] == second["result_digest"]
    assert first["protected_summary"]["all_replays_verified"] is True
    assert first["protected_summary"]["total_responses"] == 3
    assert first["protected_summary"]["total_successors"] == 3
    assert all(count == 0 for count in first["protected_summary"]["bad_counter_totals"].values())
    assert first["control_summary"]["all_informative"] is True
    assert first["claim_boundary"]["agi_claim_supported"] is False
    validate_cross_clock_result(first, value)


def test_artifact_writer_is_deterministic_and_complete(tmp_path: Path) -> None:
    first = write_cross_clock_artifacts(PROTOCOL, tmp_path / "a")
    second = write_cross_clock_artifacts(PROTOCOL, tmp_path / "b")
    assert first["artifact_count"] == 10
    assert first["files"] == second["files"]
    for name in first["files"]:
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
    bounded = json.loads((tmp_path / "a" / "bounded-result.json").read_text())
    assert bounded["status"] == "PASS"


def test_protocol_rejects_live_authority_and_multi_guard_controls() -> None:
    value = _protocol()
    value["fixture_policy"]["live_authority_writes_allowed"] = True
    with pytest.raises(ValueError, match="fixture policy"):
        validate_cross_clock_protocol(value)
    value = _protocol()
    value["configurations"][1]["disabled_guard_policy"] = "disable_all_guards"
    with pytest.raises(ValueError, match="only the current"):
        validate_cross_clock_protocol(value)


def test_result_validation_rejects_protected_stale_effect() -> None:
    value = _protocol()
    result = run_cross_clock_benchmark(value)
    protected = next(x["observation"] for x in result["observations"] if x["configuration_id"] == "protected")
    protected["counters"]["stale_commits"] = 1
    with pytest.raises(ValueError, match="precommitment"):
        validate_cross_clock_result(result, value)
