from __future__ import annotations

from agi.external_tool_runtime_retention import run_external_tool_runtime_retention


def test_behavior_bound_external_tool_survives_fresh_engine_reattachment(runtime_repo) -> None:
    report = run_external_tool_runtime_retention(
        runtime_repo,
        "external-runtime-retention-20260817",
    )

    assert report["passed"] is True
    assert report["distinct_runtime_runs"] is True
    assert report["same_run_replay_reused"] is True
    assert report["fresh_engine_replay_matched"] is True
    assert report["spoofed_identity_wrong_behavior_rejected_after_restart"] is True
    assert report["candidate_trial_state_unchanged"] is True
    assert report["candidate_trial_file_count"] >= 2
    assert report["execution_kind"] == "verified_learned_tool"
    assert report["program_kind"] == "contracted_external_tool"
    assert report["live_model_invocation_required"] is False
    assert "does not prove open-ended tool use or AGI" in report["claim_boundary"]
