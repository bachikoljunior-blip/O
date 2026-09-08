from __future__ import annotations

from agi.varied_composition import run_varied_compositional_campaign


def test_varied_campaign_is_deterministic_for_same_seed() -> None:
    first = run_varied_compositional_campaign("deterministic-seed")
    second = run_varied_compositional_campaign("deterministic-seed")

    assert first == second
    assert first["passed"] is True
    assert first["evidence_tier"] == "development"


def test_fresh_seeds_change_evaluator_selected_program_structures() -> None:
    reports = [
        run_varied_compositional_campaign(seed)
        for seed in ("compose-a", "compose-b", "compose-c")
    ]

    commitment_sets = [tuple(sorted(report["program_commitments"].items())) for report in reports]
    assert len(set(commitment_sets)) == len(reports)

    learned_programs = [
        tuple(
            (skill["skill_id"], tuple(skill["expanded_primitives"]))
            for skill in report["skills"]
            if skill["skill_id"] in {"text-base", "numeric-composed", "sequence-composed"}
        )
        for report in reports
    ]
    assert len(set(learned_programs)) == len(reports)


def test_varied_campaign_scores_withheld_queries_and_requires_macro_reuse() -> None:
    report = run_varied_compositional_campaign("heldout-macro-seed")

    assert report["passed"] is True
    assert report["snapshot_reload_verified"] is True
    assert report["rejected_bad_update"] is True
    assert len(report["task_results"]) == 8
    assert all(item["success"] for item in report["task_results"])
    assert report["regression_decision"]["adopt_candidate"] is True
    assert report["regression_decision"]["decision"] == "ACTIVE_FOR_SCOPE"
    assert report["regression_decision"]["protected_score_drops"] == []
    assert report["regression_decision"]["target_repeats"] == {
        "numeric-composed": 2,
        "sequence-composed": 2,
        "text-base": 2,
        "text-derived": 2,
    }

    derived = [item for item in report["skills"] if item["skill_id"] == "text-derived"]
    assert len(derived) == 1
    assert "skill:text-base" in derived[0]["terms"]
    assert "not AGI" in report["claim_boundary"]


def test_report_exposes_commitments_not_evaluator_program_answers_before_scoring() -> None:
    report = run_varied_compositional_campaign("commitment-only-seed")

    assert set(report["program_commitments"]) == {
        "text-base",
        "numeric-composed",
        "sequence-composed",
        "text-derived",
    }
    assert all(len(value) == 64 for value in report["program_commitments"].values())
    assert all("expected_sha256" not in item for item in report["task_results"])
    assert "bounded" in report["claim_boundary"]
