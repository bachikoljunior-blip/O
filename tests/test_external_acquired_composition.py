from __future__ import annotations

import shutil
from pathlib import Path

from agi.external_acquired_composition import run_external_acquired_composition


MATERIALIZED_STRING_CANDIDATE = "acquired-runtime-4c6a95c7c7c7069a"


def test_external_tool_output_composes_with_materialized_acquired_program(runtime_repo) -> None:
    source = Path(__file__).resolve().parents[1]
    shutil.copytree(
        source / ".continual" / "candidates" / MATERIALIZED_STRING_CANDIDATE,
        runtime_repo / ".continual" / "candidates" / MATERIALIZED_STRING_CANDIDATE,
    )

    report = run_external_acquired_composition(
        runtime_repo,
        "external-acquired-composition-20260817",
    )

    assert report["passed"] is True
    assert report["candidate_ids"][1] == MATERIALIZED_STRING_CANDIDATE
    assert report["domain_chain"] == ["object", "string", "numeric"]
    assert len(set(report["fresh_engine_runs"])) == 2
    assert report["fresh_engine_composition_matched"] is True
    assert report["candidate_trial_state_unchanged"] is True
    assert report["live_model_invocation_required"] is False
    assert "does not establish" in report["claim_boundary"]
