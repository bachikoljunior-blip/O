from __future__ import annotations

import json
from pathlib import Path

from agi.conflict_safe_disambiguation_memory import (
    run_conflict_safe_disambiguation_memory_campaign,
)


def test_persistent_disambiguation_memory_is_idempotent_and_rejects_conflicting_replacement(
    runtime_repo: Path,
) -> None:
    report = run_conflict_safe_disambiguation_memory_campaign(
        runtime_repo,
        "conflict-safe-disambiguation-memory-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "conflict-safe-persistent-disambiguation-memory"
    assert report["exact_repeat_idempotent"] is True
    assert report["contradictory_repeat_rejected"] is True
    assert report["original_memory_preserved"] is True
    assert report["retained_conflict_count"] == 1
    assert report["replacement_performed"] is False
    assert report["memory_still_loads_after_conflict"] is True

    conflicts = list(
        (
            runtime_repo
            / ".continual"
            / "evidence"
            / "verified-macro-disambiguation-memory"
            / "conflicts"
        ).glob("conflict-*.json")
    )
    assert len(conflicts) == 1
    conflict = json.loads(conflicts[0].read_text(encoding="utf-8"))
    assert conflict["kind"] == "verified-macro-counterexample-memory-conflict-v1"
    assert conflict["original_memory_preserved"] is True
    assert conflict["replacement_performed"] is False
    assert conflict["resolution"] == "fail_closed_preserve_both_evidence_paths"
    assert conflict["proposed_evidence"]["counterexample"]["output"] is True
    assert conflict["existing_evidence"]["counterexample"]["output"] is False
    assert conflict["conflict_sha256"] == report["conflict_sha256"]

    evidence = list(
        (
            runtime_repo
            / ".continual"
            / "evidence"
            / "conflict-safe-disambiguation-memory"
        ).glob("conflict-safe-memory-*.json")
    )
    assert len(evidence) == 1
    persisted = json.loads(evidence[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
