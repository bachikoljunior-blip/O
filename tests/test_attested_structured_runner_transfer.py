from __future__ import annotations

from pathlib import Path

from agi.attested_structured_runner_transfer import run_attested_structured_runner_transfer


def test_attested_structured_runner_transfer_preserves_candidate_evidence(
    runtime_repo: Path,
) -> None:
    report = run_attested_structured_runner_transfer(
        runtime_repo,
        "attested-structured-transfer-20260817",
    )

    assert report["passed"] is True
    assert report["domain_chain"] == ["numeric", "object", "boolean"]
    assert report["fresh_engine_count"] == 3
    assert len(set(report["fresh_engine_runs"])) == 3
    assert report["fresh_engine_composition_matched"] is True
    assert report["runner_attestation_checked"] is True
    assert report["runner_test_double_only"] is True
    assert report["runner_isolation_profile"] == {
        "execution_boundary": "external-isolated-runner",
        "canonical_json_transport_only": True,
        "filesystem_isolated": True,
        "network_isolated": True,
        "subprocess_isolated": True,
        "environment_isolated": True,
        "resource_limits_enforced": True,
    }
    assert report["candidate_trial_state_unchanged"] is True
    assert all(count >= 2 for count in report["candidate_trial_file_counts"].values())
    assert report["external_negative_evidence_retained"] is True
    assert report["learned_negative_evidence_retained"] is True
    assert report["learned_target_repeats"] >= 2
    assert report["live_model_invocation_required"] is False
