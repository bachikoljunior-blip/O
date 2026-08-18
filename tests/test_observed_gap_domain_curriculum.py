from __future__ import annotations

import json
from pathlib import Path

from agi.observed_gap_domain_curriculum import (
    _precommit_domain_schedules,
    _select_domain_from_observed_gaps,
    run_observed_gap_domain_curriculum,
)


def _rows(pattern: tuple[bool, bool, bool]) -> list[dict[str, bool]]:
    return [
        {"supported_before_learning": value}
        for value in pattern
    ]


def test_domain_selector_follows_observed_gap_counts_not_fixed_order() -> None:
    first = _select_domain_from_observed_gaps(
        {
            "numeric": _rows((False, False, True)),
            "string": _rows((False, False, False)),
            "sequence": _rows((False, False, True)),
        },
        "domain-selection-commitment",
    )
    assert first["selected_domain"] == "string"
    assert first["selection_uses_observed_support"] is True
    assert first["fixed_domain_order_used"] is False

    second = _select_domain_from_observed_gaps(
        {
            "numeric": _rows((False, False, False)),
            "string": _rows((False, False, True)),
            "sequence": _rows((False, False, True)),
        },
        "domain-selection-commitment",
    )
    assert second["selected_domain"] == "numeric"
    assert second["scoreboard"]["numeric"]["unsupported_count"] == 3


def test_domain_target_schedules_are_deterministic_and_precommitted(tmp_path: Path) -> None:
    first = _precommit_domain_schedules(tmp_path, "observed-domain-schedule")
    second = _precommit_domain_schedules(tmp_path, "observed-domain-schedule")

    assert first == second
    assert first["precommitted_before_support_checks"] is True
    assert first["domains"] == ["numeric", "string", "sequence"]
    assert len(first["domain_schedule_commitment"]) > 0
    assert all(
        len(first["schedules"][domain]["target_schedule"]) >= 3
        for domain in first["domains"]
    )


def test_observed_gap_domain_curriculum_selects_learns_and_retains(
    runtime_repo: Path,
) -> None:
    report = run_observed_gap_domain_curriculum(
        runtime_repo,
        "observed-gap-domain-curriculum-test",
    )

    assert report["passed"] is True
    assert report["source_domains"] == ["numeric", "string", "sequence"]
    assert report["domain_target_schedules_precommitted_before_support_checks"] is True
    assert report["fixed_domain_order_used"] is False
    assert report["selection_uses_observed_support"] is True
    assert report["selected_domain"] in {"numeric", "string", "sequence"}
    selected_score = report["domain_selection"]["scoreboard"][report["selected_domain"]]
    assert selected_score["unsupported_count"] >= 2
    assert selected_score["eligible_for_acquisition"] is True
    assert all(
        len(report["support_attempts_by_domain"][domain]) == 3
        for domain in report["source_domains"]
    )
    assert report["acquisition_target_failed_closed_before_learning"] is True
    assert report["control_target_failed_closed_before_learning"] is True
    assert report["new_candidate_negative_evidence_retained"] is True
    assert report["transactional_commit"]["atomic_directory_rename"] is True
    assert report["transactional_commit"]["overwrite_allowed"] is False
    assert report["solver_candidate_ids_supplied_by_caller"] is False
    assert report["new_candidate_rediscovered"] is True
    assert report["fresh_engine_runs_unique"] is True
    assert report["numeric_prerequisite_retained"] is True
    assert report["string_prerequisite_retained"] is True
    assert report["sequence_prerequisite_retained"] is True
    assert report["control_gap_remained_failed_closed"] is True
    assert report["prior_candidate_state_unchanged"] is True
    assert report["prior_trial_state_unchanged"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_state_unchanged"] is True
    assert report["prior_runs_copied"] is False
    assert report["prior_episodes_copied"] is False
    assert report["prior_evidence_copied"] is False
    assert report["live_model_invocation_required"] is False
    assert "does not establish open-domain autonomous curriculum learning" in report["claim_boundary"]
    assert "independent production evaluation, or AGI" in report["claim_boundary"]

    matches = list(
        (
            runtime_repo
            / ".continual"
            / "evidence"
            / "observed-gap-domain-curriculum"
        ).glob("observed-gap-*.json")
    )
    assert len(matches) == 1
    persisted = json.loads(matches[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["candidate_id"] == report["candidate_id"]
