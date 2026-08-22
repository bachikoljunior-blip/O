from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from agi.user_input_inbox import (
    UserInputInboxError,
    load_user_input_inbox,
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


def test_user_input_inbox_rejects_secret_bearing_fields_and_sequence_gaps() -> None:
    inbox = load_user_input_inbox(ROOT)

    unsafe = deepcopy(inbox)
    unsafe["entries"][0]["token"] = "must-not-be-stored"
    assert any("forbidden secret-bearing field" in error for error in validate_user_input_inbox(unsafe))

    gapped = deepcopy(inbox)
    gapped["entries"][1]["sequence"] = 3
    assert any("contiguous" in error for error in validate_user_input_inbox(gapped))
