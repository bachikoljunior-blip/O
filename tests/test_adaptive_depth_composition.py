from __future__ import annotations

import json
from pathlib import Path

import pytest

from agi.adaptive_depth_composition import (
    AdaptiveDepthCompositionError,
    _bounded_chain_layers,
    _planning_inputs,
    _unique_match_or_error,
    run_adaptive_depth_composition,
)


def test_fresh_resolver_selects_shallowest_unique_behavior_depth_under_budget(
    runtime_repo: Path,
) -> None:
    report = run_adaptive_depth_composition(
        runtime_repo,
        "adaptive-depth-composition-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "adaptive-depth-composition-v1"
    assert report["caller_supplied_candidate_ids"] is False
    assert report["planner_selected_by_semantic_role_name"] is False
    assert report["eligible_behavior_target_count"] >= 1
    assert set(report["eligible_behavior_target_depths"]).issubset({2, 3})
    assert 2 <= report["selected_chain_depth"] <= 3
    assert report["selected_chain_depth"] == report["hidden_observed_depth"]
    assert report["selected_candidate_ids"] == report["hidden_observed_chain"]
    assert report["shortest_type_chain_depth"] < report["selected_chain_depth"]
    assert report["shorter_type_correct_route_depth"] < report["selected_chain_depth"]
    assert report["shorter_routes_rejected_by_behavior"] is True
    assert report["selected_distinct_stage_signature_count"] >= 2

    assert report["adaptive_max_depth"] == 4
    assert report["selected_chain_depth"] < report["adaptive_max_depth"]
    assert report["adaptive_depths_attempted"] == list(
        range(1, report["selected_chain_depth"] + 1)
    )
    assert report["adaptive_depth_attempts"][-1]["behavior_matching_count"] == 1
    assert all(
        item["behavior_matching_count"] == 0
        for item in report["adaptive_depth_attempts"][:-1]
    )
    assert report["adaptive_stopped_at_first_unique_behavior_depth"] is True
    assert report["deeper_depths_not_searched"] == list(
        range(report["selected_chain_depth"] + 1, report["adaptive_max_depth"] + 1)
    )
    assert report["adaptive_search_nodes_visited"] <= report["adaptive_search_node_budget"]
    assert (
        report["adaptive_behavior_evaluations"]
        <= report["adaptive_behavior_evaluation_budget"]
    )
    assert report["target_search_nodes_visited"] <= report["target_search_node_budget"]

    assert report["ambiguity_policy"] == "fail_closed_at_minimal_matching_depth"
    assert report["unsupported_domain_policy"] == "fail_closed"
    assert report["search_budget_policy"] == "fail_closed"
    assert report["challenge_generated_after_selection"] is True
    assert report["challenge_case_count"] == 2
    assert report["fresh_engine_runs_unique"] is True
    assert len(set(report["fresh_engine_runs"])) == 2
    assert all(item["output"] == item["expected"] for item in report["challenge_outputs"])
    assert report["fresh_candidate_state_unchanged"] is True
    assert report["fresh_trial_state_unchanged"] is True
    assert report["source_candidate_state_unchanged"] is True
    assert report["source_trial_state_unchanged"] is True
    assert report["prior_runs_copied"] is False
    assert report["prior_episodes_copied"] is False
    assert report["prior_evidence_copied"] is False
    assert report["live_model_invocation_required"] is False

    evidence_dir = runtime_repo / ".continual" / "evidence" / "adaptive-depth-composition"
    files = list(evidence_dir.glob("composition-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["digest"] == report["digest"]
    assert "do not establish independent production" in persisted["claim_boundary"]


def test_ambiguous_minimal_behavior_fails_closed() -> None:
    first = ({"candidate_id": "candidate-a"},)
    second = ({"candidate_id": "candidate-b"},)
    with pytest.raises(AdaptiveDepthCompositionError, match="ambiguous behavior"):
        _unique_match_or_error((first, second), depth=1)


def test_bounded_chain_generation_fails_closed_before_search_explosion() -> None:
    items = (
        {"candidate_id": "a", "input_domain": "numeric", "output_domain": "numeric"},
        {"candidate_id": "b", "input_domain": "numeric", "output_domain": "numeric"},
        {"candidate_id": "c", "input_domain": "numeric", "output_domain": "numeric"},
    )
    with pytest.raises(AdaptiveDepthCompositionError, match="max_search_nodes"):
        _bounded_chain_layers(
            items,
            start_domains={"numeric"},
            max_depth=3,
            max_search_nodes=1,
        )


def test_unsupported_observed_domain_fails_closed() -> None:
    with pytest.raises(AdaptiveDepthCompositionError, match="unsupported planning input domain"):
        _planning_inputs("object", "adaptive-depth-unsupported-domain")
