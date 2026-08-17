from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.bounded_verified_macro_search import select_unique_minimal_verified_macro_chain
from agi.generated_disambiguation_probes import generate_bounded_probe_inputs
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.materialized_runtime_replay import _candidate_trial_snapshot
from agi.partial_route_probe_disambiguation import (
    _flag_value_spec,
    _has_flag_spec,
    propose_probe_with_partial_route_outcomes,
)
from agi.verified_macro_active_disambiguation import minimal_matching_verified_macro_chains
from agi.verified_macro_relevance_pruning import relevant_verified_macro_ids
from agi.verified_macro_synthesis import _load_verified_macro_items


MEMORY_KIND = "verified-macro-counterexample-memory-v1"


class PersistentDisambiguationMemoryError(RuntimeError):
    pass


def _support_payload(examples: Sequence[ProgramExample]) -> list[dict[str, Any]]:
    return [{"input": item.input, "output": item.output} for item in examples]


def _memory_path(root: Path, task_key: str) -> Path:
    token = _digest({"kind": MEMORY_KIND, "task_key": task_key})[:24]
    return (
        root.resolve()
        / ".continual"
        / "evidence"
        / "verified-macro-disambiguation-memory"
        / f"memory-{token}.json"
    )


def persist_disambiguation_counterexample(
    root: Path,
    *,
    task_key: str,
    input_domain: str,
    output_domain: str,
    support: Sequence[ProgramExample],
    probe: Any,
    answer: Any,
    probe_commitment: str,
    route_outcome_sha256: Sequence[str],
    answer_source: str,
) -> dict[str, Any]:
    """Persist a committed counterexample separately from Candidate regression state.

    The memory records task support, the probe commitment, the later oracle answer, and route-outcome
    hashes. It is evidence/experience, not a Candidate promotion decision, so reusing it must never
    consume or rewrite Candidate trial ledgers.
    """

    if not isinstance(task_key, str) or not task_key.strip():
        raise ValueError("task_key must be non-empty")
    if not isinstance(input_domain, str) or not input_domain:
        raise ValueError("input_domain must be non-empty")
    if not isinstance(output_domain, str) or not output_domain:
        raise ValueError("output_domain must be non-empty")
    if not isinstance(probe_commitment, str) or not probe_commitment:
        raise ValueError("probe_commitment must be non-empty")
    if not isinstance(answer_source, str) or not answer_source:
        raise ValueError("answer_source must be non-empty")
    normalized_support = tuple(support)
    if len(normalized_support) < 2:
        raise ValueError("at least two support examples are required")
    support_payload = _support_payload(normalized_support)
    existing_inputs = {
        json.dumps(
            item.input,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        for item in normalized_support
    }
    encoded_probe = json.dumps(
        probe,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if encoded_probe in existing_inputs:
        raise PersistentDisambiguationMemoryError("counterexample probe overlaps original support")

    record: dict[str, Any] = {
        "schema_version": 1,
        "kind": MEMORY_KIND,
        "task_key": task_key,
        "input_domain": input_domain,
        "output_domain": output_domain,
        "support_sha256": _digest(support_payload),
        "support_example_count": len(normalized_support),
        "counterexample": {"input": probe, "output": answer},
        "probe_commitment": probe_commitment,
        "route_outcome_sha256": list(route_outcome_sha256),
        "answer_source": answer_source,
        "answer_received_after_probe_commitment": True,
        "candidate_trial_state_mutated": False,
        "claim_boundary": (
            "Durable repository-authored task evidence only. This memory can prevent a repeated oracle "
            "query on the same committed behavior problem, but it is not independent production "
            "evidence and does not itself promote or validate any future Candidate."
        ),
    }
    record["memory_sha256"] = _digest(record)
    _atomic_json(_memory_path(root, task_key), record)
    return record


def load_disambiguation_counterexample(
    root: Path,
    *,
    task_key: str,
    input_domain: str,
    output_domain: str,
    support: Sequence[ProgramExample],
) -> dict[str, Any]:
    """Load a counterexample only when its task identity and committed support match exactly."""

    path = _memory_path(root, task_key)
    if not path.is_file():
        raise PersistentDisambiguationMemoryError("no persisted disambiguation memory for task")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise PersistentDisambiguationMemoryError("disambiguation memory must be an object")
    record = dict(raw)
    memory_sha256 = record.pop("memory_sha256", None)
    if not isinstance(memory_sha256, str) or memory_sha256 != _digest(record):
        raise PersistentDisambiguationMemoryError("disambiguation memory digest mismatch")
    if record.get("schema_version") != 1 or record.get("kind") != MEMORY_KIND:
        raise PersistentDisambiguationMemoryError("unsupported disambiguation memory schema")
    if record.get("task_key") != task_key:
        raise PersistentDisambiguationMemoryError("disambiguation memory task mismatch")
    if record.get("input_domain") != input_domain or record.get("output_domain") != output_domain:
        raise PersistentDisambiguationMemoryError("disambiguation memory domain mismatch")
    expected_support_sha256 = _digest(_support_payload(tuple(support)))
    if record.get("support_sha256") != expected_support_sha256:
        raise PersistentDisambiguationMemoryError("disambiguation memory support mismatch")
    if record.get("answer_received_after_probe_commitment") is not True:
        raise PersistentDisambiguationMemoryError("memory lacks post-commitment answer provenance")
    record["memory_sha256"] = memory_sha256
    return record


def _current_campaign_candidates(root: Path, seed: str) -> tuple[dict[str, Any], dict[str, Any], tuple[dict[str, Any], ...]]:
    presence = _promote_task(root, seed=f"{seed}:presence", spec=_has_flag_spec())
    projection = _promote_task(root, seed=f"{seed}:projection", spec=_flag_value_spec())
    ids = [str(presence["candidate_id"]), str(projection["candidate_id"])]
    relevant = relevant_verified_macro_ids(
        root,
        input_domain="object",
        output_domain="boolean",
        max_depth=1,
        max_library_candidates=32,
        max_relevant_candidates=32,
    )
    relevant_ids = {str(value) for value in relevant["relevant_candidate_ids"]}
    if not set(ids).issubset(relevant_ids):
        raise PersistentDisambiguationMemoryError("fresh verified Candidates were not discoverable")
    return presence, projection, _load_verified_macro_items(root, ids)


def _initial_support() -> tuple[ProgramExample, ...]:
    return (
        ProgramExample({"flag": True}, True),
        ProgramExample({"flag": True, "x": 1}, True),
        ProgramExample({"flag": True, "y": 2}, True),
    )


def _require_two_ambiguous_routes(root: Path, candidates: Sequence[dict[str, Any]], support: Sequence[ProgramExample]) -> dict[str, Any]:
    ambiguous = minimal_matching_verified_macro_chains(
        root,
        candidates=candidates,
        input_domain="object",
        output_domain="boolean",
        examples=support,
        max_depth=1,
    )
    if len(ambiguous["matching_chains"]) != 2:
        raise PersistentDisambiguationMemoryError("campaign did not reproduce two ambiguous routes")
    return ambiguous


def _challenge_presence_route(root: Path, selected: Sequence[dict[str, Any]]) -> list[bool]:
    inputs = ({}, {"flag": False}, {"flag": True, "z": 1}, {"other": 1})
    outputs: list[bool] = []
    for value in inputs:
        output, _run_id, _refs = _execute_chain(root, selected, value)
        expected = bool("flag" in value)
        if output != expected:
            raise PersistentDisambiguationMemoryError("remembered route failed fresh object challenge")
        outputs.append(bool(output))
    return outputs


def run_persistent_disambiguation_memory_campaign(root: Path, seed: str) -> dict[str, Any]:
    """Learn one disambiguating counterexample, persist it, then reuse it in a later fresh campaign.

    The first campaign starts from behaviorally ambiguous exact-scope verified object→boolean Candidates,
    commits a generated missing-key probe, receives one frozen-oracle answer after commitment, and saves
    that counterexample as durable task evidence. A later campaign creates fresh Candidate identities and
    reproduces the same ambiguity. It then loads the prior counterexample and resolves the ambiguity with
    zero new oracle queries. Both campaigns leave Candidate trial ledgers byte-identical.

    This is internal continual-learning evidence only; the tasks, oracle, memory format, Candidates, and
    runtime are repository-authored and do not satisfy the independent production evidence gate.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    task_key = "object-has-flag-presence-v1"
    support = _initial_support()

    first_presence, first_projection, first_candidates = _current_campaign_candidates(
        root, f"{seed}:first"
    )
    first_ids = [str(first_presence["candidate_id"]), str(first_projection["candidate_id"])]
    first_trials_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id) for candidate_id in first_ids
    }
    if not all(first_trials_before.values()):
        raise PersistentDisambiguationMemoryError("first campaign Candidate lacks regression evidence")
    first_ambiguous = _require_two_ambiguous_routes(root, first_candidates, support)
    probes = generate_bounded_probe_inputs("object", support, max_probes=24)
    proposal = propose_probe_with_partial_route_outcomes(
        root,
        chains=first_ambiguous["matching_chains"],
        probe_inputs=probes,
        existing_inputs=[item.input for item in support],
        max_probe_inputs=24,
    )
    probe = proposal["probe"]
    if not isinstance(probe, dict) or "flag" in probe:
        raise PersistentDisambiguationMemoryError("first campaign probe did not expose missing-key behavior")
    if proposal["verified_rejection_count"] != 1:
        raise PersistentDisambiguationMemoryError("first campaign probe lacked one verified rejection")

    first_oracle_query_count = 1
    answer = bool("flag" in probe)
    memory = persist_disambiguation_counterexample(
        root,
        task_key=task_key,
        input_domain="object",
        output_domain="boolean",
        support=support,
        probe=probe,
        answer=answer,
        probe_commitment=str(proposal["probe_commitment"]),
        route_outcome_sha256=proposal["route_outcome_sha256"],
        answer_source="frozen-presence-oracle-after-probe-commitment",
    )
    memory_path = _memory_path(root, task_key)
    memory_bytes_before_reuse = memory_path.read_bytes()

    first_selected, _first_metrics = select_unique_minimal_verified_macro_chain(
        root,
        candidates=first_candidates,
        input_domain="object",
        output_domain="boolean",
        examples=(*support, ProgramExample(probe, answer)),
        max_depth=1,
    )
    if [str(item["candidate_id"]) for item in first_selected] != [str(first_presence["candidate_id"])]:
        raise PersistentDisambiguationMemoryError("first counterexample selected the wrong route")
    first_challenge_outputs = _challenge_presence_route(root, first_selected)
    first_trials_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id) for candidate_id in first_ids
    }
    if first_trials_after != first_trials_before:
        raise PersistentDisambiguationMemoryError("first disambiguation changed Candidate trial evidence")

    later_presence, later_projection, later_candidates = _current_campaign_candidates(
        root, f"{seed}:later"
    )
    later_ids = [str(later_presence["candidate_id"]), str(later_projection["candidate_id"])]
    if set(later_ids) & set(first_ids):
        raise PersistentDisambiguationMemoryError("later campaign did not create fresh Candidate identities")
    later_trials_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id) for candidate_id in later_ids
    }
    if not all(later_trials_before.values()):
        raise PersistentDisambiguationMemoryError("later campaign Candidate lacks regression evidence")
    later_ambiguous = _require_two_ambiguous_routes(root, later_candidates, support)

    remembered = load_disambiguation_counterexample(
        root,
        task_key=task_key,
        input_domain="object",
        output_domain="boolean",
        support=support,
    )
    remembered_example = ProgramExample(
        remembered["counterexample"]["input"],
        remembered["counterexample"]["output"],
    )
    later_probe_outcomes = propose_probe_with_partial_route_outcomes(
        root,
        chains=later_ambiguous["matching_chains"],
        probe_inputs=(remembered_example.input,),
        existing_inputs=[item.input for item in support],
        max_probe_inputs=1,
    )
    if later_probe_outcomes["distinct_route_outcome_count"] != 2:
        raise PersistentDisambiguationMemoryError("remembered probe no longer distinguishes fresh routes")
    if later_probe_outcomes["verified_rejection_count"] != 1:
        raise PersistentDisambiguationMemoryError("remembered probe lost verified rejection behavior")

    later_oracle_query_count = 0
    later_selected, later_metrics = select_unique_minimal_verified_macro_chain(
        root,
        candidates=later_candidates,
        input_domain="object",
        output_domain="boolean",
        examples=(*support, remembered_example),
        max_depth=1,
    )
    later_selected_ids = [str(item["candidate_id"]) for item in later_selected]
    if later_selected_ids != [str(later_presence["candidate_id"])]:
        raise PersistentDisambiguationMemoryError("remembered counterexample selected the wrong fresh route")
    later_challenge_outputs = _challenge_presence_route(root, later_selected)
    later_trials_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id) for candidate_id in later_ids
    }
    if later_trials_after != later_trials_before:
        raise PersistentDisambiguationMemoryError("memory reuse changed Candidate trial evidence")
    if memory_path.read_bytes() != memory_bytes_before_reuse:
        raise PersistentDisambiguationMemoryError("memory reuse mutated the persisted counterexample")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "persistent-verified-macro-disambiguation-memory",
        "task_key": task_key,
        "first_candidate_ids": first_ids,
        "later_candidate_ids": later_ids,
        "fresh_candidate_identities": not bool(set(first_ids) & set(later_ids)),
        "first_initial_matching_route_count": len(first_ambiguous["matching_chains"]),
        "later_initial_matching_route_count": len(later_ambiguous["matching_chains"]),
        "first_oracle_query_count": first_oracle_query_count,
        "later_oracle_query_count": later_oracle_query_count,
        "memory_reused_without_new_oracle_query": later_oracle_query_count == 0,
        "memory_sha256": memory["memory_sha256"],
        "memory_file_unchanged_after_reuse": memory_path.read_bytes() == memory_bytes_before_reuse,
        "remembered_probe_distinct_route_outcome_count": later_probe_outcomes[
            "distinct_route_outcome_count"
        ],
        "remembered_probe_verified_rejection_count": later_probe_outcomes[
            "verified_rejection_count"
        ],
        "later_selected_candidate_ids": later_selected_ids,
        "later_selected_depth": later_metrics["selected_depth"],
        "first_challenge_outputs": first_challenge_outputs,
        "later_challenge_outputs": later_challenge_outputs,
        "first_candidate_trial_state_unchanged": first_trials_after == first_trials_before,
        "later_candidate_trial_state_unchanged": later_trials_after == later_trials_before,
        "candidate_trial_state_unchanged": (
            first_trials_after == first_trials_before and later_trials_after == later_trials_before
        ),
        "claim_boundary": (
            "Internal continual-learning evidence only. A committed counterexample was persisted and "
            "reused to resolve the same ambiguity for fresh Candidate identities without a second oracle "
            "query, while Candidate trial ledgers remained unchanged. The task, oracle, memory policy, "
            "Candidates, and runtime remain repository-authored; this is not AGI or independent "
            "production evidence."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "persistent-disambiguation-memory"
        / f"persistent-disambiguation-{_digest(seed)[:16]}.json",
        report,
    )
    return report
