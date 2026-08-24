from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
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


RemoteInboxFetch = Callable[[], Mapping[str, Any]]
RemoteInboxCompareAndSwap = Callable[[str, str], Mapping[str, Any]]
RemoteInboxReadbackWait = Callable[[int], None]


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


def serialize_user_input_inbox(value: Mapping[str, Any]) -> str:
    """Return validated, deterministic UTF-8 text for the authoritative inbox."""

    errors = validate_user_input_inbox(value)
    if errors:
        raise UserInputInboxError("invalid user input inbox: " + "; ".join(errors))
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _validated_append_entries(
    entries: Sequence[Mapping[str, Any]], *, expected_revision: int
) -> list[dict[str, Any]]:
    if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence) or not entries:
        raise UserInputInboxError("entries must be a non-empty sequence of objects")
    prepared: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for offset, raw_entry in enumerate(entries, start=1):
        if not isinstance(raw_entry, Mapping):
            raise UserInputInboxError("every appended entry must be an object")
        entry = deepcopy(dict(raw_entry))
        target_sequence = expected_revision + offset
        supplied_sequence = entry.get("sequence")
        if supplied_sequence is not None and supplied_sequence != target_sequence:
            raise UserInputInboxError(
                "entry sequence conflict: "
                f"expected {target_sequence}, observed {supplied_sequence}"
            )
        entry["sequence"] = target_sequence
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id.strip():
            raise UserInputInboxError("every appended entry id must be non-empty")
        if entry_id in seen_ids:
            raise UserInputInboxError(f"duplicate appended entry id: {entry_id}")
        seen_ids.add(entry_id)
        prepared.append(entry)
    return prepared


def prepare_user_input_inbox_append(
    current: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    *,
    expected_revision: int,
    updated_at: datetime,
) -> dict[str, Any]:
    """Build one revision-bound append candidate without performing I/O.

    A retry after an ambiguous provider response is idempotent only when every
    requested entry already exists at its exact expected sequence with identical
    content. Any stale or divergent request fails closed.
    """

    errors = validate_user_input_inbox(current)
    if errors:
        raise UserInputInboxError("invalid current user input inbox: " + "; ".join(errors))
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 0
    ):
        raise UserInputInboxError("expected_revision must be a non-negative integer")
    if (
        not isinstance(updated_at, datetime)
        or updated_at.tzinfo is None
        or updated_at.utcoffset() is None
    ):
        raise UserInputInboxError("updated_at must include a timezone")

    prepared = _validated_append_entries(entries, expected_revision=expected_revision)
    current_revision = int(current["revision"])
    existing_by_id = {entry["id"]: entry for entry in current["entries"]}

    if current_revision > expected_revision:
        if all(existing_by_id.get(entry["id"]) == entry for entry in prepared):
            return {
                "status": "already_applied",
                "expected_revision": expected_revision,
                "result_revision": current_revision,
                "entry_ids": [entry["id"] for entry in prepared],
                "value": deepcopy(dict(current)),
            }
        raise UserInputInboxError(
            "inbox revision conflict: "
            f"expected {expected_revision}, observed {current_revision}"
        )
    if current_revision != expected_revision:
        raise UserInputInboxError(
            "inbox revision conflict: "
            f"expected {expected_revision}, observed {current_revision}"
        )
    duplicates = [entry["id"] for entry in prepared if entry["id"] in existing_by_id]
    if duplicates:
        raise UserInputInboxError(
            "entry id already exists with non-idempotent placement: " + ", ".join(duplicates)
        )

    candidate = deepcopy(dict(current))
    candidate["entries"].extend(prepared)
    candidate["revision"] = expected_revision + len(prepared)
    candidate["updated_at"] = (
        updated_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    errors = validate_user_input_inbox(candidate)
    if errors:
        raise UserInputInboxError("invalid appended user input inbox: " + "; ".join(errors))
    return {
        "status": "prepared",
        "expected_revision": expected_revision,
        "result_revision": candidate["revision"],
        "entry_ids": [entry["id"] for entry in prepared],
        "value": candidate,
    }


def _remote_inbox_snapshot(fetch: RemoteInboxFetch) -> dict[str, Any]:
    raw = fetch()
    if not isinstance(raw, Mapping):
        raise UserInputInboxError("remote inbox fetch must return an object")
    content = raw.get("content")
    blob_sha = raw.get("blob_sha")
    if not isinstance(content, str) or not content:
        raise UserInputInboxError("remote inbox content must be non-empty UTF-8 text")
    if (
        not isinstance(blob_sha, str)
        or len(blob_sha) != 40
        or any(character not in "0123456789abcdef" for character in blob_sha)
    ):
        raise UserInputInboxError("remote inbox blob_sha must be a lowercase 40-hex Git SHA")
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise UserInputInboxError(f"remote inbox is malformed JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise UserInputInboxError("remote inbox must be a JSON object")
    errors = validate_user_input_inbox(value)
    if errors:
        raise UserInputInboxError("invalid remote user input inbox: " + "; ".join(errors))
    return {"content": content, "blob_sha": blob_sha, "value": value}


def append_remote_user_input_inbox(
    entries: Sequence[Mapping[str, Any]],
    *,
    expected_revision: int,
    updated_at: datetime,
    fetch: RemoteInboxFetch,
    compare_and_swap: RemoteInboxCompareAndSwap,
    readback_attempts: int = 3,
    readback_wait: RemoteInboxReadbackWait | None = None,
) -> dict[str, Any]:
    """Append through one fail-closed fetch/CAS/readback transaction.

    ``compare_and_swap`` receives ``(expected_blob_sha, complete_content)`` and
    must atomically replace only ``agi/USER_INPUT_INBOX.json``. It returns the
    provider's ``commit_sha`` and ``content_sha``. This function never retries a
    mutation: a conflict or mismatched readback is surfaced for reconciliation.
    """

    if (
        isinstance(readback_attempts, bool)
        or not isinstance(readback_attempts, int)
        or readback_attempts < 1
    ):
        raise UserInputInboxError("readback_attempts must be a positive integer")
    before = _remote_inbox_snapshot(fetch)
    prepared = prepare_user_input_inbox_append(
        before["value"],
        entries,
        expected_revision=expected_revision,
        updated_at=updated_at,
    )
    if prepared["status"] == "already_applied":
        return {
            "status": "already_applied",
            "expected_revision": expected_revision,
            "result_revision": prepared["result_revision"],
            "expected_blob_sha": before["blob_sha"],
            "result_blob_sha": before["blob_sha"],
            "result_commit_sha": None,
            "entry_ids": prepared["entry_ids"],
            "content_sha256": hashlib.sha256(before["content"].encode("utf-8")).hexdigest(),
            "remote_readback_verified": True,
            "readback_attempts": 1,
        }

    content = serialize_user_input_inbox(prepared["value"])
    result = compare_and_swap(before["blob_sha"], content)
    if not isinstance(result, Mapping):
        raise UserInputInboxError("remote inbox CAS must return an object")
    commit_sha = result.get("commit_sha")
    content_sha = result.get("content_sha")
    for field, value in (("commit_sha", commit_sha), ("content_sha", content_sha)):
        if (
            not isinstance(value, str)
            or len(value) != 40
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise UserInputInboxError(f"remote inbox CAS {field} must be lowercase 40-hex")

    after: dict[str, Any] | None = None
    verified_attempt = 0
    for attempt in range(1, readback_attempts + 1):
        after = _remote_inbox_snapshot(fetch)
        if after["blob_sha"] == content_sha and after["content"] == content:
            verified_attempt = attempt
            break
        if attempt < readback_attempts and readback_wait is not None:
            readback_wait(attempt)
    if verified_attempt == 0 or after is None:
        raise UserInputInboxError(
            "remote inbox CAS readback mismatch; do not retry mutation before reconciliation"
        )
    if after["value"] != prepared["value"]:
        raise UserInputInboxError("remote inbox semantic readback mismatch")
    return {
        "status": "appended",
        "expected_revision": expected_revision,
        "result_revision": prepared["result_revision"],
        "expected_blob_sha": before["blob_sha"],
        "result_blob_sha": content_sha,
        "result_commit_sha": commit_sha,
        "entry_ids": prepared["entry_ids"],
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "remote_readback_verified": True,
        "readback_attempts": verified_attempt,
    }


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
        for entry in sorted(value["entries"], key=lambda entry: entry["sequence"])
        if entry["sequence"] > after_revision and entry["status"] == "active"
    ]
