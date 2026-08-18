from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample, execute_program
from agi.adaptive_depth_composition import (
    AdaptiveDepthCompositionError,
    _execute_descriptor_chain,
    _planning_inputs,
    synthesize_shallowest_verified_route,
)
from agi.adaptive_depth_postaddition_retention import (
    run_adaptive_depth_postaddition_retention,
)
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.durable_state_rehydration import _tree_snapshot
from agi.heterogeneous_retention_campaign import _digest
from agi.multi_gap_autonomous_curriculum import _all_candidate_ids, _trial_snapshot
from agi.multi_session_continual_chain import _copy_persistent_state
from agi.verified_macro_synthesis import _load_verified_macro_items


class AdaptiveDepthMultiSeedReplayError(RuntimeError):
    pass


def _behavior_fingerprint(
    items: tuple[dict[str, Any], ...],
    *,
    input_domain: str,
    output_domain: str,
) -> tuple[str, list[dict[str, Any]]]:
    probes = _planning_inputs(input_domain, "adaptive-depth-multiseed-canonical-v1")
    observations = [
        {"input": value, "output": _execute_descriptor_chain(items, value)}
        for value in probes
    ]
    fingerprint = _digest(
        {
            "input_domain": input_domain,
            "output_domain": output_domain,
            "observations": observations,
            "phase": "adaptive-depth-multiseed-behavior-v1",
        }
    )
    return fingerprint, observations


def _later_session_replay(
    seed_root: Path,
    *,
    round_seed: str,
    retention: dict[str, Any],
) -> dict[str, Any]:
    selected_ids = tuple(str(value) for value in retention["initial_selected_candidate_ids"])
    if not selected_ids:
        raise AdaptiveDepthMultiSeedReplayError("retained route is empty")
    selected_items = tuple(_load_verified_macro_items(seed_root, selected_ids))
    input_domain = str(selected_items[0]["input_domain"])
    output_domain = str(selected_items[-1]["output_domain"])
    initial_seed = f"{round_seed}:initial-adaptive"
    planning_inputs = _planning_inputs(input_domain, initial_seed)
    planning_outputs = tuple(
        _execute_descriptor_chain(selected_items, value) for value in planning_inputs
    )
    planning_examples = tuple(
        ProgramExample(value, output)
        for value, output in zip(planning_inputs, planning_outputs, strict=True)
    )

    all_ids = _all_candidate_ids(seed_root)
    durable_tree = _tree_snapshot(seed_root, all_ids)
    durable_trials = _trial_snapshot(seed_root, all_ids)
    if not all(durable_trials.get(candidate_id) for candidate_id in all_ids):
        raise AdaptiveDepthMultiSeedReplayError("later-session source has missing trial evidence")

    with tempfile.TemporaryDirectory(prefix="agi-adaptive-multiseed-session-") as temporary:
        replay_root = Path(temporary).resolve()
        _copy_persistent_state(seed_root, replay_root)
        prior_runs_copied = (replay_root / ".continual" / "runs").exists()
        prior_episodes_copied = (replay_root / ".continual" / "episodes").exists()
        prior_evidence_copied = (replay_root / ".continual" / "evidence").exists()
        if _tree_snapshot(replay_root, all_ids) != durable_tree:
            raise AdaptiveDepthMultiSeedReplayError(
                "later-session reconstruction changed Candidate bytes"
            )
        if _trial_snapshot(replay_root, all_ids) != durable_trials:
            raise AdaptiveDepthMultiSeedReplayError(
                "later-session reconstruction changed Candidate trials"
            )

        original_depth = int(retention["initial_selected_chain_depth"])
        replay = synthesize_shallowest_verified_route(
            replay_root,
            input_domain=input_domain,
            output_domain=output_domain,
            examples=planning_examples,
            max_depth=max(4, original_depth),
            max_candidates=64,
            max_search_nodes=int(retention["replay_search_node_budget"]),
            max_behavior_evaluations=int(retention["replay_behavior_evaluation_budget"]),
        )
        replay_ids = tuple(str(value) for value in replay["selected_candidate_ids"])
        if replay.get("candidate_ids_supplied_by_caller") is not False:
            raise AdaptiveDepthMultiSeedReplayError(
                "later-session replay relied on caller Candidate IDs"
            )
        if replay_ids != selected_ids:
            raise AdaptiveDepthMultiSeedReplayError(
                "later-session replay selected a different retained route"
            )
        if int(replay["selected_chain_depth"]) != original_depth:
            raise AdaptiveDepthMultiSeedReplayError(
                "later-session replay changed the shallowest behavior-sufficient depth"
            )

        challenge_inputs = _planning_inputs(
            input_domain,
            f"{round_seed}:later-session-heldback-v1",
        )
        if any(value in planning_inputs for value in challenge_inputs):
            raise AdaptiveDepthMultiSeedReplayError(
                "later-session challenge overlaps planning inputs"
            )
        expected_outputs = tuple(
            _execute_descriptor_chain(selected_items, value) for value in challenge_inputs
        )
        replay_items = tuple(_load_verified_macro_items(replay_root, replay_ids))
        fresh_run_ids: list[str] = []
        challenge_outputs: list[dict[str, Any]] = []
        for value, expected in zip(challenge_inputs, expected_outputs, strict=True):
            compiled_output = execute_program(replay["compiled_program"], value)
            runtime_output, run_id, refs = _execute_chain(replay_root, replay_items, value)
            if compiled_output != expected or runtime_output != expected:
                raise AdaptiveDepthMultiSeedReplayError(
                    "later-session retained route failed held-back challenge"
                )
            if len(refs) != original_depth:
                raise AdaptiveDepthMultiSeedReplayError(
                    "later-session challenge executed the wrong learned-stage count"
                )
            fresh_run_ids.append(run_id)
            challenge_outputs.append(
                {"input": value, "expected": expected, "output": runtime_output}
            )
        if len(set(fresh_run_ids)) != len(fresh_run_ids):
            raise AdaptiveDepthMultiSeedReplayError(
                "later-session held-back challenges reused an Engine run"
            )

        unsupported_failed_closed = False
        try:
            synthesize_shallowest_verified_route(
                replay_root,
                input_domain="string",
                output_domain="numeric",
                examples=(
                    ProgramExample("later-session-unseen-a", 1),
                    ProgramExample("later-session-unseen-b", 2),
                ),
                max_depth=3,
                max_candidates=64,
                max_search_nodes=64,
                max_behavior_evaluations=32,
            )
        except AdaptiveDepthCompositionError as exc:
            unsupported_failed_closed = "no unique behavior-matching verified route" in str(exc)
        if not unsupported_failed_closed:
            raise AdaptiveDepthMultiSeedReplayError(
                "later-session unsupported string-to-numeric gap did not fail closed"
            )

        if _tree_snapshot(replay_root, all_ids) != durable_tree:
            raise AdaptiveDepthMultiSeedReplayError(
                "later-session replay changed Candidate bytes"
            )
        if _trial_snapshot(replay_root, all_ids) != durable_trials:
            raise AdaptiveDepthMultiSeedReplayError(
                "later-session replay changed Candidate trials"
            )

    if _tree_snapshot(seed_root, all_ids) != durable_tree:
        raise AdaptiveDepthMultiSeedReplayError(
            "later-session validation mutated source Candidate bytes"
        )
    if _trial_snapshot(seed_root, all_ids) != durable_trials:
        raise AdaptiveDepthMultiSeedReplayError(
            "later-session validation mutated source Candidate trials"
        )

    fingerprint, canonical_observations = _behavior_fingerprint(
        selected_items,
        input_domain=input_domain,
        output_domain=output_domain,
    )
    return {
        "round_seed": round_seed,
        "round_seed_commitment": _digest(
            {"round_seed": round_seed, "phase": "adaptive-depth-multiseed-seed-v1"}
        ),
        "retention_digest": str(retention["digest"]),
        "selected_candidate_ids": list(selected_ids),
        "selected_chain_depth": original_depth,
        "selected_shortest_type_chain_depth": int(
            retention["initial_shortest_type_chain_depth"]
        ),
        "input_domain": input_domain,
        "output_domain": output_domain,
        "behavior_fingerprint": fingerprint,
        "canonical_behavior_observations": canonical_observations,
        "caller_supplied_candidate_ids": False,
        "replay_selected_candidate_ids": list(replay_ids),
        "replay_selected_chain_depth": int(replay["selected_chain_depth"]),
        "replay_depths_attempted": replay["depths_attempted"],
        "minimal_depth_retained": int(replay["selected_chain_depth"]) == original_depth,
        "route_identity_retained": replay_ids == selected_ids,
        "heldback_challenge_outputs": challenge_outputs,
        "fresh_engine_runs": fresh_run_ids,
        "fresh_engine_runs_unique": len(set(fresh_run_ids)) == len(fresh_run_ids),
        "unsupported_gap_failed_closed": unsupported_failed_closed,
        "candidate_state_unchanged": True,
        "trial_state_unchanged": True,
        "prior_runs_copied": prior_runs_copied,
        "prior_episodes_copied": prior_episodes_copied,
        "prior_evidence_copied": prior_evidence_copied,
    }


def run_adaptive_depth_multiseed_replay(
    root: Path,
    seed: str,
    *,
    max_seed_attempts: int = 4,
    required_distinct_behaviors: int = 2,
) -> dict[str, Any]:
    """Require diverse seed-selected adaptive routes to survive another durable-state-only session.

    A deterministic seed schedule is committed before execution. Each seed is evaluated in an isolated
    state-only reconstruction of the same source root, runs the post-addition retention milestone, then
    undergoes an additional later-session reconstruction. The later session must rediscover the same
    route at the same shallowest behavior-sufficient depth without caller Candidate IDs, pass held-back
    challenges in fresh Engine runs, preserve Candidate bytes/trials, and keep unsupported behavior
    fail-closed. The campaign succeeds only if the bounded precommitted seed schedule exposes at least
    two distinct route behaviors; otherwise the lack of diversity is retained as falsification rather
    than being relabeled as success.

    This is repository-authored bounded internal development evidence only. It does not establish
    independent production evaluation or AGI.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    if (
        not isinstance(max_seed_attempts, int)
        or isinstance(max_seed_attempts, bool)
        or not 2 <= max_seed_attempts <= 6
    ):
        raise ValueError("max_seed_attempts must be an integer in [2, 6]")
    if (
        not isinstance(required_distinct_behaviors, int)
        or isinstance(required_distinct_behaviors, bool)
        or not 2 <= required_distinct_behaviors <= max_seed_attempts
    ):
        raise ValueError(
            "required_distinct_behaviors must be an integer in [2, max_seed_attempts]"
        )

    root = root.resolve()
    source_ids = _all_candidate_ids(root)
    source_tree = _tree_snapshot(root, source_ids)
    source_trials = _trial_snapshot(root, source_ids)
    seed_schedule = [f"{seed}:round-{index}" for index in range(max_seed_attempts)]
    seed_schedule_commitment = _digest(
        {
            "seed": seed,
            "seed_schedule": seed_schedule,
            "required_distinct_behaviors": required_distinct_behaviors,
            "phase": "adaptive-depth-multiseed-schedule-v1",
        }
    )

    records: list[dict[str, Any]] = []
    behavior_fingerprints: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="agi-adaptive-multiseed-") as temporary:
        campaign_root = Path(temporary).resolve()
        for index, round_seed in enumerate(seed_schedule):
            seed_root = campaign_root / f"seed-{index}"
            _copy_persistent_state(root, seed_root)
            if _tree_snapshot(seed_root, source_ids) != source_tree:
                raise AdaptiveDepthMultiSeedReplayError(
                    "seed reconstruction changed source Candidate bytes"
                )
            if _trial_snapshot(seed_root, source_ids) != source_trials:
                raise AdaptiveDepthMultiSeedReplayError(
                    "seed reconstruction changed source Candidate trials"
                )

            retention = run_adaptive_depth_postaddition_retention(seed_root, round_seed)
            if not retention.get("passed"):
                raise AdaptiveDepthMultiSeedReplayError(
                    "seed post-addition retention campaign did not pass"
                )
            record = _later_session_replay(
                seed_root,
                round_seed=round_seed,
                retention=retention,
            )
            records.append(record)
            behavior_fingerprints.add(str(record["behavior_fingerprint"]))
            if len(records) >= 2 and len(behavior_fingerprints) >= required_distinct_behaviors:
                break

    if len(behavior_fingerprints) < required_distinct_behaviors:
        raise AdaptiveDepthMultiSeedReplayError(
            "bounded precommitted seed schedule exposed insufficient route behavior diversity"
        )
    if _tree_snapshot(root, source_ids) != source_tree:
        raise AdaptiveDepthMultiSeedReplayError(
            "multi-seed validation mutated source Candidate bytes"
        )
    if _trial_snapshot(root, source_ids) != source_trials:
        raise AdaptiveDepthMultiSeedReplayError(
            "multi-seed validation mutated source Candidate trials"
        )

    route_identities = {
        _digest(
            {
                "candidate_ids": record["selected_candidate_ids"],
                "input_domain": record["input_domain"],
                "output_domain": record["output_domain"],
            }
        )
        for record in records
    }
    selected_depths = [int(record["selected_chain_depth"]) for record in records]
    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "adaptive-depth-multiseed-later-session-replay-v1",
        "seed_schedule": seed_schedule,
        "seed_schedule_commitment": seed_schedule_commitment,
        "seed_attempt_count": len(records),
        "max_seed_attempts": max_seed_attempts,
        "required_distinct_behaviors": required_distinct_behaviors,
        "distinct_behavior_fingerprint_count": len(behavior_fingerprints),
        "distinct_route_identity_count": len(route_identities),
        "selected_depths": selected_depths,
        "distinct_selected_depth_count": len(set(selected_depths)),
        "multi_seed_behavior_diversity_observed": True,
        "all_routes_minimal_depth_retained": all(
            bool(record["minimal_depth_retained"]) for record in records
        ),
        "all_route_identities_retained": all(
            bool(record["route_identity_retained"]) for record in records
        ),
        "all_unsupported_gaps_failed_closed": all(
            bool(record["unsupported_gap_failed_closed"]) for record in records
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
        "seed_records": records,
        "live_model_invocation_required": False,
        "claim_boundary": (
            "Internal bounded multi-seed adaptive-depth retention evidence only. A precommitted seed "
            "schedule exposed multiple route behaviors, and each observed route was rediscovered in an "
            "additional durable-state-only session at the same shallowest behavior-sufficient depth "
            "without caller Candidate IDs while held-back challenges passed, unsupported behavior "
            "failed closed, and Candidate bytes/trial ledgers remained unchanged. Repository-authored "
            "generation, search, regression, runtime, and scoring do not establish independent "
            "production evidence or AGI."
        ),
    }
    if not all(
        (
            report["multi_seed_behavior_diversity_observed"],
            report["distinct_behavior_fingerprint_count"] >= required_distinct_behaviors,
            report["distinct_route_identity_count"] >= 2,
            report["all_routes_minimal_depth_retained"],
            report["all_route_identities_retained"],
            report["all_unsupported_gaps_failed_closed"],
            report["all_fresh_engine_runs_unique"],
            report["all_candidate_state_unchanged"],
            report["all_trial_state_unchanged"],
            report["source_candidate_state_unchanged"],
            report["source_trial_state_unchanged"],
            all(not record["prior_runs_copied"] for record in records),
            all(not record["prior_episodes_copied"] for record in records),
            all(not record["prior_evidence_copied"] for record in records),
        )
    ):
        raise AdaptiveDepthMultiSeedReplayError(
            "multi-seed later-session replay aggregate invariant failed"
        )

    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "adaptive-depth-multiseed-replay"
        / f"multiseed-{_digest(seed)[:16]}.json",
        report,
    )
    return report
