from __future__ import annotations

from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample
from agi.adaptive_depth_composition import (
    AdaptiveDepthCompositionError,
    run_adaptive_depth_composition,
    synthesize_shallowest_verified_route,
)
from agi.cross_domain_seed_gap_acquisition import run_cross_domain_seed_gap_acquisition
from agi.durable_state_rehydration import _tree_snapshot
from agi.dynamic_skillgraph_input_domains import (
    DynamicSkillGraphInputDomainError,
    _PROBEABLE_DOMAINS,
    _execute_dynamic_target,
    _precommit_dynamic_target_plan,
)
from agi.heterogeneous_retention_campaign import _digest
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.sequence_seed_gap_acquisition import run_sequence_seed_gap_acquisition


class DynamicSkillGraphGrowthError(RuntimeError):
    pass


def _run_dynamic_from_existing_graph(root: Path, seed: str) -> dict[str, Any]:
    """Validate dynamic source-domain targets without rebuilding an already prepared graph."""

    source_ids = _all_candidate_ids(root)
    source_tree = _tree_snapshot(root, source_ids)
    source_trials = _trial_snapshot(root, source_ids)
    if not source_ids or not all(source_trials.get(candidate_id) for candidate_id in source_ids):
        raise DynamicSkillGraphGrowthError(
            "prepared dynamic target graph lacks durable Candidate trial evidence"
        )

    try:
        plan = _precommit_dynamic_target_plan(root, seed)
    except DynamicSkillGraphInputDomainError as exc:
        raise DynamicSkillGraphGrowthError(
            "prepared verified graph still failed dynamic target planning"
        ) from exc
    observed_domains = list(plan["observed_generator_input_domains"])
    if set(observed_domains) != set(_PROBEABLE_DOMAINS):
        raise DynamicSkillGraphGrowthError(
            "prepared graph did not expose numeric, string, and sequence target sources"
        )
    target_domains = [str(item["input_domain"]) for item in plan["target_plan"]]
    if set(target_domains) != set(observed_domains) or len(target_domains) != len(set(target_domains)):
        raise DynamicSkillGraphGrowthError(
            "prepared target plan did not select exactly one target per observed input domain"
        )

    records = [
        _execute_dynamic_target(
            root,
            target,
            plan_commitment=str(plan["target_plan_commitment"]),
            source_ids=source_ids,
            source_tree=source_tree,
            source_trials=source_trials,
        )
        for target in plan["target_plan"]
    ]
    if _tree_snapshot(root, source_ids) != source_tree:
        raise DynamicSkillGraphGrowthError("dynamic target campaign changed source Candidate bytes")
    if _trial_snapshot(root, source_ids) != source_trials:
        raise DynamicSkillGraphGrowthError("dynamic target campaign changed source Candidate trials")

    control_examples = (
        ProgramExample("aa", 7),
        ProgramExample("bbb", -2),
        ProgramExample("c", 11),
    )
    try:
        synthesize_shallowest_verified_route(
            root,
            input_domain="string",
            output_domain="numeric",
            examples=control_examples,
            max_depth=4,
            max_candidates=192,
            max_search_nodes=8192,
            max_behavior_evaluations=4096,
        )
    except AdaptiveDepthCompositionError:
        control_failed_closed = True
    else:
        control_failed_closed = False
    if not control_failed_closed:
        raise DynamicSkillGraphGrowthError(
            "unrelated cross-source control behavior unexpectedly became supported"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "dynamic-skillgraph-input-domain-targets-v1",
        "seed": seed,
        "observed_generator_input_domains": observed_domains,
        "observed_input_domain_count": len(observed_domains),
        "fixed_numeric_sequence_start_set_used": False,
        "target_plan_precommitted_before_solver_execution": bool(
            plan["target_plan_precommitted_before_solver_execution"]
        ),
        "target_plan_commitment": str(plan["target_plan_commitment"]),
        "precommitted_source_order": list(plan["precommitted_source_order"]),
        "target_input_domains": target_domains,
        "target_record_count": len(records),
        "target_records": records,
        "string_source_target_exercised": any(
            record["input_domain"] == "string" for record in records
        ),
        "all_generator_hidden_ids_withheld": all(
            record["generator_hidden_candidate_ids_supplied_to_solver"] is False
            for record in records
        ),
        "all_solver_calls_candidate_id_free": all(
            record["solver_candidate_ids_supplied_by_caller"] is False
            for record in records
        ),
        "all_generated_routes_multistage": all(
            int(record["solver_selected_chain_depth"]) >= 2 for record in records
        ),
        "all_fresh_engine_runs_unique": all(
            bool(record["fresh_engine_runs_unique"]) for record in records
        ),
        "all_candidate_state_unchanged": all(
            bool(record["candidate_state_unchanged"]) for record in records
        ),
        "all_trial_state_unchanged": all(
            bool(record["trial_state_unchanged"]) for record in records
        ),
        "source_candidate_state_unchanged": True,
        "source_trial_state_unchanged": True,
        "unrelated_cross_source_control_failed_closed": control_failed_closed,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded dynamic skill-graph target-source evidence only. Target generation derives "
            "numeric, string, and sequence source domains from the actually verified durable Candidate "
            "graph and exercises one committed multistage target from every observed source without "
            "exposing hidden generator Candidate IDs. Probeable domain types, generation, search, runtime, "
            "regression, and scoring remain repository-authored; this does not establish open-domain "
            "target generation, independent production evaluation, or AGI."
        ),
    }
    if not all(
        (
            report["fixed_numeric_sequence_start_set_used"] is False,
            report["target_plan_precommitted_before_solver_execution"],
            report["observed_input_domain_count"] == 3,
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
            "prepared dynamic skill-graph aggregate invariant failed"
        )
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "dynamic-skillgraph-input-domains"
        / f"dynamic-inputs-{_digest(seed)[:16]}.json",
        report,
    )
    return report


def run_dynamic_skillgraph_growth_campaign(root: Path, seed: str) -> dict[str, Any]:
    """Grow string behavior until the observed graph has a unique multistage string target.

    The original dynamic-source attempt correctly failed because its verified graph exposed no unique
    multistage string-origin behavior. This campaign keeps that boundary intact. It establishes the same
    adaptive and three-domain prerequisites once, then performs bounded counterexample-driven string
    acquisition only while the unchanged dynamic planner still reports the specific missing-string
    capability. Every growth step uses the existing unsupported-gap, exact-scope regression,
    transactional commit, fresh replay, and untouched-control path. The prepared graph is then validated
    directly so repeated prerequisite bootstraps cannot manufacture duplicate semantic implementations
    that make a previously unique route ambiguous.

    All evidence remains bounded repository-authored internal development evidence. This does not prove
    open-domain autonomous learning, evaluator independence, production deployment, or AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()

    adaptive = run_adaptive_depth_composition(
        root,
        f"{seed}:pre-dynamic-base-graph",
    )
    if not adaptive.get("passed"):
        raise DynamicSkillGraphGrowthError(
            "pre-dynamic adaptive base graph did not pass"
        )
    three_domain = run_sequence_seed_gap_acquisition(
        root,
        f"{seed}:pre-dynamic-three-domain",
        max_target_attempts=3,
    )
    if not three_domain.get("passed"):
        raise DynamicSkillGraphGrowthError(
            "pre-dynamic three-domain graph did not pass"
        )

    readiness_failures: list[str] = []
    growth_reports: list[dict[str, Any]] = []
    readiness_seed = f"{seed}:dynamic-readiness"
    for growth_index in range(3):
        try:
            readiness = _precommit_dynamic_target_plan(root, readiness_seed)
        except DynamicSkillGraphInputDomainError as exc:
            reason = str(exc)
            readiness_failures.append(reason)
            prefix = "observed input domains lack unique multistage target semantics: "
            if not reason.startswith(prefix):
                raise DynamicSkillGraphGrowthError(
                    "prepared graph failed outside the intended missing-string boundary"
                ) from exc
            missing = {value for value in reason.removeprefix(prefix).split(",") if value}
            if missing != {"string"}:
                raise DynamicSkillGraphGrowthError(
                    "prepared graph has a non-string dynamic target capability gap"
                ) from exc
        else:
            if "string" not in set(readiness["observed_generator_input_domains"]):
                raise DynamicSkillGraphGrowthError(
                    "ready graph unexpectedly omitted the string source domain"
                )
            break

        string_growth = run_cross_domain_seed_gap_acquisition(
            root,
            f"{seed}:pre-dynamic-string-growth:{growth_index}",
            max_target_attempts=16,
        )
        if not string_growth.get("passed"):
            raise DynamicSkillGraphGrowthError(
                "pre-dynamic string capability growth did not pass"
            )
        growth_reports.append(string_growth)
    else:
        try:
            _precommit_dynamic_target_plan(root, readiness_seed)
        except DynamicSkillGraphInputDomainError as exc:
            raise DynamicSkillGraphGrowthError(
                "bounded string capability growth did not create a unique multistage string target"
            ) from exc

    if not growth_reports:
        raise DynamicSkillGraphGrowthError(
            "known missing-string capability boundary disappeared before any growth step"
        )

    dynamic = _run_dynamic_from_existing_graph(
        root,
        f"{seed}:dynamic-source-validation",
    )
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

    last_growth = growth_reports[-1]
    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "dynamic-skillgraph-growth-campaign-v1",
        "seed": seed,
        "pre_growth_failure_boundary": (
            "the observed verified graph exposes no unique multi-stage string-origin target"
        ),
        "failure_boundary_weakened": False,
        "readiness_failures": readiness_failures,
        "bounded_string_growth_attempt_count": len(growth_reports),
        "string_growth_candidate_ids": [str(item["candidate_id"]) for item in growth_reports],
        "string_growth_candidate_id": str(last_growth["candidate_id"]),
        "string_growth_candidate_negative_evidence_retained": all(
            bool(item["new_candidate_negative_evidence_retained"])
            for item in growth_reports
        ),
        "string_growth_transactional_commit": last_growth["transactional_commit"],
        "string_growth_control_gap_remained_failed_closed": all(
            bool(item["control_gap_remained_failed_closed"])
            for item in growth_reports
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
            "Internal bounded capability-growth and dynamic target-source evidence only. Missing string "
            "capacity is repaired by bounded counterexample-driven acquisition through the existing "
            "unsupported-gap, exact-scope regression, transactional commit, fresh replay, and untouched-"
            "control path; the multistage target requirement is not weakened. Generation, search, runtime, "
            "regression, persistence, and scoring remain repository-authored; this does not establish "
            "open-domain target generation, independent production evaluation, or AGI."
        ),
    }
    if not all(
        (
            report["failure_boundary_weakened"] is False,
            report["bounded_string_growth_attempt_count"] >= 1,
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
