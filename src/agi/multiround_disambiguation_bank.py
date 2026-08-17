from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample
from agi.bounded_verified_macro_search import select_unique_minimal_verified_macro_chain
from agi.generated_disambiguation_probes import propose_generated_disambiguating_probe
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.materialized_runtime_replay import _candidate_trial_snapshot
from agi.verified_macro_active_disambiguation import minimal_matching_verified_macro_chains
from agi.verified_macro_synthesis import _load_verified_macro_items


BANK_KIND = "verified-macro-counterexample-bank-v1"


class MultiRoundDisambiguationError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _support_payload(examples: Sequence[ProgramExample]) -> list[dict[str, Any]]:
    return [{"input": item.input, "output": item.output} for item in examples]


def _bank_path(root: Path, task_key: str) -> Path:
    token = _digest({"kind": BANK_KIND, "task_key": task_key})[:24]
    return (
        root.resolve()
        / ".continual"
        / "evidence"
        / "verified-macro-counterexample-bank"
        / f"bank-{token}.json"
    )


def _bank_without_digest(bank: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in bank.items() if key != "bank_sha256"}


def _validate_bank(
    bank: Mapping[str, Any],
    *,
    task_key: str,
    input_domain: str,
    output_domain: str,
    support_sha256: str,
) -> dict[str, Any]:
    value = dict(bank)
    digest = value.get("bank_sha256")
    if not isinstance(digest, str) or digest != _digest(_bank_without_digest(value)):
        raise MultiRoundDisambiguationError("counterexample bank digest mismatch")
    if value.get("schema_version") != 1 or value.get("kind") != BANK_KIND:
        raise MultiRoundDisambiguationError("unsupported counterexample bank schema")
    if value.get("task_key") != task_key:
        raise MultiRoundDisambiguationError("counterexample bank task mismatch")
    if value.get("input_domain") != input_domain or value.get("output_domain") != output_domain:
        raise MultiRoundDisambiguationError("counterexample bank domain mismatch")
    if value.get("support_sha256") != support_sha256:
        raise MultiRoundDisambiguationError("counterexample bank support mismatch")
    entries = value.get("entries")
    if not isinstance(entries, list):
        raise MultiRoundDisambiguationError("counterexample bank entries must be a list")
    seen_inputs: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise MultiRoundDisambiguationError("counterexample bank entry must be an object")
        entry_value = dict(entry)
        entry_digest = entry_value.pop("entry_sha256", None)
        if not isinstance(entry_digest, str) or entry_digest != _digest(entry_value):
            raise MultiRoundDisambiguationError("counterexample bank entry digest mismatch")
        counterexample = entry.get("counterexample")
        if not isinstance(counterexample, Mapping) or "input" not in counterexample or "output" not in counterexample:
            raise MultiRoundDisambiguationError("counterexample bank entry is malformed")
        encoded_input = _canonical(counterexample["input"])
        answer_digest = _digest(counterexample["output"])
        if encoded_input in seen_inputs and seen_inputs[encoded_input] != answer_digest:
            raise MultiRoundDisambiguationError("counterexample bank contains contradictory answers")
        seen_inputs[encoded_input] = answer_digest
    return value


def load_counterexample_bank(
    root: Path,
    *,
    task_key: str,
    input_domain: str,
    output_domain: str,
    support: Sequence[ProgramExample],
) -> dict[str, Any]:
    path = _bank_path(root, task_key)
    if not path.is_file():
        raise MultiRoundDisambiguationError("counterexample bank does not exist")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise MultiRoundDisambiguationError("counterexample bank must be an object")
    return _validate_bank(
        raw,
        task_key=task_key,
        input_domain=input_domain,
        output_domain=output_domain,
        support_sha256=_digest(_support_payload(tuple(support))),
    )


def append_counterexample_to_bank(
    root: Path,
    *,
    task_key: str,
    input_domain: str,
    output_domain: str,
    support: Sequence[ProgramExample],
    round_index: int,
    probe: Any,
    answer: Any,
    probe_commitment: str,
    route_output_sha256: Sequence[str],
    answer_source: str,
) -> dict[str, Any]:
    """Append committed disambiguation evidence without replacing earlier counterexamples."""

    if not isinstance(task_key, str) or not task_key.strip():
        raise ValueError("task_key must be non-empty")
    if not isinstance(round_index, int) or isinstance(round_index, bool) or round_index < 1:
        raise ValueError("round_index must be a positive integer")
    if not isinstance(probe_commitment, str) or not probe_commitment:
        raise ValueError("probe_commitment must be non-empty")
    if not isinstance(answer_source, str) or not answer_source:
        raise ValueError("answer_source must be non-empty")
    normalized_support = tuple(support)
    if len(normalized_support) < 2:
        raise ValueError("at least two base support examples are required")
    support_payload = _support_payload(normalized_support)
    support_sha256 = _digest(support_payload)
    existing_inputs = {_canonical(item.input) for item in normalized_support}
    encoded_probe = _canonical(probe)
    if encoded_probe in existing_inputs:
        raise MultiRoundDisambiguationError("counterexample probe overlaps base support")

    path = _bank_path(root, task_key)
    if path.is_file():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise MultiRoundDisambiguationError("counterexample bank must be an object")
        bank = _validate_bank(
            raw,
            task_key=task_key,
            input_domain=input_domain,
            output_domain=output_domain,
            support_sha256=support_sha256,
        )
    else:
        bank = {
            "schema_version": 1,
            "kind": BANK_KIND,
            "task_key": task_key,
            "input_domain": input_domain,
            "output_domain": output_domain,
            "support_sha256": support_sha256,
            "support_example_count": len(normalized_support),
            "entries": [],
            "candidate_trial_state_mutated": False,
            "claim_boundary": (
                "Append-only repository-authored task evidence only. Bank entries may reduce repeated "
                "oracle queries for the same exact support context, but do not promote Candidates or "
                "satisfy independent production AGI evidence requirements."
            ),
        }

    entry_core = {
        "round_index": round_index,
        "counterexample": {"input": probe, "output": answer},
        "probe_commitment": probe_commitment,
        "route_output_sha256": list(route_output_sha256),
        "answer_source": answer_source,
        "answer_received_after_probe_commitment": True,
    }
    entry = {**entry_core, "entry_sha256": _digest(entry_core)}
    entries = list(bank["entries"])
    for existing in entries:
        existing_counterexample = existing["counterexample"]
        if _canonical(existing_counterexample["input"]) != encoded_probe:
            continue
        if existing_counterexample["output"] != answer:
            raise MultiRoundDisambiguationError(
                "contradictory answer for remembered probe; append refused"
            )
        if existing.get("entry_sha256") == entry["entry_sha256"]:
            return {**bank, "append_outcome": "idempotent_existing"}
        raise MultiRoundDisambiguationError(
            "same remembered probe has different provenance; append refused"
        )
    entries.append(entry)
    bank["entries"] = entries
    bank["bank_sha256"] = _digest(_bank_without_digest(bank))
    _atomic_json(path, bank)
    return {**bank, "append_outcome": "appended"}


def bank_examples(
    root: Path,
    *,
    task_key: str,
    input_domain: str,
    output_domain: str,
    support: Sequence[ProgramExample],
) -> tuple[ProgramExample, ...]:
    bank = load_counterexample_bank(
        root,
        task_key=task_key,
        input_domain=input_domain,
        output_domain=output_domain,
        support=support,
    )
    return tuple(
        ProgramExample(entry["counterexample"]["input"], entry["counterexample"]["output"])
        for entry in bank["entries"]
    )


def _has_key_spec(key: str) -> dict[str, Any]:
    other = "b" if key == "a" else "a"
    return {
        "name": f"multiround-has-{key}",
        "input_domain": "object",
        "output_domain": "boolean",
        "examples": (
            ProgramExample({key: 1}, True),
            ProgramExample({key: 0, other: 1}, True),
            ProgramExample({other: 1}, False),
            ProgramExample({}, False),
        ),
        "probe": {key: False, "novel": 1},
        "expected": True,
        "max_nodes": 3,
    }


def _has_either_spec() -> dict[str, Any]:
    return {
        "name": "multiround-has-either",
        "input_domain": "object",
        "output_domain": "boolean",
        "examples": (
            ProgramExample({"a": 1}, True),
            ProgramExample({"b": 1}, True),
            ProgramExample({}, False),
            ProgramExample({"c": 1}, False),
            ProgramExample({"a": 0, "b": 0}, True),
        ),
        "probe": {"b": False, "novel": 1},
        "expected": True,
        "max_nodes": 5,
    }


def _promoted_candidates(root: Path, seed: str) -> tuple[dict[str, Any], ...]:
    promoted = (
        _promote_task(root, seed=f"{seed}:has-a", spec=_has_key_spec("a")),
        _promote_task(root, seed=f"{seed}:has-b", spec=_has_key_spec("b")),
        _promote_task(root, seed=f"{seed}:has-either", spec=_has_either_spec()),
    )
    ids = [str(item["candidate_id"]) for item in promoted]
    if len(set(ids)) != 3:
        raise MultiRoundDisambiguationError("multi-round campaign reused Candidate identity")
    return _load_verified_macro_items(root, ids)


def _base_support() -> tuple[ProgramExample, ...]:
    return (
        ProgramExample({"a": 1, "b": 1}, True),
        ProgramExample({"a": 0, "b": 2}, True),
        ProgramExample({"a": False, "b": False}, True),
    )


def _target_answer(value: Any) -> bool:
    return isinstance(value, dict) and "a" in value


def _matching(
    root: Path,
    candidates: Sequence[dict[str, Any]],
    examples: Sequence[ProgramExample],
) -> dict[str, Any]:
    return minimal_matching_verified_macro_chains(
        root,
        candidates=candidates,
        input_domain="object",
        output_domain="boolean",
        examples=examples,
        max_depth=1,
    )


def _fresh_challenge(root: Path, selected: Sequence[dict[str, Any]]) -> list[bool]:
    from agi.behavior_guided_tool_chain_discovery import _execute_chain

    values = ({}, {"a": 1}, {"b": 1}, {"a": False, "z": 1}, {"z": 1})
    outputs: list[bool] = []
    for value in values:
        output, _run_id, _refs = _execute_chain(root, selected, value)
        expected = _target_answer(value)
        if output != expected:
            raise MultiRoundDisambiguationError("selected multi-round route failed fresh challenge")
        outputs.append(bool(output))
    return outputs


def run_multiround_disambiguation_bank_campaign(root: Path, seed: str) -> dict[str, Any]:
    """Accumulate two active-learning counterexamples and reuse both in a later fresh campaign.

    Three exact-scope verified object→boolean Candidates agree on the initial support: has-key-a,
    has-key-b, and has-a-or-b. No single target answer to the first useful probe uniquely identifies
    has-key-a. The learner therefore commits a probe, receives one answer, refines the route set, commits
    a second probe, receives a second answer, and appends both experiences to a durable bank. A later
    campaign uses disjoint fresh Candidate identities plus both remembered counterexamples and reaches
    the target with zero new oracle queries. Candidate trial ledgers remain unchanged throughout.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    task_key = "object-has-key-a-multiround-v1"
    support = _base_support()

    first_candidates = _promoted_candidates(root, f"{seed}:first")
    first_ids = [str(item["candidate_id"]) for item in first_candidates]
    first_trials_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id) for candidate_id in first_ids
    }
    if not all(first_trials_before.values()):
        raise MultiRoundDisambiguationError("first campaign Candidate lacks regression evidence")

    learned_examples: list[ProgramExample] = list(support)
    first_initial = _matching(root, first_candidates, learned_examples)
    if len(first_initial["matching_chains"]) != 3:
        raise MultiRoundDisambiguationError("expected three initially ambiguous routes")

    oracle_queries = 0
    round_route_counts = [len(first_initial["matching_chains"])]
    probe_inputs: list[Any] = []
    commitments: list[str] = []
    while True:
        ambiguous = _matching(root, first_candidates, learned_examples)
        matching = ambiguous["matching_chains"]
        if len(matching) == 1:
            break
        if len(probe_inputs) >= 3:
            raise MultiRoundDisambiguationError("multi-round disambiguation exceeded round bound")
        proposal = propose_generated_disambiguating_probe(
            root,
            chains=matching,
            input_domain="object",
            examples=learned_examples,
            max_probes=32,
        )
        probe = proposal["probe"]
        if any(_canonical(probe) == _canonical(item) for item in probe_inputs):
            raise MultiRoundDisambiguationError("active learner repeated a probe")
        answer = _target_answer(probe)
        oracle_queries += 1
        probe_inputs.append(probe)
        commitments.append(str(proposal["probe_commitment"]))
        append_counterexample_to_bank(
            root,
            task_key=task_key,
            input_domain="object",
            output_domain="boolean",
            support=support,
            round_index=oracle_queries,
            probe=probe,
            answer=answer,
            probe_commitment=str(proposal["probe_commitment"]),
            route_output_sha256=proposal["route_output_sha256"],
            answer_source=f"frozen-has-a-oracle-round-{oracle_queries}-after-probe-commitment",
        )
        learned_examples.append(ProgramExample(probe, answer))
        round_route_counts.append(len(_matching(root, first_candidates, learned_examples)["matching_chains"]))

    if oracle_queries != 2:
        raise MultiRoundDisambiguationError("campaign did not require exactly two oracle queries")
    if round_route_counts != [3, 2, 1]:
        raise MultiRoundDisambiguationError("route ambiguity did not shrink 3→2→1")
    first_selected, first_metrics = select_unique_minimal_verified_macro_chain(
        root,
        candidates=first_candidates,
        input_domain="object",
        output_domain="boolean",
        examples=tuple(learned_examples),
        max_depth=1,
    )
    first_challenge = _fresh_challenge(root, first_selected)
    first_trials_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id) for candidate_id in first_ids
    }
    if first_trials_after != first_trials_before:
        raise MultiRoundDisambiguationError("first multi-round campaign changed Candidate trial evidence")

    bank = load_counterexample_bank(
        root,
        task_key=task_key,
        input_domain="object",
        output_domain="boolean",
        support=support,
    )
    if len(bank["entries"]) != 2:
        raise MultiRoundDisambiguationError("durable counterexample bank did not retain both rounds")
    bank_bytes_before_reuse = _bank_path(root, task_key).read_bytes()

    later_candidates = _promoted_candidates(root, f"{seed}:later")
    later_ids = [str(item["candidate_id"]) for item in later_candidates]
    if set(first_ids) & set(later_ids):
        raise MultiRoundDisambiguationError("later campaign did not use fresh Candidate identities")
    later_trials_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id) for candidate_id in later_ids
    }
    if not all(later_trials_before.values()):
        raise MultiRoundDisambiguationError("later campaign Candidate lacks regression evidence")
    later_initial = _matching(root, later_candidates, support)
    if len(later_initial["matching_chains"]) != 3:
        raise MultiRoundDisambiguationError("later campaign did not reproduce original ambiguity")

    remembered = bank_examples(
        root,
        task_key=task_key,
        input_domain="object",
        output_domain="boolean",
        support=support,
    )
    later_oracle_queries = 0
    later_examples = (*support, *remembered)
    later_selected, later_metrics = select_unique_minimal_verified_macro_chain(
        root,
        candidates=later_candidates,
        input_domain="object",
        output_domain="boolean",
        examples=later_examples,
        max_depth=1,
    )
    if len(later_selected) != 1:
        raise MultiRoundDisambiguationError("remembered multi-round evidence remained ambiguous")
    later_challenge = _fresh_challenge(root, later_selected)
    later_trials_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id) for candidate_id in later_ids
    }
    if later_trials_after != later_trials_before:
        raise MultiRoundDisambiguationError("later memory reuse changed Candidate trial evidence")
    if _bank_path(root, task_key).read_bytes() != bank_bytes_before_reuse:
        raise MultiRoundDisambiguationError("later memory reuse mutated the counterexample bank")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "multi-round-persistent-active-disambiguation",
        "task_key": task_key,
        "first_candidate_ids": first_ids,
        "later_candidate_ids": later_ids,
        "fresh_candidate_identities": not bool(set(first_ids) & set(later_ids)),
        "initial_matching_route_count": len(first_initial["matching_chains"]),
        "round_route_counts": round_route_counts,
        "first_oracle_query_count": oracle_queries,
        "probe_count": len(probe_inputs),
        "probe_commitments": commitments,
        "bank_entry_count": len(bank["entries"]),
        "bank_sha256": bank["bank_sha256"],
        "later_initial_matching_route_count": len(later_initial["matching_chains"]),
        "later_oracle_query_count": later_oracle_queries,
        "later_reused_counterexample_count": len(remembered),
        "later_selected_depth": later_metrics["selected_depth"],
        "first_selected_depth": first_metrics["selected_depth"],
        "first_challenge_outputs": first_challenge,
        "later_challenge_outputs": later_challenge,
        "counterexample_bank_unchanged_after_reuse": _bank_path(root, task_key).read_bytes()
        == bank_bytes_before_reuse,
        "first_candidate_trial_state_unchanged": first_trials_after == first_trials_before,
        "later_candidate_trial_state_unchanged": later_trials_after == later_trials_before,
        "candidate_trial_state_unchanged": (
            first_trials_after == first_trials_before and later_trials_after == later_trials_before
        ),
        "claim_boundary": (
            "Internal bounded continual-learning and active-disambiguation evidence only. Two committed "
            "counterexamples accumulated across sequential refinement rounds and later solved the same "
            "ambiguity for fresh Candidate identities without new oracle queries. Candidate generation, "
            "oracle answers, probe generation, memory policy, and runtime remain repository-authored; "
            "this is not AGI or independent production evidence."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "multiround-disambiguation-bank"
        / f"multiround-disambiguation-{_digest(seed)[:16]}.json",
        report,
    )
    return report
