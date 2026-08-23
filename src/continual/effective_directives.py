from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from agi.user_input_inbox import validate_user_input_inbox

from .store import Store


class EffectiveDirectiveError(ValueError):
    """Raised when O cannot compile one unambiguous effective user policy."""


_CARDINALITIES = {"single", "many"}
_OWNER_KINDS = {"work_primary", "work_recovery_automation"}


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EffectiveDirectiveError(f"{label} must be an object")
    return deepcopy(dict(value))


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EffectiveDirectiveError(f"{label} must be non-empty text")
    return value


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise EffectiveDirectiveError(f"{label} must be an integer >= {minimum}")
    return value


def _sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise EffectiveDirectiveError(f"{label} must be an array")
    return value


def _validate_runtime_bindings(
    effective_by_slot: Mapping[str, list[dict[str, Any]]],
    *,
    state: Mapping[str, Any],
    strategy: Mapping[str, Any],
) -> None:
    expected = {
        "execution.primary": "chatgpt_work_primary",
        "execution.main_writer": "single_fenced_primary",
        "publication.mode": "isolated_exact_head_ci_then_serial_main",
        "completion.condition": "user_objective_or_explicit_stop",
        "context.decision_authority": "O Engine",
    }
    for slot, expected_value in expected.items():
        atoms = effective_by_slot.get(slot, [])
        if len(atoms) != 1 or atoms[0]["value"] != expected_value:
            raise EffectiveDirectiveError(
                f"effective directive binding mismatch for {slot}"
            )

    if state.get("mode") != "work_o_engine_single_writer":
        raise EffectiveDirectiveError("compiled single-writer policy contradicts state.mode")
    if state.get("owner_kind") not in _OWNER_KINDS:
        raise EffectiveDirectiveError("compiled primary policy contradicts state.owner_kind")
    if state.get("status") != "running":
        raise EffectiveDirectiveError("compiled primary policy requires running state")
    publication = state.get("result_publication_policy")
    if not isinstance(publication, Mapping) or publication.get("destination") != "main":
        raise EffectiveDirectiveError("compiled publication policy contradicts state")
    if "force_push" not in publication.get("excludes", []):
        raise EffectiveDirectiveError("state publication policy must exclude force_push")
    completion = state.get("primary_run_contract")
    completion_text = (
        completion.get("normal_completion_condition")
        if isinstance(completion, Mapping)
        else None
    )
    if (
        not isinstance(completion_text, str)
        or "explicit_user_stop" not in completion_text
        or "optional verification machinery" not in completion_text
    ):
        raise EffectiveDirectiveError("compiled completion policy contradicts state")
    rules = strategy.get("execution_rules")
    if (
        not isinstance(rules, Mapping)
        or rules.get("validated_execution_results_destination") != "main"
        or "exact-head CI" not in str(rules.get("main_integration_rule"))
    ):
        raise EffectiveDirectiveError("compiled publication policy contradicts strategy")
    context = strategy.get("context_management")
    if (
        not isinstance(context, Mapping)
        or not str(context.get("decision_authority", "")).startswith("O Engine")
    ):
        raise EffectiveDirectiveError(
            "compiled context authority contradicts strategy"
        )


def compile_effective_directives(
    inbox_value: Mapping[str, Any],
    ledger_value: Mapping[str, Any],
    *,
    state: Mapping[str, Any],
    strategy: Mapping[str, Any],
    store: Store,
) -> dict[str, Any]:
    """Compile reviewed directive atoms without inferring policy from free text.

    The inbox remains the authoritative user-input source. The ledger is O's
    reviewed interpretation: every active source directive must be covered by
    one or more typed atoms and every atom is bound to the exact source entry
    bytes. Multiple atoms may intentionally cover one mixed prose directive.
    """

    inbox = _mapping(inbox_value, "inbox")
    inbox_errors = validate_user_input_inbox(inbox)
    if inbox_errors:
        raise EffectiveDirectiveError(
            "invalid authoritative inbox: " + "; ".join(inbox_errors)
        )
    ledger = _mapping(ledger_value, "directive ledger")
    if ledger.get("schema_version") != 1:
        raise EffectiveDirectiveError("directive ledger schema_version must be 1")
    source = _mapping(ledger.get("source"), "directive ledger.source")
    if source.get("path") != "agi/USER_INPUT_INBOX.json":
        raise EffectiveDirectiveError("directive ledger source path mismatch")
    revision = _integer(inbox.get("revision"), "inbox.revision")
    if source.get("revision") != revision:
        raise EffectiveDirectiveError("directive ledger source revision mismatch")
    inbox_digest = store.stable_digest(inbox, length=64)
    if source.get("content_digest") != inbox_digest:
        raise EffectiveDirectiveError("directive ledger source digest mismatch")

    active_entries: dict[str, dict[str, Any]] = {}
    for raw in inbox["entries"]:
        entry = _mapping(raw, "inbox entry")
        if entry.get("status") == "active":
            active_entries[_text(entry.get("id"), "inbox entry id")] = entry

    atoms_raw = _sequence(ledger.get("atoms"), "directive ledger.atoms")
    atoms: dict[str, dict[str, Any]] = {}
    coverage: set[tuple[str, int]] = set()
    for index, raw in enumerate(atoms_raw):
        atom = _mapping(raw, f"directive ledger.atoms[{index}]")
        atom_id = _text(atom.get("atom_id"), f"atom[{index}].atom_id")
        if atom_id in atoms:
            raise EffectiveDirectiveError(f"duplicate directive atom id: {atom_id}")
        entry_id = _text(atom.get("source_entry_id"), f"atom {atom_id} source_entry_id")
        entry = active_entries.get(entry_id)
        if entry is None:
            raise EffectiveDirectiveError(
                f"directive atom {atom_id} references unknown active source entry"
            )
        entry_digest = store.stable_digest(entry, length=64)
        if atom.get("source_entry_digest") != entry_digest:
            raise EffectiveDirectiveError(
                f"directive atom {atom_id} source entry digest mismatch"
            )
        precedence = _integer(atom.get("precedence"), f"atom {atom_id} precedence")
        if precedence != entry.get("sequence"):
            raise EffectiveDirectiveError(
                f"directive atom {atom_id} precedence must equal source sequence"
            )
        indices = _sequence(
            atom.get("source_directive_indices"),
            f"atom {atom_id} source_directive_indices",
        )
        if not indices:
            raise EffectiveDirectiveError(
                f"directive atom {atom_id} must reference a source directive"
            )
        directives = entry.get("directives", [])
        normalized_indices: list[int] = []
        for directive_index in indices:
            directive_index = _integer(
                directive_index,
                f"atom {atom_id} source directive index",
            )
            if directive_index >= len(directives):
                raise EffectiveDirectiveError(
                    f"directive atom {atom_id} source directive index is out of range"
                )
            key = (entry_id, directive_index)
            coverage.add(key)
            normalized_indices.append(directive_index)
        cardinality = _text(atom.get("cardinality"), f"atom {atom_id} cardinality")
        if cardinality not in _CARDINALITIES:
            raise EffectiveDirectiveError(
                f"directive atom {atom_id} cardinality is invalid"
            )
        supersedes = _sequence(atom.get("supersedes"), f"atom {atom_id} supersedes")
        if not all(isinstance(item, str) and item for item in supersedes):
            raise EffectiveDirectiveError(
                f"directive atom {atom_id} supersedes must contain atom ids"
            )
        atoms[atom_id] = {
            "atom_id": atom_id,
            "source_entry_id": entry_id,
            "source_sequence": entry["sequence"],
            "source_entry_digest": entry_digest,
            "source_directive_indices": normalized_indices,
            "slot": _text(atom.get("slot"), f"atom {atom_id} slot"),
            "cardinality": cardinality,
            "value": deepcopy(atom.get("value")),
            "precedence": precedence,
            "supersedes": list(supersedes),
        }

    expected_coverage = {
        (entry_id, index)
        for entry_id, entry in active_entries.items()
        for index in range(len(entry.get("directives", [])))
    }
    missing = sorted(expected_coverage - coverage)
    if missing:
        entry_id, directive_index = missing[0]
        raise EffectiveDirectiveError(
            f"active source directive is not atomized: {entry_id}[{directive_index}]"
        )

    graph: dict[str, list[str]] = {}
    for atom_id, atom in atoms.items():
        graph[atom_id] = []
        for target_id in atom["supersedes"]:
            target = atoms.get(target_id)
            if target is None:
                raise EffectiveDirectiveError(
                    f"directive atom {atom_id} supersedes unknown atom {target_id}"
                )
            if target["slot"] != atom["slot"]:
                raise EffectiveDirectiveError(
                    f"directive atom {atom_id} supersedes a different policy slot"
                )
            graph[atom_id].append(target_id)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(atom_id: str) -> None:
        if atom_id in visiting:
            raise EffectiveDirectiveError("directive supersede cycle")
        if atom_id in visited:
            return
        visiting.add(atom_id)
        for target_id in graph[atom_id]:
            visit(target_id)
        visiting.remove(atom_id)
        visited.add(atom_id)

    for atom_id in sorted(atoms):
        visit(atom_id)

    superseded_by: dict[str, str] = {}
    for atom_id, targets in graph.items():
        for target_id in targets:
            existing = superseded_by.get(target_id)
            if existing is not None and existing != atom_id:
                raise EffectiveDirectiveError(
                    f"directive atom {target_id} has multiple superseders"
                )
            superseded_by[target_id] = atom_id

    effective = [
        atom for atom_id, atom in atoms.items() if atom_id not in superseded_by
    ]
    effective.sort(key=lambda item: (item["slot"], item["precedence"], item["atom_id"]))
    effective_by_slot: dict[str, list[dict[str, Any]]] = {}
    for atom in effective:
        effective_by_slot.setdefault(atom["slot"], []).append(atom)
    for slot, slot_atoms in effective_by_slot.items():
        cardinalities = {atom["cardinality"] for atom in slot_atoms}
        if len(cardinalities) != 1:
            raise EffectiveDirectiveError(
                f"effective directive slot has mixed cardinality: {slot}"
            )
        if cardinalities == {"single"} and len(slot_atoms) != 1:
            raise EffectiveDirectiveError(
                f"conflicting active values in exclusive policy slot: {slot}"
            )

    _validate_runtime_bindings(
        effective_by_slot,
        state=state,
        strategy=strategy,
    )

    projected_atoms = [
        {
            "atom_id": atom["atom_id"],
            "activation_status": "effective",
            "slot": atom["slot"],
            "cardinality": atom["cardinality"],
            "value": deepcopy(atom["value"]),
            "precedence": atom["precedence"],
            "source_entry_id": atom["source_entry_id"],
            "source_sequence": atom["source_sequence"],
            "source_directive_indices": atom["source_directive_indices"],
        }
        for atom in effective
    ]
    ledger_digest = store.stable_digest(ledger, length=64)
    result = {
        "schema_version": 1,
        "source_revision": revision,
        "source_content_digest": inbox_digest,
        "ledger_content_digest": ledger_digest,
        "effective_atoms": projected_atoms,
        "superseded_atoms": [
            {
                "atom_id": atom_id,
                "activation_status": "superseded",
                "slot": atoms[atom_id]["slot"],
                "precedence": atoms[atom_id]["precedence"],
                "source_entry_id": atoms[atom_id]["source_entry_id"],
                "superseded_by": superseder,
            }
            for atom_id, superseder in sorted(superseded_by.items())
        ],
    }
    result["effective_policy_digest"] = store.stable_digest(result, length=64)
    return result
