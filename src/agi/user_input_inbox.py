from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any, Mapping


INBOX_PATH = Path("agi/USER_INPUT_INBOX.json")
ENTRY_STATUSES = {"active", "superseded", "withdrawn"}
FORBIDDEN_SECRET_KEYS = {
    "api_key",
    "authorization",
    "credential",
    "credentials",
    "cookie",
    "password",
    "private_key",
    "secret",
    "token",
}


class UserInputInboxError(RuntimeError):
    pass


def _aware_timestamp(value: Any, *, field: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field} must be a non-empty ISO-8601 string")
        return
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{field} must be a valid ISO-8601 timestamp")
        return
    if parsed.tzinfo is None:
        errors.append(f"{field} must include a timezone")


def _forbidden_secret_fields(value: Any, path: str = "inbox") -> list[str]:
    errors: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized = key.lower().replace("-", "_")
            child_path = f"{path}.{key}"
            if normalized in FORBIDDEN_SECRET_KEYS:
                errors.append(f"forbidden secret-bearing field: {child_path}")
            errors.extend(_forbidden_secret_fields(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_forbidden_secret_fields(child, f"{path}[{index}]"))
    return errors


def validate_user_input_inbox(value: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if value.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    revision = value.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        errors.append("revision must be a non-negative integer")
        revision = -1

    policy = value.get("policy")
    required_policy = {
        "append_only_semantics": True,
        "development_writer_lease_required_to_read": False,
        "development_writer_lease_required_to_append": False,
        "append_requires_expected_revision": True,
        "secrets_allowed": False,
        "apply_only_at_safe_semantic_boundaries": True,
        "user_input_is_not_automatic_proof": True,
    }
    if not isinstance(policy, Mapping):
        errors.append("policy must be an object")
    else:
        for key, expected in required_policy.items():
            if policy.get(key) is not expected:
                errors.append(f"policy.{key} must be {expected!r}")

    entries = value.get("entries")
    if not isinstance(entries, list):
        errors.append("entries must be an array")
        entries = []
    seen_ids: set[str] = set()
    seen_sequences: set[int] = set()
    for index, entry in enumerate(entries):
        prefix = f"entries[{index}]"
        if not isinstance(entry, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        sequence = entry.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            errors.append(f"{prefix}.sequence must be a positive integer")
        elif sequence in seen_sequences:
            errors.append(f"duplicate sequence: {sequence}")
        else:
            seen_sequences.add(sequence)
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id.strip():
            errors.append(f"{prefix}.id must be non-empty")
        elif entry_id in seen_ids:
            errors.append(f"duplicate entry id: {entry_id}")
        else:
            seen_ids.add(entry_id)
        _aware_timestamp(entry.get("received_at"), field=f"{prefix}.received_at", errors=errors)
        if entry.get("status") not in ENTRY_STATUSES:
            errors.append(f"{prefix}.status is invalid")
        for field in ("kind", "summary", "source"):
            field_value = entry.get(field)
            if not isinstance(field_value, str) or not field_value.strip():
                errors.append(f"{prefix}.{field} must be non-empty")
        directives = entry.get("directives")
        if not isinstance(directives, list) or not directives or not all(
            isinstance(item, str) and item.strip() for item in directives
        ):
            errors.append(f"{prefix}.directives must contain non-empty strings")
        supersedes = entry.get("supersedes")
        if not isinstance(supersedes, list) or not all(
            isinstance(item, str) and item.strip() for item in supersedes
        ):
            errors.append(f"{prefix}.supersedes must be an array of non-empty ids")

    if isinstance(revision, int) and revision >= 0:
        expected = set(range(1, revision + 1))
        if seen_sequences != expected:
            errors.append("entry sequences must be contiguous from 1 through revision")
        if len(entries) != revision:
            errors.append("revision must equal the number of append-only entries")
    _aware_timestamp(value.get("updated_at"), field="updated_at", errors=errors)
    errors.extend(_forbidden_secret_fields(value))
    return errors


def load_user_input_inbox(root: Path) -> dict[str, Any]:
    path = root / INBOX_PATH
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UserInputInboxError(f"cannot read user input inbox: {exc}") from exc
    if not isinstance(value, dict):
        raise UserInputInboxError("user input inbox must be a JSON object")
    errors = validate_user_input_inbox(value)
    if errors:
        raise UserInputInboxError("invalid user input inbox: " + "; ".join(errors))
    return value


def unapplied_user_inputs(
    value: Mapping[str, Any], *, after_revision: int
) -> list[dict[str, Any]]:
    errors = validate_user_input_inbox(value)
    if errors:
        raise UserInputInboxError("invalid user input inbox: " + "; ".join(errors))
    if isinstance(after_revision, bool) or not isinstance(after_revision, int) or after_revision < 0:
        raise UserInputInboxError("after_revision must be a non-negative integer")
    revision = int(value["revision"])
    if after_revision > revision:
        raise UserInputInboxError(
            f"applied revision {after_revision} is ahead of inbox revision {revision}"
        )
    return [
        dict(entry)
        for entry in value["entries"]
        if entry["sequence"] > after_revision and entry["status"] == "active"
    ]
