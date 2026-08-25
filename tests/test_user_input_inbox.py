from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from agi.user_input_inbox import (
    UserInputInboxError,
    append_remote_user_input_inbox,
    load_user_input_inbox,
    prepare_user_input_inbox_append,
    serialize_user_input_inbox,
    unapplied_user_inputs,
    validate_user_input_inbox,
)


ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_user_input_inbox_is_valid_and_append_only() -> None:
    inbox = load_user_input_inbox(ROOT)

    assert validate_user_input_inbox(inbox) == []
    assert inbox["revision"] == len(inbox["entries"])
    assert [entry["sequence"] for entry in inbox["entries"]] == list(
        range(1, inbox["revision"] + 1)
    )
    assert inbox["policy"]["development_writer_lease_required_to_read"] is False
    assert inbox["policy"]["apply_only_at_safe_semantic_boundaries"] is True
    assert inbox["policy"]["user_input_is_not_automatic_proof"] is True


def test_unapplied_user_inputs_are_revision_ordered() -> None:
    inbox = load_user_input_inbox(ROOT)

    active_sequences = [
        entry["sequence"] for entry in inbox["entries"] if entry["status"] == "active"
    ]
    for applied_revision in range(inbox["revision"] + 1):
        assert [
            entry["sequence"]
            for entry in unapplied_user_inputs(inbox, after_revision=applied_revision)
        ] == [sequence for sequence in active_sequences if sequence > applied_revision]

    with pytest.raises(UserInputInboxError, match="ahead of inbox revision"):
        unapplied_user_inputs(inbox, after_revision=inbox["revision"] + 1)


def test_truncated_revision_is_quarantined_without_becoming_effective() -> None:
    inbox = load_user_input_inbox(ROOT)

    quarantined = inbox["entries"][15]
    resumed = inbox["entries"][16]
    assert quarantined["sequence"] == 16
    assert quarantined["status"] == "withdrawn"
    assert quarantined["source"] == "repository_integrity_repair"
    assert "645f6f1f5ef0b4e7842142991f3748df858082a1" in " ".join(
        quarantined["directives"]
    )
    assert resumed["sequence"] == 17
    assert resumed["status"] == "active"
    assert resumed["directives"][0] == "The user's exact words were: 再開して (2026-08-24)."
    assert [entry["sequence"] for entry in unapplied_user_inputs(inbox, after_revision=15)] == [
        entry["sequence"]
        for entry in inbox["entries"][15:]
        if entry["status"] == "active"
    ]


def test_recovery_entries_are_integrated_after_revision_17_with_exact_provenance() -> None:
    inbox = load_user_input_inbox(ROOT)
    recovery = json.loads((ROOT / "agi" / "USER_INPUT_INBOX_RECOVERY.json").read_text())

    assert inbox["revision"] >= recovery["integration_receipt"]["result_revision"]
    assert recovery["status"] == "integrated_authoritative_revision_19"
    assert recovery["integration_receipt"]["expected_revision"] == 17
    assert recovery["integration_receipt"]["result_revision"] == 19
    assert recovery["integration_receipt"]["remote_readback_verified"] is True
    for target, source, sequence in zip(
        inbox["entries"][17:19], recovery["pending_entries"], (18, 19), strict=True
    ):
        expected = deepcopy(source)
        expected["sequence"] = sequence
        assert target == expected


def test_revision_22_supersedes_legacy_feed_bridge_with_clean_g1_only() -> None:
    inbox = load_user_input_inbox(ROOT)
    ledger = json.loads((ROOT / "agi" / "USER_DIRECTIVE_EVENTS.json").read_text())
    strategy = json.loads((ROOT / "agi" / "WORK_STRATEGY.json").read_text())

    assert inbox["revision"] == 22
    assert inbox["entries"][-1]["sequence"] == 22
    assert inbox["entries"][-1]["supersedes"] == [
        "user-direction-external-research-feed-poll-20260825-v21"
    ]
    assert ledger["source"]["revision"] == 22
    assert ledger["source"]["content_digest"] == hashlib.sha256(
        json.dumps(inbox, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()

    atoms = {atom["atom_id"]: atom for atom in ledger["atoms"]}
    assert atoms["r22-clean-g1-research-feed-boundary-poll"]["supersedes"] == [
        "r21-external-research-feed-boundary-poll"
    ]
    assert atoms["r22-clean-g1-research-feed-ingestion"]["supersedes"] == [
        "r21-external-research-feed-ingestion"
    ]
    assert strategy["context_management"]["source_user_input_revision"] == 22
    assert strategy["research_feed"]["source_user_input_revision"] == 22
    assert strategy["research_feed"]["path"] == "research_index_clean_g1/O_FEED.json"
    assert "historical_only" in strategy["research_feed"]["legacy_policy"]


def _append_entry(entry_id: str = "new-user-input") -> dict:
    return {
        "id": entry_id,
        "received_at": "2026-08-24T07:00:00Z",
        "kind": "user_direction",
        "status": "active",
        "summary": "A new safe append test entry.",
        "directives": ["Preserve expected-revision and provider readback safety."],
        "supersedes": [],
        "source": "user_chat",
    }


def test_prepare_append_is_revision_bound_validated_and_idempotent() -> None:
    inbox = load_user_input_inbox(ROOT)
    expected_revision = inbox["revision"]
    entry = _append_entry()
    prepared = prepare_user_input_inbox_append(
        inbox,
        [entry],
        expected_revision=expected_revision,
        updated_at=datetime(2026, 8, 24, 7, 1, tzinfo=timezone.utc),
    )

    assert prepared["status"] == "prepared"
    assert prepared["value"]["revision"] == expected_revision + 1
    assert prepared["value"]["entries"][:expected_revision] == inbox["entries"]
    assert prepared["value"]["entries"][expected_revision] == {
        **entry,
        "sequence": expected_revision + 1,
    }
    assert validate_user_input_inbox(prepared["value"]) == []
    assert serialize_user_input_inbox(prepared["value"]).endswith("\n")

    retried = prepare_user_input_inbox_append(
        prepared["value"],
        [entry],
        expected_revision=expected_revision,
        updated_at=datetime(2026, 8, 24, 7, 2, tzinfo=timezone.utc),
    )
    assert retried["status"] == "already_applied"


def test_prepare_append_rejects_stale_sequence_duplicate_and_secret() -> None:
    inbox = load_user_input_inbox(ROOT)
    expected_revision = inbox["revision"]
    timestamp = datetime(2026, 8, 24, 7, 1, tzinfo=timezone.utc)
    with pytest.raises(UserInputInboxError, match="revision conflict"):
        prepare_user_input_inbox_append(
            inbox,
            [_append_entry()],
            expected_revision=expected_revision - 1,
            updated_at=timestamp,
        )
    with pytest.raises(UserInputInboxError, match="sequence conflict"):
        prepare_user_input_inbox_append(
            inbox,
            [{**_append_entry(), "sequence": 99}],
            expected_revision=expected_revision,
            updated_at=timestamp,
        )
    with pytest.raises(UserInputInboxError, match="forbidden secret-bearing field"):
        prepare_user_input_inbox_append(
            inbox,
            [{**_append_entry(), "token": "must-not-persist"}],
            expected_revision=expected_revision,
            updated_at=timestamp,
        )
    with pytest.raises(UserInputInboxError, match="updated_at must include a timezone"):
        prepare_user_input_inbox_append(
            inbox,
            [_append_entry()],
            expected_revision=expected_revision,
            updated_at="2026-08-24T07:01:00Z",  # type: ignore[arg-type]
        )


def test_remote_append_performs_one_cas_and_exact_readback() -> None:
    current = load_user_input_inbox(ROOT)
    expected_revision = current["revision"]
    state = {
        "content": serialize_user_input_inbox(current),
        "blob_sha": "a" * 40,
    }
    calls: list[tuple[str, str]] = []

    def fetch() -> dict:
        return deepcopy(state)

    def compare_and_swap(expected_blob_sha: str, content: str) -> dict:
        calls.append((expected_blob_sha, content))
        assert expected_blob_sha == state["blob_sha"]
        state["content"] = content
        state["blob_sha"] = "b" * 40
        return {"commit_sha": "c" * 40, "content_sha": state["blob_sha"]}

    receipt = append_remote_user_input_inbox(
        [_append_entry()],
        expected_revision=expected_revision,
        updated_at=datetime(2026, 8, 24, 7, 1, tzinfo=timezone.utc),
        fetch=fetch,
        compare_and_swap=compare_and_swap,
    )

    assert len(calls) == 1
    assert receipt == {
        "status": "appended",
        "expected_revision": expected_revision,
        "result_revision": expected_revision + 1,
        "expected_blob_sha": "a" * 40,
        "result_blob_sha": "b" * 40,
        "result_commit_sha": "c" * 40,
        "entry_ids": ["new-user-input"],
        "content_sha256": hashlib.sha256(state["content"].encode()).hexdigest(),
        "remote_readback_verified": True,
        "readback_attempts": 1,
    }

    retry = append_remote_user_input_inbox(
        [_append_entry()],
        expected_revision=expected_revision,
        updated_at=datetime(2026, 8, 24, 7, 2, tzinfo=timezone.utc),
        fetch=fetch,
        compare_and_swap=compare_and_swap,
    )
    assert retry["status"] == "already_applied"
    assert len(calls) == 1


def test_remote_append_tolerates_bounded_stale_readback_without_repeating_cas() -> None:
    current = load_user_input_inbox(ROOT)
    expected_revision = current["revision"]
    current_content = serialize_user_input_inbox(current)
    published: dict[str, str] = {}
    fetch_calls = 0
    cas_calls = 0
    waits: list[int] = []

    def fetch() -> dict:
        nonlocal fetch_calls
        fetch_calls += 1
        if fetch_calls <= 2:
            return {"content": current_content, "blob_sha": "a" * 40}
        return {"content": published["content"], "blob_sha": "b" * 40}

    def compare_and_swap(expected_blob_sha: str, content: str) -> dict:
        nonlocal cas_calls
        cas_calls += 1
        assert expected_blob_sha == "a" * 40
        published["content"] = content
        return {"commit_sha": "c" * 40, "content_sha": "b" * 40}

    receipt = append_remote_user_input_inbox(
        [_append_entry()],
        expected_revision=expected_revision,
        updated_at=datetime(2026, 8, 24, 7, 1, tzinfo=timezone.utc),
        fetch=fetch,
        compare_and_swap=compare_and_swap,
        readback_wait=waits.append,
    )

    assert receipt["readback_attempts"] == 2
    assert cas_calls == 1
    assert waits == [1]


def test_remote_append_fails_closed_before_cas_or_after_one_mismatched_readback() -> None:
    current = load_user_input_inbox(ROOT)
    expected_revision = current["revision"]
    content = serialize_user_input_inbox(current)
    cas_calls = 0

    def stale_fetch() -> dict:
        return {"content": content, "blob_sha": "a" * 40}

    def cas(_: str, __: str) -> dict:
        nonlocal cas_calls
        cas_calls += 1
        return {"commit_sha": "c" * 40, "content_sha": "b" * 40}

    with pytest.raises(UserInputInboxError, match="revision conflict"):
        append_remote_user_input_inbox(
            [_append_entry()],
            expected_revision=expected_revision - 1,
            updated_at=datetime(2026, 8, 24, 7, 1, tzinfo=timezone.utc),
            fetch=stale_fetch,
            compare_and_swap=cas,
        )
    assert cas_calls == 0

    with pytest.raises(UserInputInboxError, match="readback mismatch"):
        append_remote_user_input_inbox(
            [_append_entry()],
            expected_revision=expected_revision,
            updated_at=datetime(2026, 8, 24, 7, 1, tzinfo=timezone.utc),
            fetch=stale_fetch,
            compare_and_swap=cas,
        )
    assert cas_calls == 1

    with pytest.raises(UserInputInboxError, match="malformed JSON"):
        append_remote_user_input_inbox(
            [_append_entry()],
            expected_revision=expected_revision,
            updated_at=datetime(2026, 8, 24, 7, 1, tzinfo=timezone.utc),
            fetch=lambda: {"content": "{", "blob_sha": "a" * 40},
            compare_and_swap=cas,
        )
    assert cas_calls == 1


def test_remote_append_never_retries_a_failed_cas() -> None:
    current = load_user_input_inbox(ROOT)
    expected_revision = current["revision"]
    content = serialize_user_input_inbox(current)
    cas_calls = 0

    def fetch() -> dict:
        return {"content": content, "blob_sha": "a" * 40}

    def rejected_cas(_: str, __: str) -> dict:
        nonlocal cas_calls
        cas_calls += 1
        raise UserInputInboxError("provider compare-and-swap conflict")

    with pytest.raises(UserInputInboxError, match="compare-and-swap conflict"):
        append_remote_user_input_inbox(
            [_append_entry()],
            expected_revision=expected_revision,
            updated_at=datetime(2026, 8, 24, 7, 1, tzinfo=timezone.utc),
            fetch=fetch,
            compare_and_swap=rejected_cas,
        )
    assert cas_calls == 1

def test_user_input_inbox_rejects_secret_bearing_fields_and_sequence_gaps() -> None:
    inbox = load_user_input_inbox(ROOT)

    unsafe = deepcopy(inbox)
    unsafe["entries"][0]["token"] = "must-not-be-stored"
    assert any("forbidden secret-bearing field" in error for error in validate_user_input_inbox(unsafe))

    gapped = deepcopy(inbox)
    gapped["entries"][1]["sequence"] = 3
    assert any("contiguous" in error for error in validate_user_input_inbox(gapped))
