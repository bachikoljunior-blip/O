from __future__ import annotations

from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.cross_domain_seed_gap_acquisition import run_cross_domain_seed_gap_acquisition
from agi.dynamic_skillgraph_input_domains import (
    DynamicSkillGraphInputDomainError,
    run_dynamic_skillgraph_input_domains,
)
from agi.heterogeneous_retention_campaign import _digest


class DynamicSkillGraphGrowthError(RuntimeError):
    pass


def run_dynamic_skillgraph_growth_campaign(root: Path, seed: str) -> dict[str, Any]:
    """Grow a second verified string skill before testing dynamic source-domain generation.

    The first dynamic-source attempt correctly failed because a graph with only one verified string-origin
    skill cannot expose a unique multi-stage string-origin behavior. This campaign preserves that negative
    result as a capability-gap observation rather than weakening the milestone. It first runs the existing
    seed-committed cross-domain acquisition path with a distinct seed so the library must acquire another
    exact-scope string behavior only after it is shown unsupported, regression-gate it, transactionally
    commit it, replay it from fresh durable state, and retain its numeric prerequisite. The dynamic target
    generator then derives its source domains from the enlarged verified graph and must exercise numeric,
    string, and sequence multi-stage targets without caller Candidate IDs.

    All evidence remains bounded repository-authored internal development evidence. This does not prove
    open-domain autonomous learning, evaluator independence, production deployment, or AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    string_growth = run_cross_domain_seed_gap_acquisition(
        root,
        f"{seed}:pre-dynamic-string-growth",
        max_target_attempts=16,
    )
    if not string_growth.get("passed"):
        raise DynamicSkillGraphGrowthError(
            "pre-dynamic string capability growth did not pass"
        )
    growth_candidate_id = str(string_growth["candidate_id"])

    try:
        dynamic = run_dynamic_skillgraph_input_domains(
            root,
            f"{seed}:dynamic-source-validation",
        )
    except DynamicSkillGraphInputDomainError as exc:
        raise DynamicSkillGraphGrowthError(
            "enlarged verified graph still failed the dynamic source-domain milestone"
        ) from exc
    if not dynamic.get("passed"):
        raise DynamicSkillGraphGrowthError("dynamic source-domain validation did not pass")
    if "string" not in set(dynamic["observed_generator_input_domains"]):
        raise DynamicSkillGraphGrowthError(
            "enlarged graph failed to expose the string source domain"
        )
    if not dynamic.get("string_source_target_exercised"):
        raise DynamicSkillGraphGrowthError(
            "enlarged graph did not exercise a string-origin generated target"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "dynamic-skillgraph-growth-campaign-v1",
        "seed": seed,
        "pre_growth_failure_boundary": (
            "a single verified string-origin skill exposes no unique multi-stage string-origin target"
        ),
        "failure_boundary_weakened": False,
        "string_growth_candidate_id": growth_candidate_id,
        "string_growth_candidate_negative_evidence_retained": bool(
            string_growth["new_candidate_negative_evidence_retained"]
        ),
        "string_growth_transactional_commit": string_growth["transactional_commit"],
        "string_growth_control_gap_remained_failed_closed": bool(
            string_growth["control_gap_remained_failed_closed"]
        ),
        "dynamic_report_digest": str(dynamic["digest"]),
        "observed_generator_input_domains": list(
            dynamic["observed_generator_input_domains"]
        ),
        "fixed_numeric_sequence_start_set_used": bool(
            dynamic["fixed_numeric_sequence_start_set_used"]
        ),
        "target_plan_precommitted_before_solver_execution": bool(
            dynamic["target_plan_precommitted_before_solver_execution"]
        ),
        "string_source_target_exercised": bool(
            dynamic["string_source_target_exercised"]
        ),
        "all_generator_hidden_ids_withheld": bool(
            dynamic["all_generator_hidden_ids_withheld"]
        ),
        "all_solver_calls_candidate_id_free": bool(
            dynamic["all_solver_calls_candidate_id_free"]
        ),
        "all_generated_routes_multistage": bool(
            dynamic["all_generated_routes_multistage"]
        ),
        "all_fresh_engine_runs_unique": bool(
            dynamic["all_fresh_engine_runs_unique"]
        ),
        "all_candidate_state_unchanged": bool(
            dynamic["all_candidate_state_unchanged"]
        ),
        "all_trial_state_unchanged": bool(dynamic["all_trial_state_unchanged"]),
        "source_candidate_state_unchanged": bool(
            dynamic["source_candidate_state_unchanged"]
        ),
        "source_trial_state_unchanged": bool(
            dynamic["source_trial_state_unchanged"]
        ),
        "unrelated_cross_source_control_failed_closed": bool(
            dynamic["unrelated_cross_source_control_failed_closed"]
        ),
        "target_records": dynamic["target_records"],
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded capability-growth and dynamic target-source evidence only. The prior "
            "single-string-skill failure is retained and the missing graph capacity is repaired through "
            "the same unsupported-gap acquisition, exact-scope regression, transactional commit, and "
            "fresh replay path rather than by weakening the multi-stage target requirement. Generation, "
            "search, runtime, regression, persistence, and scoring remain repository-authored; this does "
            "not establish open-domain target generation, independent production evaluation, or AGI."
        ),
    }
    if not all(
        (
            report["failure_boundary_weakened"] is False,
            report["string_growth_candidate_negative_evidence_retained"],
            report["string_growth_control_gap_remained_failed_closed"],
            report["fixed_numeric_sequence_start_set_used"] is False,
            report["target_plan_precommitted_before_solver_execution"],
            report["string_source_target_exercised"],
            report["all_generator_hidden_ids_withheld"],
            report["all_solver_calls_candidate_id_free"],
            report["all_generated_routes_multistage"],
            report["all_fresh_engine_runs_unique"],
            report["all_candidate_state_unchanged"],
            report["all_trial_state_unchanged"],
            report["source_candidate_state_unchanged"],
            report["source_trial_state_unchanged"],
            report["unrelated_cross_source_control_failed_closed"],
        )
    ):
        raise DynamicSkillGraphGrowthError(
            "dynamic skill-graph growth aggregate invariant failed"
        )

    report["digest"] = _digest(
        {key: value for key, value in report.items() if key != "digest"}
    )
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "dynamic-skillgraph-growth-campaign"
        / f"growth-{_digest(seed)[:16]}.json",
        report,
    )
    return report
