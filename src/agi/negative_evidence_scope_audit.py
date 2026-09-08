from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping


class NegativeEvidenceScopeAuditError(ValueError):
    """Raised when retained negative evidence exceeds its verified scope."""


_CLASSIFICATIONS = {
    "tested_variant_failure",
    "adaptation_or_ablation_loss",
    "original_baseline_reproduction_failure",
    "untested_mechanism",
}
_CONTROL_DECISIONS = {
    "reuse_provenance_equivalent",
    "missing_provenance_equivalent_control",
    "not_required_without_negative_attribution",
}
_PROHIBITED = [
    "scientist_agent_family_failure",
    "untested_mechanism_failure",
    "original_method_failure_without_direct_evidence",
]


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NegativeEvidenceScopeAuditError(f"{label} must be non-empty text")
    return value


def _pointer(value: Any, pointer: str) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise NegativeEvidenceScopeAuditError("source pointer must be an absolute JSON pointer")
    current = value
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as exc:
                raise NegativeEvidenceScopeAuditError(
                    f"unresolved list pointer token: {token}"
                ) from exc
        elif isinstance(current, Mapping) and token in current:
            current = current[token]
        else:
            raise NegativeEvidenceScopeAuditError(f"unresolved pointer token: {token}")
    return current


def _load_source(root: Path, source: Mapping[str, Any]) -> Any:
    path_text = _text(source.get("path"), "source.path")
    path = Path(path_text)
    if path.is_absolute() or ".." in path.parts:
        raise NegativeEvidenceScopeAuditError("source.path must stay inside the repository")
    try:
        value = json.loads((root / path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise NegativeEvidenceScopeAuditError(f"unreadable source: {path_text}") from exc
    selected = _pointer(value, _text(source.get("json_pointer"), "source.json_pointer"))
    if source.get("content_digest") != _digest(selected):
        raise NegativeEvidenceScopeAuditError(f"source digest mismatch: {path_text}")
    return selected


def _validate_control(value: Any, entry_id: str) -> None:
    if not isinstance(value, Mapping):
        raise NegativeEvidenceScopeAuditError(f"{entry_id}: positive_control must be an object")
    decision = value.get("decision")
    if decision not in _CONTROL_DECISIONS:
        raise NegativeEvidenceScopeAuditError(f"{entry_id}: invalid positive-control decision")
    _text(value.get("rationale"), f"{entry_id}: positive_control.rationale")
    refs = value.get("provenance_refs")
    if not isinstance(refs, list) or not all(isinstance(item, str) and item for item in refs):
        raise NegativeEvidenceScopeAuditError(f"{entry_id}: invalid control provenance")
    equivalence = value.get("equivalence")
    if not isinstance(equivalence, Mapping):
        raise NegativeEvidenceScopeAuditError(f"{entry_id}: equivalence must be an object")
    status = equivalence.get("status")
    criteria = equivalence.get("criteria")
    if not isinstance(criteria, list) or not all(isinstance(item, str) and item for item in criteria):
        raise NegativeEvidenceScopeAuditError(f"{entry_id}: equivalence criteria missing")
    if decision == "reuse_provenance_equivalent":
        if status != "established" or not refs:
            raise NegativeEvidenceScopeAuditError(
                f"{entry_id}: reused control lacks established provenance equivalence"
            )
    elif decision == "missing_provenance_equivalent_control":
        if status != "not_established":
            raise NegativeEvidenceScopeAuditError(
                f"{entry_id}: missing control was silently treated as equivalent"
            )
    elif status != "not_applicable":
        raise NegativeEvidenceScopeAuditError(
            f"{entry_id}: no-attribution control must be not_applicable"
        )


def validate_negative_evidence_scope_ledger(
    value: Mapping[str, Any], *, root: Path
) -> dict[str, Any]:
    """Validate source-bound revision-20 scope and positive-control decisions."""

    if value.get("schema_version") != 1 or value.get("policy_revision") != 20:
        raise NegativeEvidenceScopeAuditError("unsupported ledger schema or policy revision")
    if value.get("status") != "completed":
        raise NegativeEvidenceScopeAuditError("ledger must be completed")
    entries = value.get("entries")
    if not isinstance(entries, list) or not entries:
        raise NegativeEvidenceScopeAuditError("entries must be a non-empty array")
    ids = [entry.get("entry_id") for entry in entries if isinstance(entry, Mapping)]
    if len(ids) != len(entries) or ids != sorted(ids) or len(ids) != len(set(ids)):
        raise NegativeEvidenceScopeAuditError("entries must have unique sorted entry ids")

    counts = {name: 0 for name in sorted(_CLASSIFICATIONS)}
    repair_count = 0
    for raw in entries:
        entry = dict(raw)
        entry_id = _text(entry.get("entry_id"), "entry_id")
        source = entry.get("source")
        if not isinstance(source, Mapping):
            raise NegativeEvidenceScopeAuditError(f"{entry_id}: source must be an object")
        _load_source(root, source)
        for field in (
            "tested_target",
            "candidate_or_version",
            "configuration",
            "conditions",
            "supported_conclusion",
        ):
            _text(entry.get(field), f"{entry_id}: {field}")
        classification = entry.get("classification")
        if classification not in _CLASSIFICATIONS:
            raise NegativeEvidenceScopeAuditError(f"{entry_id}: invalid classification")
        counts[classification] += 1
        if entry.get("prohibited_conclusions") != _PROHIBITED:
            raise NegativeEvidenceScopeAuditError(
                f"{entry_id}: prohibited breadth must remain exact"
            )
        if entry.get("family_failure_supported") is not False:
            raise NegativeEvidenceScopeAuditError(f"{entry_id}: family inference is unsupported")
        if entry.get("untested_mechanism_failure_supported") is not False:
            raise NegativeEvidenceScopeAuditError(
                f"{entry_id}: untested-mechanism inference is unsupported"
            )
        _validate_control(entry.get("positive_control"), entry_id)
        repair = entry.get("repair")
        if not isinstance(repair, Mapping) or not isinstance(repair.get("required"), bool):
            raise NegativeEvidenceScopeAuditError(f"{entry_id}: repair contract missing")
        if repair["required"]:
            repair_count += 1
            _text(repair.get("source_locator"), f"{entry_id}: repair.source_locator")
            _text(repair.get("bounded_replacement"), f"{entry_id}: bounded_replacement")
        elif repair.get("source_locator") is not None or repair.get("bounded_replacement") is not None:
            raise NegativeEvidenceScopeAuditError(f"{entry_id}: spurious repair payload")

    expected_summary = {
        "entry_count": len(entries),
        "classification_counts": counts,
        "repair_required_count": repair_count,
        "family_wide_negative_count": 0,
        "untested_mechanism_negative_count": 0,
    }
    if value.get("summary") != expected_summary:
        raise NegativeEvidenceScopeAuditError("summary does not match audited entries")
    body = deepcopy(dict(value))
    supplied = body.pop("ledger_digest", None)
    if supplied != _digest(body):
        raise NegativeEvidenceScopeAuditError("ledger digest mismatch")
    return deepcopy(dict(value))


def load_negative_evidence_scope_ledger(path: Path, *, root: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise NegativeEvidenceScopeAuditError("unreadable scope ledger") from exc
    if not isinstance(value, Mapping):
        raise NegativeEvidenceScopeAuditError("scope ledger must be an object")
    return validate_negative_evidence_scope_ledger(value, root=root)
