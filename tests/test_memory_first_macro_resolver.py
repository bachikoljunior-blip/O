from __future__ import annotations

from pathlib import Path

import pytest

from agi.memory_first_macro_resolver import (
    MemoryFirstMacroResolutionError,
    resolve_verified_macro_with_memory,
)
from agi.multiround_disambiguation_bank import (
    _base_support,
    _fresh_challenge,
    _promoted_candidates,
    _target_answer,
)


def test_reusable_resolver_learns_two_counterexamples_then_later_uses_memory_without_oracle(
    runtime_repo: Path,
) -> None:
    task_key = "generic-memory-first-has-a-v1"
    support = _base_support()
    first_candidates = _promoted_candidates(runtime_repo, "resolver-first")

    first = resolve_verified_macro_with_memory(
        runtime_repo,
        candidates=first_candidates,
        task_key=task_key,
        input_domain="object",
        output_domain="boolean",
        support=support,
        max_depth=1,
        max_rounds=3,
        oracle=_target_answer,
    )
    first_report = first["report"]
    assert first_report["passed"] is True
    assert first_report["resolver_kind"] == "memory-first-verified-macro-resolver-v1"
    assert first_report["initial_matching_route_count"] == 3
    assert first_report["remembered_counterexample_count"] == 0
    assert first_report["route_count_after_memory"] == 3
    assert first_report["route_counts_during_new_learning"] == [3, 2, 1]
    assert first_report["oracle_query_count"] == 2
    assert len(first_report["new_probe_commitments"]) == 2
    assert len(first_report["selected_candidate_ids"]) == 1
    assert first_report["selected_depth"] == 1
    assert first_report["candidate_trial_state_unchanged"] is True
    assert _fresh_challenge(runtime_repo, first["selected"]) == [False, True, False, True, False]

    later_candidates = _promoted_candidates(runtime_repo, "resolver-later")
    assert set(first_report["candidate_ids"]).isdisjoint(
        {str(item["candidate_id"]) for item in later_candidates}
    )

    def forbidden_oracle(_value: object) -> object:
        raise AssertionError("durable memory should eliminate later oracle calls")

    later = resolve_verified_macro_with_memory(
        runtime_repo,
        candidates=later_candidates,
        task_key=task_key,
        input_domain="object",
        output_domain="boolean",
        support=support,
        max_depth=1,
        max_rounds=3,
        oracle=forbidden_oracle,
    )
    later_report = later["report"]
    assert later_report["initial_matching_route_count"] == 3
    assert later_report["remembered_counterexample_count"] == 2
    assert later_report["route_count_after_memory"] == 1
    assert later_report["route_counts_during_new_learning"] == [1]
    assert later_report["oracle_query_count"] == 0
    assert later_report["new_probe_commitments"] == []
    assert len(later_report["selected_candidate_ids"]) == 1
    assert later_report["selected_depth"] == 1
    assert later_report["candidate_trial_state_unchanged"] is True
    assert _fresh_challenge(runtime_repo, later["selected"]) == [False, True, False, True, False]


def test_resolver_fails_closed_without_memory_or_oracle(runtime_repo: Path) -> None:
    candidates = _promoted_candidates(runtime_repo, "resolver-no-oracle")
    with pytest.raises(
        MemoryFirstMacroResolutionError,
        match="ambiguity remains after durable memory and no oracle is available",
    ):
        resolve_verified_macro_with_memory(
            runtime_repo,
            candidates=candidates,
            task_key="no-memory-no-oracle-v1",
            input_domain="object",
            output_domain="boolean",
            support=_base_support(),
            max_depth=1,
            max_rounds=3,
            oracle=None,
        )
