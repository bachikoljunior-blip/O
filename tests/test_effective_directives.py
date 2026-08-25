from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from continual.effective_directives import (
    EffectiveDirectiveError,
    compile_effective_directives,
)
from continual.store import Store


ROOT = Path(".")


def _load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _inputs() -> tuple[dict, dict, dict, dict]:
    return (
        _load("agi/USER_INPUT_INBOX.json"),
        _load("agi/USER_DIRECTIVE_EVENTS.json"),
        _load("agi/WORK_EXECUTION_STATE.json"),
        _load("agi/WORK_STRATEGY.json"),
    )


def _compile(
    inbox: dict,
    ledger: dict,
    state: dict,
    strategy: dict,
) -> dict:
    return compile_effective_directives(
        inbox,
        ledger,
        state=state,
        strategy=strategy,
        store=Store(ROOT),
    )


def _atom(ledger: dict, atom_id: str) -> dict:
    return next(atom for atom in ledger["atoms"] if atom["atom_id"] == atom_id)


def test_current_policy_is_deterministic_and_preserves_partial_supersedes() -> None:
    inbox, ledger, state, strategy = _inputs()
    first = _compile(inbox, ledger, state, strategy)
    second = _compile(
        deepcopy(inbox),
        deepcopy(ledger),
        deepcopy(state),
        deepcopy(strategy),
    )
    assert first == second
    effective = {atom["atom_id"]: atom for atom in first["effective_atoms"]}
    superseded = {
        item["atom_id"]: item["superseded_by"]
        for item in first["superseded_atoms"]
    }
    assert effective["r14-primary-executor"]["value"] == "chatgpt_work_primary"
    assert effective["r14-completion-condition"]["value"] == (
        "user_objective_or_explicit_stop"
    )
    assert effective["r14-main-writer"]["value"] == "single_fenced_primary"
    assert effective["r14-main-writer"]["activation_status"] == "effective"
    assert effective["r14-main-writer"]["precedence"] == 14
    assert effective["r6-parallel-safety"]["value"] == (
        "isolated_non_destructive_exact_head_parallel_contribution"
    )
    assert effective["r20-negative-evidence-scope"]["value"] == (
        "name_tested_target_configuration_and_conditions_and_do_not_generalize_beyond_direct_evidence"
    )
    assert effective["r20-external-method-positive-control"]["value"] == (
        "require_positive_control_only_when_equivalent_original_conditions_are_not_already_established"
    )
    assert effective["r20-external-method-failure-attribution"]["value"] == (
        "distinguish_tested_variant_failure_adaptation_or_ablation_loss_baseline_reproduction_failure_and_untested_mechanisms"
    )
    assert effective["r24-primary-o-routing-targeting-correction"]["value"] == (
        "revision23_does_not_apply_to_primary_o_restore_pre_revision23_routing"
    )
    assert effective["r5-smartphone-operator"]["value"] == (
        "automate_repository_work_and_minimize_secret_free_user_actions"
    )
    assert effective["r24-resume-frozen-revision22-execute"]["value"] == (
        "resume_frozen_revision22_context_kernel_action_adherence_execute_without_revision23_detour"
    )
    assert "r24-revision5-constraint-authority" in effective
    assert "r24-recovery-context-targeting" in effective
    assert "r23-least-work-routing" not in effective
    assert "r23-retain-smartphone-and-account-constraints" not in effective
    assert "r23-preserve-frozen-work-contracts" not in effective
    assert "r4-input-provenance" in effective
    assert superseded == {
        "r13-primary-executor": "r14-primary-executor",
        "r4-completion-condition": "r14-completion-condition",
        "r4-parallel-mode": "r6-parallel-mode",
        "r4-primary-executor": "r6-primary-executor",
        "r6-parallel-mode": "r14-parallel-mode",
        "r6-primary-executor": "r13-primary-executor",
        "r19-external-method-positive-control": "r20-external-method-positive-control",
        "r19-external-method-failure-attribution": "r20-external-method-failure-attribution",
        "r21-external-research-feed-boundary-poll": "r22-clean-g1-research-feed-boundary-poll",
        "r21-external-research-feed-ingestion": "r22-clean-g1-research-feed-ingestion",
        "r21-external-research-feed-cursor": "r22-clean-g1-research-feed-cursor",
        "r21-external-research-feed-work-volume": "r22-clean-g1-research-feed-work-volume",
        "r21-external-research-feed-failure-policy": "r22-clean-g1-research-feed-failure-policy",
        "r23-least-work-routing": "r24-primary-o-routing-targeting-correction",
        "r23-retain-smartphone-and-account-constraints": "r24-revision5-constraint-authority",
        "r23-preserve-frozen-work-contracts": "r24-resume-frozen-revision22-execute",
    }
    rendered = json.dumps(first, ensure_ascii=False)
    assert "strict independent external production evidence gate" not in rendered
    assert first["source_content_digest"] == ledger["source"]["content_digest"]
    assert all(
        atom["activation_status"] == "superseded"
        for atom in first["superseded_atoms"]
    )


def test_compiler_rejects_source_and_atom_identity_tampering() -> None:
    inbox, ledger, state, strategy = _inputs()

    bad_source = deepcopy(ledger)
    bad_source["source"]["content_digest"] = "0" * 64
    with pytest.raises(EffectiveDirectiveError, match="source digest mismatch"):
        _compile(inbox, bad_source, state, strategy)

    unknown_entry = deepcopy(ledger)
    _atom(unknown_entry, "r15-context-management")["source_entry_id"] = "missing"
    with pytest.raises(EffectiveDirectiveError, match="unknown active source entry"):
        _compile(inbox, unknown_entry, state, strategy)

    bad_entry_digest = deepcopy(ledger)
    _atom(bad_entry_digest, "r15-context-management")["source_entry_digest"] = (
        "0" * 64
    )
    with pytest.raises(EffectiveDirectiveError, match="entry digest mismatch"):
        _compile(inbox, bad_entry_digest, state, strategy)

    duplicate = deepcopy(ledger)
    duplicate["atoms"].append(deepcopy(duplicate["atoms"][0]))
    with pytest.raises(EffectiveDirectiveError, match="duplicate directive atom id"):
        _compile(inbox, duplicate, state, strategy)


def test_compiler_rejects_missing_unknown_and_cyclic_supersedes() -> None:
    inbox, ledger, state, strategy = _inputs()

    missing_coverage = deepcopy(ledger)
    missing_coverage["atoms"] = [
        atom
        for atom in missing_coverage["atoms"]
        if atom["atom_id"] != "r15-context-management"
    ]
    with pytest.raises(EffectiveDirectiveError, match="not atomized"):
        _compile(inbox, missing_coverage, state, strategy)

    erase_unaffected_mixed_entry_constraint = deepcopy(ledger)
    erase_unaffected_mixed_entry_constraint["atoms"] = [
        atom
        for atom in erase_unaffected_mixed_entry_constraint["atoms"]
        if atom["atom_id"] != "r4-completion-condition"
    ]
    with pytest.raises(
        EffectiveDirectiveError,
        match=r"user-direction-gate-not-user-set-claude-primary-20260822-v4\[1\]",
    ):
        _compile(
            inbox,
            erase_unaffected_mixed_entry_constraint,
            state,
            strategy,
        )

    unknown_target = deepcopy(ledger)
    _atom(unknown_target, "r14-primary-executor")["supersedes"] = ["missing"]
    with pytest.raises(EffectiveDirectiveError, match="supersedes unknown atom"):
        _compile(inbox, unknown_target, state, strategy)

    non_increasing = deepcopy(ledger)
    _atom(non_increasing, "r7-context-conditioning-observation")["supersedes"] = [
        "r8-context-freshness-observation"
    ]
    with pytest.raises(EffectiveDirectiveError, match="precedence must increase"):
        _compile(inbox, non_increasing, state, strategy)

    cyclic = deepcopy(ledger)
    _atom(cyclic, "r4-primary-executor")["supersedes"] = ["r6-primary-executor"]
    with pytest.raises(EffectiveDirectiveError, match="supersede cycle"):
        _compile(inbox, cyclic, state, strategy)


def test_compiler_rejects_exclusive_conflict_and_runtime_contradiction() -> None:
    inbox, ledger, state, strategy = _inputs()
    conflict = deepcopy(ledger)
    extra = deepcopy(_atom(conflict, "r15-context-authority"))
    extra["atom_id"] = "r15-competing-context-authority"
    extra["value"] = "outer session"
    conflict["atoms"].append(extra)
    with pytest.raises(EffectiveDirectiveError, match="conflicting active values"):
        _compile(inbox, conflict, state, strategy)

    bad_state = deepcopy(state)
    bad_state["owner_kind"] = "unknown_writer"
    with pytest.raises(EffectiveDirectiveError, match="state.owner_kind"):
        _compile(inbox, ledger, bad_state, strategy)

    bad_strategy = deepcopy(strategy)
    bad_strategy["execution_rules"]["validated_execution_results_destination"] = (
        "other"
    )
    with pytest.raises(
        EffectiveDirectiveError,
        match="publication policy contradicts strategy",
    ):
        _compile(inbox, ledger, state, bad_strategy)
