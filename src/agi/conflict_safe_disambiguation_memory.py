from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample
from agi.heterogeneous_retention_campaign import _digest
from agi.persistent_disambiguation_memory import (
    PersistentDisambiguationMemoryError,
    _initial_support,
    _memory_path,
    load_disambiguation_counterexample,
    persist_disambiguation_counterexample,
    run_persistent_disambiguation_memory_campaign,
)


CONFLICT_KIND = "verified-macro-counterexample-memory-conflict-v1"


def _conflict_path(root: Path, task_key: str, proposed: dict[str, Any]) -> Path:
    token = _digest(
        {
            "kind": CONFLICT_KIND,
            "task_key": task_key,
            "proposed": proposed,
        }
    )[:24]
    return (
        root.resolve()
        / ".continual"
        / "evidence"
        / "verified-macro-disambiguation-memory"
        / "conflicts"
        / f"conflict-{token}.json"
    )


def _proposed_evidence(
    *,
    probe: Any,
    answer: Any,
    probe_commitment: str,
    route_outcome_sha256: Sequence[str],
    answer_source: str,
) -> dict[str, Any]:
    return {
        "counterexample": {"input": probe, "output": answer},
        "probe_commitment": probe_commitment,
        "route_outcome_sha256": list(route_outcome_sha256),
        "answer_source": answer_source,
    }


def persist_disambiguation_counterexample_fail_closed(
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
    """Persist one durable counterexample without silently overwriting contradictory experience.

    An exact repeated observation is idempotent. If the same task-key memory slot already contains
    different committed evidence, the original memory remains byte-identical, a separate contradiction
    record is appended, and the write fails closed. Corrupt or context-incompatible existing memory is
    also preserved and blocks replacement rather than being repaired implicitly.
    """

    root = root.resolve()
    path = _memory_path(root, task_key)
    proposed = _proposed_evidence(
        probe=probe,
        answer=answer,
        probe_commitment=probe_commitment,
        route_outcome_sha256=route_outcome_sha256,
        answer_source=answer_source,
    )
    if not path.exists():
        created = persist_disambiguation_counterexample(
            root,
            task_key=task_key,
            input_domain=input_domain,
            output_domain=output_domain,
            support=support,
            probe=probe,
            answer=answer,
            probe_commitment=probe_commitment,
            route_outcome_sha256=route_outcome_sha256,
            answer_source=answer_source,
        )
        return {**created, "persistence_outcome": "created", "idempotent_reuse": False}

    original_bytes = path.read_bytes()
    try:
        existing = load_disambiguation_counterexample(
            root,
            task_key=task_key,
            input_domain=input_domain,
            output_domain=output_domain,
            support=support,
        )
        incompatible_reason: str | None = None
    except PersistentDisambiguationMemoryError as exc:
        existing = None
        incompatible_reason = str(exc)

    if existing is not None:
        existing_evidence = {
            "counterexample": existing.get("counterexample"),
            "probe_commitment": existing.get("probe_commitment"),
            "route_outcome_sha256": existing.get("route_outcome_sha256"),
            "answer_source": existing.get("answer_source"),
        }
        if existing_evidence == proposed:
            if path.read_bytes() != original_bytes:
                raise PersistentDisambiguationMemoryError(
                    "idempotent memory check unexpectedly changed persisted bytes"
                )
            return {
                **existing,
                "persistence_outcome": "idempotent_existing",
                "idempotent_reuse": True,
            }
        existing_memory_sha256 = str(existing["memory_sha256"])
    else:
        existing_evidence = None
        existing_memory_sha256 = _digest(
            {
                "raw_bytes_sha256": __import__("hashlib").sha256(original_bytes).hexdigest(),
                "incompatible_reason": incompatible_reason,
            }
        )

    conflict: dict[str, Any] = {
        "schema_version": 1,
        "kind": CONFLICT_KIND,
        "task_key": task_key,
        "input_domain": input_domain,
        "output_domain": output_domain,
        "existing_memory_sha256": existing_memory_sha256,
        "existing_evidence": existing_evidence,
        "proposed_evidence": proposed,
        "existing_memory_incompatible_reason": incompatible_reason,
        "original_memory_preserved": True,
        "replacement_performed": False,
        "resolution": "fail_closed_preserve_both_evidence_paths",
        "claim_boundary": (
            "Internal robustness evidence only. Contradictory or incompatible remembered evidence is "
            "retained and blocks silent replacement; this does not establish that either observation is "
            "externally correct or satisfy the independent production AGI evidence gate."
        ),
    }
    conflict["conflict_sha256"] = _digest(conflict)
    _atomic_json(_conflict_path(root, task_key, proposed), conflict)
    if path.read_bytes() != original_bytes:
        raise PersistentDisambiguationMemoryError("conflict handling mutated original memory")
    raise PersistentDisambiguationMemoryError(
        "contradictory disambiguation memory preserved; replacement refused"
    )


def run_conflict_safe_disambiguation_memory_campaign(root: Path, seed: str) -> dict[str, Any]:
    """Show idempotent reuse and fail-closed contradiction retention around durable task memory."""

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    base = run_persistent_disambiguation_memory_campaign(root, f"{seed}:base")
    task_key = str(base["task_key"])
    support = _initial_support()
    memory = load_disambiguation_counterexample(
        root,
        task_key=task_key,
        input_domain="object",
        output_domain="boolean",
        support=support,
    )
    memory_path = _memory_path(root, task_key)
    original_bytes = memory_path.read_bytes()
    counterexample = memory["counterexample"]

    repeated = persist_disambiguation_counterexample_fail_closed(
        root,
        task_key=task_key,
        input_domain="object",
        output_domain="boolean",
        support=support,
        probe=counterexample["input"],
        answer=counterexample["output"],
        probe_commitment=str(memory["probe_commitment"]),
        route_outcome_sha256=memory["route_outcome_sha256"],
        answer_source=str(memory["answer_source"]),
    )
    if repeated["persistence_outcome"] != "idempotent_existing":
        raise PersistentDisambiguationMemoryError("exact repeated evidence was not idempotent")
    if memory_path.read_bytes() != original_bytes:
        raise PersistentDisambiguationMemoryError("idempotent persistence changed original memory")

    contradiction_rejected = False
    try:
        persist_disambiguation_counterexample_fail_closed(
            root,
            task_key=task_key,
            input_domain="object",
            output_domain="boolean",
            support=support,
            probe=counterexample["input"],
            answer=not bool(counterexample["output"]),
            probe_commitment=str(memory["probe_commitment"]),
            route_outcome_sha256=memory["route_outcome_sha256"],
            answer_source="contradictory-frozen-oracle-after-same-probe-commitment",
        )
    except PersistentDisambiguationMemoryError as exc:
        if "replacement refused" not in str(exc):
            raise
        contradiction_rejected = True
    if not contradiction_rejected:
        raise PersistentDisambiguationMemoryError("contradictory memory silently replaced prior evidence")
    if memory_path.read_bytes() != original_bytes:
        raise PersistentDisambiguationMemoryError("contradiction handling changed original memory")

    conflict_dir = memory_path.parent / "conflicts"
    conflicts = sorted(conflict_dir.glob("conflict-*.json"))
    if len(conflicts) != 1:
        raise PersistentDisambiguationMemoryError("expected one retained contradiction record")
    conflict = json.loads(conflicts[0].read_text(encoding="utf-8"))
    if conflict.get("replacement_performed") is not False:
        raise PersistentDisambiguationMemoryError("conflict record does not prove fail-closed behavior")
    reloaded = load_disambiguation_counterexample(
        root,
        task_key=task_key,
        input_domain="object",
        output_domain="boolean",
        support=support,
    )
    if reloaded["memory_sha256"] != memory["memory_sha256"]:
        raise PersistentDisambiguationMemoryError("original remembered evidence changed after conflict")

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "conflict-safe-persistent-disambiguation-memory",
        "base_campaign_digest": base["digest"],
        "memory_sha256": memory["memory_sha256"],
        "exact_repeat_idempotent": repeated["idempotent_reuse"] is True,
        "contradictory_repeat_rejected": contradiction_rejected,
        "original_memory_preserved": memory_path.read_bytes() == original_bytes,
        "retained_conflict_count": len(conflicts),
        "conflict_sha256": conflict["conflict_sha256"],
        "replacement_performed": conflict["replacement_performed"],
        "memory_still_loads_after_conflict": reloaded["memory_sha256"] == memory["memory_sha256"],
        "claim_boundary": (
            "Internal robustness and continual-memory evidence only. Exact repeats are idempotent and "
            "contradictory repeated evidence is retained separately while replacement fails closed. "
            "The evidence remains repository-authored and is not AGI or independent production proof."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "conflict-safe-disambiguation-memory"
        / f"conflict-safe-memory-{_digest(seed)[:16]}.json",
        report,
    )
    return report
