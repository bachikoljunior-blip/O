from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from agi.acquired_programs import ProgramExample
from agi.bounded_verified_macro_search import select_unique_minimal_verified_macro_chain
from agi.generated_disambiguation_probes import generate_bounded_probe_inputs
from agi.heterogeneous_retention_campaign import _digest
from agi.materialized_runtime_replay import _candidate_trial_snapshot
from agi.multiround_disambiguation_bank import (
    MultiRoundDisambiguationError,
    _bank_path,
    _support_payload,
    append_counterexample_to_bank,
    bank_examples,
    load_counterexample_bank,
)
from agi.partial_route_probe_disambiguation import (
    PartialRouteProbeError,
    propose_probe_with_partial_route_outcomes,
)
from agi.verified_macro_active_disambiguation import minimal_matching_verified_macro_chains


TRANSFER_KIND = "related-verified-macro-memory-transfer-v1"
_CONTRACT_KEYS = {"family", "semantic_contract", "version"}
_SOURCE_KEYS = {
    "task_key",
    "input_domain",
    "output_domain",
    "support",
    "transfer_contract",
}


class CrossContextMemoryTransferError(RuntimeError):
    pass


Oracle = Callable[[Any], Any]


def _canonical(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CrossContextMemoryTransferError(
            "cross-context memory values must be finite JSON-compatible data"
        ) from exc


def _normalize_contract(
    raw: Mapping[str, Any],
    *,
    input_domain: str,
    output_domain: str,
) -> tuple[dict[str, Any], str]:
    if not isinstance(raw, Mapping):
        raise ValueError("transfer_contract must be an object")
    if set(raw) != _CONTRACT_KEYS:
        raise ValueError(
            "transfer_contract must contain exactly family, semantic_contract, and version"
        )
    family = raw.get("family")
    semantic_contract = raw.get("semantic_contract")
    version = raw.get("version")
    if not isinstance(family, str) or not family.strip():
        raise ValueError("transfer_contract.family must be non-empty")
    if not isinstance(semantic_contract, str) or not semantic_contract.strip():
        raise ValueError("transfer_contract.semantic_contract must be non-empty")
    if not isinstance(version, int) or isinstance(version, bool) or not 1 <= version <= 1_000:
        raise ValueError("transfer_contract.version must be an integer in [1, 1000]")
    payload = {
        "kind": TRANSFER_KIND,
        "family": family.strip(),
        "semantic_contract": semantic_contract.strip(),
        "version": version,
        "input_domain": input_domain,
        "output_domain": output_domain,
    }
    return payload, _digest(payload)


def _normalize_examples(value: Any, *, field: str) -> tuple[ProgramExample, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list or tuple of ProgramExample values")
    examples = tuple(value)
    if len(examples) < 2 or any(not isinstance(item, ProgramExample) for item in examples):
        raise ValueError(f"{field} must contain at least two ProgramExample values")
    # Canonicalize now so malformed or non-finite data fails before any durable mutation.
    for item in examples:
        _canonical(item.input)
        _canonical(item.output)
    return examples


def _matching(
    root: Path,
    *,
    candidates: Sequence[dict[str, Any]],
    input_domain: str,
    output_domain: str,
    examples: Sequence[ProgramExample],
    max_depth: int,
) -> dict[str, Any]:
    return minimal_matching_verified_macro_chains(
        root,
        candidates=candidates,
        input_domain=input_domain,
        output_domain=output_domain,
        examples=examples,
        max_depth=max_depth,
    )


def _candidate_ids(candidates: Sequence[dict[str, Any]]) -> list[str]:
    ids = [str(item["candidate_id"]) for item in candidates]
    if not ids or any(not candidate_id for candidate_id in ids) or len(ids) != len(set(ids)):
        raise CrossContextMemoryTransferError(
            "Candidates must have unique non-empty identities"
        )
    return ids


def _example_outputs(examples: Sequence[ProgramExample]) -> dict[str, str]:
    outputs: dict[str, str] = {}
    for example in examples:
        encoded_input = _canonical(example.input)
        encoded_output = _canonical(example.output)
        prior = outputs.get(encoded_input)
        if prior is not None and prior != encoded_output:
            raise CrossContextMemoryTransferError(
                "target evidence contains contradictory outputs for one input"
            )
        outputs[encoded_input] = encoded_output
    return outputs


def _normalize_source(
    raw: Mapping[str, Any],
) -> tuple[str, str, str, tuple[ProgramExample, ...], Mapping[str, Any]]:
    if not isinstance(raw, Mapping):
        raise ValueError("each related source must be an object")
    if set(raw) != _SOURCE_KEYS:
        raise ValueError(
            "each related source must contain exactly task_key, input_domain, output_domain, support, and transfer_contract"
        )
    task_key = raw.get("task_key")
    input_domain = raw.get("input_domain")
    output_domain = raw.get("output_domain")
    transfer_contract = raw.get("transfer_contract")
    if not isinstance(task_key, str) or not task_key.strip():
        raise ValueError("related source task_key must be non-empty")
    if not isinstance(input_domain, str) or not input_domain:
        raise ValueError("related source input_domain must be non-empty")
    if not isinstance(output_domain, str) or not output_domain:
        raise ValueError("related source output_domain must be non-empty")
    if not isinstance(transfer_contract, Mapping):
        raise ValueError("related source transfer_contract must be an object")
    support = _normalize_examples(raw.get("support"), field="related source support")
    return task_key.strip(), input_domain, output_domain, support, transfer_contract


def resolve_verified_macro_with_related_memory(
    root: Path,
    *,
    candidates: Sequence[dict[str, Any]],
    task_key: str,
    input_domain: str,
    output_domain: str,
    support: Sequence[ProgramExample],
    transfer_contract: Mapping[str, Any],
    related_sources: Sequence[Mapping[str, Any]],
    max_depth: int,
    max_related_sources: int = 4,
    max_transferred_examples: int = 16,
    max_rounds: int = 4,
    max_probes_per_round: int = 32,
    oracle: Oracle | None = None,
) -> dict[str, Any]:
    """Resolve a verified macro using exact memory, then explicit compatible related memory.

    Exact-context banks remain authoritative and unchanged. Related memory is never discovered by fuzzy
    task text: the caller must explicitly provide bounded source contexts whose strict family,
    semantic-contract version, and input/output domains equal the target contract. Every source bank is
    validated against its own exact task key and support digest. Each source counterexample is then
    behaviorally re-tested against the current target routes and accepted only when it monotonically
    reduces ambiguity without eliminating every verified route. Incompatible, contradictory, malformed,
    or non-informative transfer fails closed or is rejected with provenance retained.

    Candidate regression ledgers and source banks are read-only. If compatible transfer is insufficient,
    the ordinary post-commitment oracle path may learn target-specific exact-context counterexamples.
    This is an internal continual-learning primitive, not independent production or AGI evidence.
    """

    if not isinstance(task_key, str) or not task_key.strip():
        raise ValueError("task_key must be non-empty")
    if not isinstance(input_domain, str) or not input_domain:
        raise ValueError("input_domain must be non-empty")
    if not isinstance(output_domain, str) or not output_domain:
        raise ValueError("output_domain must be non-empty")
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 1 <= max_depth <= 6:
        raise ValueError("max_depth must be an integer in [1, 6]")
    if (
        not isinstance(max_related_sources, int)
        or isinstance(max_related_sources, bool)
        or not 0 <= max_related_sources <= 8
    ):
        raise ValueError("max_related_sources must be an integer in [0, 8]")
    if (
        not isinstance(max_transferred_examples, int)
        or isinstance(max_transferred_examples, bool)
        or not 0 <= max_transferred_examples <= 32
    ):
        raise ValueError("max_transferred_examples must be an integer in [0, 32]")
    if not isinstance(max_rounds, int) or isinstance(max_rounds, bool) or not 0 <= max_rounds <= 16:
        raise ValueError("max_rounds must be an integer in [0, 16]")
    if (
        not isinstance(max_probes_per_round, int)
        or isinstance(max_probes_per_round, bool)
        or not 1 <= max_probes_per_round <= 64
    ):
        raise ValueError("max_probes_per_round must be an integer in [1, 64]")
    if not isinstance(related_sources, (list, tuple)):
        raise ValueError("related_sources must be a list or tuple")
    if len(related_sources) > max_related_sources:
        raise ValueError("related_sources exceeds max_related_sources")

    root = root.resolve()
    normalized_candidates = tuple(candidates)
    normalized_support = _normalize_examples(tuple(support), field="support")
    normalized_task_key = task_key.strip()
    target_contract, target_contract_sha256 = _normalize_contract(
        transfer_contract,
        input_domain=input_domain,
        output_domain=output_domain,
    )
    target_support_sha256 = _digest(_support_payload(normalized_support))

    candidate_ids = _candidate_ids(normalized_candidates)
    trials_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in candidate_ids
    }
    if not all(trials_before.values()):
        raise CrossContextMemoryTransferError(
            "all Candidates must have persisted regression evidence before transfer"
        )

    initial = _matching(
        root,
        candidates=normalized_candidates,
        input_domain=input_domain,
        output_domain=output_domain,
        examples=normalized_support,
        max_depth=max_depth,
    )
    initial_route_count = len(initial["matching_chains"])
    if initial_route_count < 1:
        raise CrossContextMemoryTransferError(
            "target support has no matching verified macro route"
        )

    try:
        exact_memory = bank_examples(
            root,
            task_key=normalized_task_key,
            input_domain=input_domain,
            output_domain=output_domain,
            support=normalized_support,
        )
    except MultiRoundDisambiguationError as exc:
        if "does not exist" not in str(exc):
            raise CrossContextMemoryTransferError(str(exc)) from exc
        exact_memory = ()

    learned_examples: list[ProgramExample] = [*normalized_support, *exact_memory]
    exact_route_count = len(
        _matching(
            root,
            candidates=normalized_candidates,
            input_domain=input_domain,
            output_domain=output_domain,
            examples=learned_examples,
            max_depth=max_depth,
        )["matching_chains"]
    )
    if exact_route_count < 1:
        raise CrossContextMemoryTransferError(
            "exact-context memory eliminated every verified target route"
        )

    source_task_keys: set[str] = set()
    source_snapshots: dict[Path, bytes] = {}
    source_records: list[dict[str, Any]] = []
    rejected_sources: list[dict[str, Any]] = []
    accepted_transfers: list[dict[str, Any]] = []
    rejected_examples: list[dict[str, Any]] = []
    related_route_counts = [exact_route_count]
    compatible_source_count = 0

    for source_index, raw_source in enumerate(related_sources):
        (
            source_task_key,
            source_input_domain,
            source_output_domain,
            source_support,
            source_contract_raw,
        ) = _normalize_source(raw_source)
        if source_task_key in source_task_keys:
            raise ValueError("related source task_key values must be unique")
        source_task_keys.add(source_task_key)
        source_contract, source_contract_sha256 = _normalize_contract(
            source_contract_raw,
            input_domain=source_input_domain,
            output_domain=source_output_domain,
        )
        source_support_sha256 = _digest(_support_payload(source_support))

        rejection_reason: str | None = None
        if source_task_key == normalized_task_key:
            rejection_reason = "same_exact_context_must_use_exact_memory"
        elif source_input_domain != input_domain or source_output_domain != output_domain:
            rejection_reason = "domain_mismatch"
        elif source_contract_sha256 != target_contract_sha256:
            rejection_reason = "semantic_contract_mismatch"
        if rejection_reason is not None:
            rejected_sources.append(
                {
                    "source_index": source_index,
                    "source_task_key": source_task_key,
                    "source_support_sha256": source_support_sha256,
                    "source_contract_sha256": source_contract_sha256,
                    "reason": rejection_reason,
                }
            )
            continue

        compatible_source_count += 1
        source_path = _bank_path(root, source_task_key)
        if not source_path.is_file():
            raise CrossContextMemoryTransferError(
                f"compatible related source bank does not exist: {source_task_key}"
            )
        before_bytes = source_path.read_bytes()
        bank = load_counterexample_bank(
            root,
            task_key=source_task_key,
            input_domain=source_input_domain,
            output_domain=source_output_domain,
            support=source_support,
        )
        source_snapshots[source_path] = before_bytes
        source_record = {
            "source_index": source_index,
            "source_task_key": source_task_key,
            "source_support_sha256": source_support_sha256,
            "source_contract": source_contract,
            "source_contract_sha256": source_contract_sha256,
            "source_bank_sha256": bank["bank_sha256"],
            "source_entry_count": len(bank["entries"]),
            "accepted_counterexample_count": 0,
            "rejected_counterexample_count": 0,
        }

        for entry_index, entry in enumerate(bank["entries"]):
            if len(accepted_transfers) >= max_transferred_examples:
                rejected_examples.append(
                    {
                        "source_task_key": source_task_key,
                        "source_entry_sha256": entry.get("entry_sha256"),
                        "reason": "global_transfer_example_bound_reached",
                    }
                )
                source_record["rejected_counterexample_count"] += 1
                continue
            if related_route_counts[-1] <= 1:
                rejected_examples.append(
                    {
                        "source_task_key": source_task_key,
                        "source_entry_sha256": entry.get("entry_sha256"),
                        "reason": "target_already_uniquely_resolved",
                    }
                )
                source_record["rejected_counterexample_count"] += 1
                continue
            if (
                entry.get("answer_received_after_probe_commitment") is not True
                or not isinstance(entry.get("probe_commitment"), str)
                or not entry["probe_commitment"]
                or not isinstance(entry.get("answer_source"), str)
                or not entry["answer_source"]
                or not isinstance(entry.get("route_output_sha256"), list)
                or len(entry["route_output_sha256"]) < 2
                or any(not isinstance(item, str) or not item for item in entry["route_output_sha256"])
            ):
                raise CrossContextMemoryTransferError(
                    "related source counterexample lacks committed provenance"
                )

            counterexample = entry["counterexample"]
            probe = counterexample["input"]
            answer = counterexample["output"]
            current_outputs = _example_outputs(learned_examples)
            encoded_probe = _canonical(probe)
            encoded_answer = _canonical(answer)
            if encoded_probe in current_outputs:
                if current_outputs[encoded_probe] != encoded_answer:
                    raise CrossContextMemoryTransferError(
                        "related memory contradicts target evidence for the same input"
                    )
                rejected_examples.append(
                    {
                        "source_task_key": source_task_key,
                        "source_entry_sha256": entry["entry_sha256"],
                        "reason": "redundant_with_target_evidence",
                    }
                )
                source_record["rejected_counterexample_count"] += 1
                continue

            ambiguous = _matching(
                root,
                candidates=normalized_candidates,
                input_domain=input_domain,
                output_domain=output_domain,
                examples=learned_examples,
                max_depth=max_depth,
            )
            routes_before = len(ambiguous["matching_chains"])
            if routes_before <= 1:
                break
            try:
                target_proposal = propose_probe_with_partial_route_outcomes(
                    root,
                    chains=ambiguous["matching_chains"],
                    probe_inputs=(probe,),
                    existing_inputs=[item.input for item in learned_examples],
                    max_probe_inputs=1,
                )
            except PartialRouteProbeError:
                rejected_examples.append(
                    {
                        "source_task_key": source_task_key,
                        "source_entry_sha256": entry["entry_sha256"],
                        "reason": "not_behaviorally_informative_for_target_routes",
                    }
                )
                source_record["rejected_counterexample_count"] += 1
                continue

            proposed_examples = [*learned_examples, ProgramExample(probe, answer)]
            refined_count = len(
                _matching(
                    root,
                    candidates=normalized_candidates,
                    input_domain=input_domain,
                    output_domain=output_domain,
                    examples=proposed_examples,
                    max_depth=max_depth,
                )["matching_chains"]
            )
            if refined_count == 0:
                raise CrossContextMemoryTransferError(
                    "related memory eliminated every verified target route; transfer refused"
                )
            if refined_count >= routes_before:
                rejected_examples.append(
                    {
                        "source_task_key": source_task_key,
                        "source_entry_sha256": entry["entry_sha256"],
                        "reason": "answer_did_not_reduce_target_ambiguity",
                    }
                )
                source_record["rejected_counterexample_count"] += 1
                continue

            transfer_commitment = _digest(
                {
                    "kind": TRANSFER_KIND,
                    "target_task_key": normalized_task_key,
                    "target_support_sha256": target_support_sha256,
                    "target_contract_sha256": target_contract_sha256,
                    "target_candidate_ids": candidate_ids,
                    "source_task_key": source_task_key,
                    "source_support_sha256": source_support_sha256,
                    "source_bank_sha256": bank["bank_sha256"],
                    "source_entry_sha256": entry["entry_sha256"],
                    "source_probe_commitment": entry["probe_commitment"],
                    "target_probe_commitment": target_proposal["probe_commitment"],
                    "target_route_output_sha256": target_proposal["route_outcome_sha256"],
                    "answer_sha256": _digest(answer),
                    "routes_before": routes_before,
                    "routes_after": refined_count,
                }
            )
            learned_examples.append(ProgramExample(probe, answer))
            accepted_transfers.append(
                {
                    "source_task_key": source_task_key,
                    "source_bank_sha256": bank["bank_sha256"],
                    "source_entry_sha256": entry["entry_sha256"],
                    "source_entry_index": entry_index,
                    "source_probe_commitment": entry["probe_commitment"],
                    "target_probe_commitment": target_proposal["probe_commitment"],
                    "target_route_output_sha256": target_proposal["route_outcome_sha256"],
                    "routes_before": routes_before,
                    "routes_after": refined_count,
                    "transfer_commitment": transfer_commitment,
                }
            )
            source_record["accepted_counterexample_count"] += 1
            related_route_counts.append(refined_count)
        source_records.append(source_record)

    route_count_after_related_memory = related_route_counts[-1]
    oracle_query_count = 0
    generated_probe_count = 0
    new_probe_commitments: list[str] = []
    oracle_route_counts = [route_count_after_related_memory]

    while oracle_route_counts[-1] > 1:
        if oracle is None:
            reason = (
                "verified macro ambiguity remains after exact and compatible related memory; "
                "resolution failed closed without an oracle"
            )
            if compatible_source_count == 0:
                reason = (
                    "verified macro ambiguity remains because no compatible related memory source "
                    "was accepted; resolution failed closed without an oracle"
                )
            raise CrossContextMemoryTransferError(reason)
        if oracle_query_count >= max_rounds:
            raise CrossContextMemoryTransferError(
                "verified macro ambiguity exceeded bounded oracle-query rounds"
            )
        ambiguous = _matching(
            root,
            candidates=normalized_candidates,
            input_domain=input_domain,
            output_domain=output_domain,
            examples=learned_examples,
            max_depth=max_depth,
        )
        probes = generate_bounded_probe_inputs(
            input_domain,
            learned_examples,
            max_probes=max_probes_per_round,
        )
        proposal = propose_probe_with_partial_route_outcomes(
            root,
            chains=ambiguous["matching_chains"],
            probe_inputs=probes,
            existing_inputs=[item.input for item in learned_examples],
            max_probe_inputs=max_probes_per_round,
        )
        probe = proposal["probe"]
        commitment = str(proposal["probe_commitment"])
        answer = oracle(probe)
        oracle_query_count += 1
        generated_probe_count += len(probes)
        new_probe_commitments.append(commitment)
        append_counterexample_to_bank(
            root,
            task_key=normalized_task_key,
            input_domain=input_domain,
            output_domain=output_domain,
            support=normalized_support,
            round_index=len(exact_memory) + oracle_query_count,
            probe=probe,
            answer=answer,
            probe_commitment=commitment,
            route_output_sha256=proposal["route_outcome_sha256"],
            answer_source=(
                f"related-memory-resolver-oracle-round-{oracle_query_count}-after-probe-commitment"
            ),
        )
        learned_examples.append(ProgramExample(probe, answer))
        refined_count = len(
            _matching(
                root,
                candidates=normalized_candidates,
                input_domain=input_domain,
                output_domain=output_domain,
                examples=learned_examples,
                max_depth=max_depth,
            )["matching_chains"]
        )
        if refined_count == 0:
            raise CrossContextMemoryTransferError(
                "committed target oracle answer eliminated every verified route"
            )
        if refined_count >= oracle_route_counts[-1]:
            raise CrossContextMemoryTransferError(
                "committed target oracle answer did not reduce verified-macro ambiguity"
            )
        oracle_route_counts.append(refined_count)

    selected, metrics = select_unique_minimal_verified_macro_chain(
        root,
        candidates=normalized_candidates,
        input_domain=input_domain,
        output_domain=output_domain,
        examples=tuple(learned_examples),
        max_depth=max_depth,
    )

    for source_path, before_bytes in source_snapshots.items():
        if not source_path.is_file() or source_path.read_bytes() != before_bytes:
            raise CrossContextMemoryTransferError(
                "cross-context resolution changed a source counterexample bank"
            )
    trials_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in candidate_ids
    }
    if trials_after != trials_before:
        raise CrossContextMemoryTransferError(
            "cross-context resolution changed Candidate regression evidence"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "resolver_kind": TRANSFER_KIND,
        "task_key": normalized_task_key,
        "input_domain": input_domain,
        "output_domain": output_domain,
        "target_support_sha256": target_support_sha256,
        "target_contract": target_contract,
        "target_contract_sha256": target_contract_sha256,
        "candidate_ids": candidate_ids,
        "initial_matching_route_count": initial_route_count,
        "exact_counterexample_count": len(exact_memory),
        "route_count_after_exact_memory": exact_route_count,
        "related_source_count_requested": len(related_sources),
        "compatible_related_source_count": compatible_source_count,
        "related_source_records": source_records,
        "rejected_related_sources": rejected_sources,
        "accepted_related_counterexample_count": len(accepted_transfers),
        "accepted_related_counterexamples": accepted_transfers,
        "rejected_related_counterexample_count": len(rejected_examples),
        "rejected_related_counterexamples": rejected_examples,
        "route_counts_during_related_transfer": related_route_counts,
        "route_count_after_related_memory": route_count_after_related_memory,
        "oracle_available": oracle is not None,
        "oracle_query_count": oracle_query_count,
        "oracle_route_counts": oracle_route_counts,
        "generated_probe_pool_items_considered": generated_probe_count,
        "new_probe_commitments": new_probe_commitments,
        "selected_candidate_ids": [str(item["candidate_id"]) for item in selected],
        "selected_depth": metrics["selected_depth"],
        "source_bank_state_unchanged": True,
        "candidate_trial_state_unchanged": True,
        "claim_boundary": (
            "Internal bounded continual-learning transfer evidence only. Related memory sources were "
            "explicitly supplied, exact-source-bank validated, strict-contract matched, and behaviorally "
            "retested against target routes before use. Candidate trials and source banks remained "
            "unchanged. Contracts, tasks, source evidence, and any fallback oracle may still be "
            "repository-authored; this is not AGI or independent production evidence."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    return {"selected": tuple(selected), "report": report}
