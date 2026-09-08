from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable

from continual.learning_engine import LearningEnabledEngine

from .acquired_program_runtime import _atomic_json, _digest, _new_runtime_run
from .acquired_programs import execute_program
from .generated_crossdomain_cegis import (
    _runtime_call,
    generated_crossdomain_target,
    run_generated_crossdomain_cegis_campaign,
)


def _trial_snapshot(root: Path, candidate_id: str) -> dict[str, str]:
    trial_dir = root / ".continual" / "candidates" / candidate_id / "trials"
    return {
        path.name: _digest(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(trial_dir.glob("*.json"))
    }


def _retest_generated_capability(
    root: Path,
    seed: str,
    report: dict[str, Any],
    *,
    engine_factory: Callable[[Path], LearningEnabledEngine],
) -> dict[str, Any]:
    target, _, _, _, final_inputs = generated_crossdomain_target(seed)
    expected = tuple(execute_program(target, value) for value in final_inputs)
    before = _trial_snapshot(root, str(report["candidate_id"]))
    engine = engine_factory(root)
    run_id = _new_runtime_run(engine)
    results = []
    for query, answer_expected in zip(final_inputs, expected, strict=True):
        answer = _runtime_call(
            engine,
            run_id,
            scope=str(report["scope"]),
            tool_id=str(report["tool_id"]),
            value=query,
        )
        results.append(
            {
                "query_sha256": _digest(query),
                "answer_sha256": _digest(answer),
                "success": answer == answer_expected,
            }
        )
    after = _trial_snapshot(root, str(report["candidate_id"]))
    return {
        "candidate_id": report["candidate_id"],
        "scope": report["scope"],
        "score": sum(1.0 if item["success"] else 0.0 for item in results) / len(results),
        "trial_snapshot_before": before,
        "trial_snapshot_after": after,
        "trial_budget_unchanged": before == after,
        "results": results,
    }


def run_multicap_crossdomain_cegis_campaign(
    root: Path,
    seeds: Iterable[str],
    *,
    engine_factory: Callable[[Path], LearningEnabledEngine] = LearningEnabledEngine,
) -> dict[str, Any]:
    """Sequentially acquire distinct cross-domain capabilities and prove restart retention.

    Each promotion is followed by fresh-engine retesting of every capability accumulated so far.
    Runtime retests must leave each Candidate's persistent trial ledger unchanged.
    """

    root = root.resolve()
    seed_list = tuple(seeds)
    if len(seed_list) < 3 or any(not isinstance(seed, str) or not seed.strip() for seed in seed_list):
        raise ValueError("at least three non-empty seeds are required")
    if len(set(seed_list)) != len(seed_list):
        raise ValueError("seeds must be distinct")

    reports: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    for seed in seed_list:
        report = run_generated_crossdomain_cegis_campaign(root, seed, engine_factory=engine_factory)
        if not report["passed"]:
            raise RuntimeError("generated cross-domain capability failed acquisition")
        reports.append(report)
        retests = [
            _retest_generated_capability(root, prior_seed, prior_report, engine_factory=engine_factory)
            for prior_seed, prior_report in zip(seed_list[: len(reports)], reports, strict=True)
        ]
        if not all(item["score"] == 1.0 and item["trial_budget_unchanged"] for item in retests):
            raise RuntimeError("accumulated capability retention or trial-budget invariance failed")
        stages.append(
            {
                "promoted_candidate_id": report["candidate_id"],
                "family": report["family"],
                "accumulated_capability_count": len(reports),
                "fresh_engine_retests": retests,
            }
        )

    candidate_ids = [str(item["candidate_id"]) for item in reports]
    scopes = [str(item["scope"]) for item in reports]
    families = [str(item["family"]) for item in reports]
    result = {
        "schema_version": 1,
        "campaign_id": f"multicap-crossdomain-{_digest(candidate_ids)[:16]}",
        "capability_count": len(reports),
        "distinct_candidate_count": len(set(candidate_ids)),
        "distinct_scope_count": len(set(scopes)),
        "families": families,
        "distinct_family_count": len(set(families)),
        "stages": stages,
        "all_promotions_passed": all(item["passed"] for item in reports),
        "all_restart_retests_passed": all(
            retest["score"] == 1.0 for stage in stages for retest in stage["fresh_engine_retests"]
        ),
        "all_trial_budgets_unchanged": all(
            retest["trial_budget_unchanged"]
            for stage in stages
            for retest in stage["fresh_engine_retests"]
        ),
        "claim_boundary": (
            "Internal sequential continual-learning evidence only. This campaign does not constitute "
            "independent production evidence and does not prove AGI."
        ),
    }
    result["passed"] = bool(
        result["capability_count"] >= 3
        and result["distinct_candidate_count"] == result["capability_count"]
        and result["distinct_scope_count"] == result["capability_count"]
        and result["distinct_family_count"] >= 3
        and result["all_promotions_passed"]
        and result["all_restart_retests_passed"]
        and result["all_trial_budgets_unchanged"]
    )
    result["digest"] = _digest({key: value for key, value in result.items() if key != "digest"})
    _atomic_json(
        root / ".continual" / "evidence" / "multicap-crossdomain-cegis" / f"{result['campaign_id']}.json",
        result,
    )
    return result
