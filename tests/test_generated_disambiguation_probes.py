from __future__ import annotations

import json
from pathlib import Path

from agi.generated_disambiguation_probes import run_generated_disambiguation_probe_campaign


def test_disambiguation_generates_fresh_typed_probe_without_caller_pool(
    runtime_repo: Path,
) -> None:
    report = run_generated_disambiguation_probe_campaign(
        runtime_repo,
        "generated-disambiguation-probe-seed",
    )

    assert report["passed"] is True
    assert report["campaign_kind"] == "generated-disambiguation-probes"
    assert report["candidate_ids_supplied_by_caller"] is False
    assert report["probe_pool_supplied_by_caller"] is False
    assert report["generated_probe_count"] > 0
    assert report["probe_generator"].startswith("bounded-domain-kernels")
    assert report["initial_matching_route_count"] == 2
    assert report["probe_input"] < 0
    assert report["distinct_route_output_count"] == 2
    assert report["counterexample_answer"] == abs(report["probe_input"])
    assert len(report["refined_selected_candidate_ids"]) == 1
    assert report["refined_selected_depth"] == 1
    assert report["candidate_trial_state_unchanged"] is True

    evidence_dir = (
        runtime_repo
        / ".continual"
        / "evidence"
        / "generated-disambiguation-probes"
    )
    files = list(evidence_dir.glob("generated-probes-*.json"))
    assert len(files) == 1
    persisted = json.loads(files[0].read_text(encoding="utf-8"))
    assert persisted["digest"] == report["digest"]
    assert persisted["passed"] is True
