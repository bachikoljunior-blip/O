from __future__ import annotations

from pathlib import Path
from typing import Any

from agi.acquired_program_runtime import _atomic_json
from agi.acquired_programs import ProgramExample
from agi.behavior_guided_tool_chain_discovery import _execute_chain
from agi.cross_context_memory_transfer import (
    CrossContextMemoryTransferError,
    resolve_verified_macro_with_related_memory,
)
from agi.heterogeneous_retention_campaign import _digest, _promote_task
from agi.materialized_runtime_replay import (
    _candidate_trial_snapshot,
    _invoke,
    _mechanical_engine,
    _new_runtime_run,
    _validate_runtime_output,
)
from agi.memory_first_macro_materialization import run_memory_first_macro_materialization
from agi.multiround_disambiguation_bank import (
    _bank_path,
    _base_support,
    _has_either_spec,
    _has_key_spec,
    _support_payload,
    _target_answer,
    bank_examples,
)
from agi.verified_macro_synthesis import _load_verified_macro_items


class CrossContextMemorySkillTransferError(RuntimeError):
    pass


def _target_support() -> tuple[ProgramExample, ...]:
    """A distinct support set on which has-a, has-b, and has-either remain ambiguous."""

    return (
        ProgramExample({"a": "target-a", "b": "target-b"}, True),
        ProgramExample({"a": None, "b": 0, "context": "target"}, True),
        ProgramExample({"a": [], "b": {}}, True),
    )


def _contract() -> dict[str, Any]:
    return {
        "family": "object-key-presence",
        "semantic_contract": "returns-true-exactly-when-key-a-is-present",
        "version": 1,
    }


def run_cross_context_memory_skill_transfer(root: Path, seed: str) -> dict[str, Any]:
    """Transfer exact-source memory and its verified skill to a related fresh task context.

    A source campaign first learns two committed counterexamples, resolves has-key-a, compiles the route
    into a separate exact-scope regression-promoted acquired skill, rejects an adverse protected
    regression, and replays the skill through fresh Engines. The related target task uses a different
    task key and different base-support digest. Its candidate pool contains that already verified source
    skill plus fresh verified has-key-b and has-a-or-b distractors.

    The related-memory resolver may read the source bank only through an explicit matching semantic
    contract. It must validate the source bank against its own exact support, behaviorally re-test each
    source counterexample against the target routes, reduce ambiguity 3→2→1 without an oracle, and
    select the previously materialized source skill. A mismatched contract must fail closed without
    creating target memory. The selected skill is then replayed on related fresh challenges through new
    Engines while source-bank bytes and every Candidate trial ledger remain unchanged.

    This is internal bounded transfer, retention, and skill-reuse evidence only. All contracts, tasks,
    source evidence, regressions, challenges, and runtimes are repository-authored; it is not AGI or
    independent production evidence.
    """

    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("seed must be non-empty")
    root = root.resolve()
    token = _digest({"campaign": "cross-context-memory-skill-transfer-v1", "seed": seed})[:16]

    source_report = run_memory_first_macro_materialization(root, f"{seed}:source")
    if not source_report["passed"] or not source_report["compiled_candidate_promoted"]:
        raise CrossContextMemorySkillTransferError(
            "source memory-materialized skill did not reach verified promotion"
        )
    if source_report["durable_counterexample_count"] != 2:
        raise CrossContextMemorySkillTransferError(
            "source campaign did not retain exactly two counterexamples"
        )
    source_task_key = str(source_report["task_key"])
    source_candidate_id = str(source_report["compiled_candidate_id"])
    source_support = _base_support()
    source_bank_examples = bank_examples(
        root,
        task_key=source_task_key,
        input_domain="object",
        output_domain="boolean",
        support=source_support,
    )
    if len(source_bank_examples) != 2:
        raise CrossContextMemorySkillTransferError(
            "source counterexample bank did not reproduce two exact-context examples"
        )
    source_bank_path = _bank_path(root, source_task_key)
    source_bank_before = source_bank_path.read_bytes()

    source_item = _load_verified_macro_items(root, [source_candidate_id])[0]
    has_b = _promote_task(
        root,
        seed=f"{seed}:target-has-b",
        spec=_has_key_spec("b"),
    )
    has_either = _promote_task(
        root,
        seed=f"{seed}:target-has-either",
        spec=_has_either_spec(),
    )
    distractor_ids = [str(has_b["candidate_id"]), str(has_either["candidate_id"])]
    distractors = _load_verified_macro_items(root, distractor_ids)
    candidates = (source_item, *distractors)
    candidate_ids = [str(item["candidate_id"]) for item in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise CrossContextMemorySkillTransferError(
            "related candidate pool reused an identity"
        )
    candidate_trials_before = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in candidate_ids
    }
    if not all(candidate_trials_before.values()):
        raise CrossContextMemorySkillTransferError(
            "related candidate pool lacks durable exact-scope regression evidence"
        )

    target_task_key = f"related-memory-has-a-{token}"
    target_support = _target_support()
    source_support_sha256 = _digest(_support_payload(source_support))
    target_support_sha256 = _digest(_support_payload(target_support))
    if source_support_sha256 == target_support_sha256:
        raise CrossContextMemorySkillTransferError(
            "related target accidentally reused the exact source support"
        )
    target_bank_path = _bank_path(root, target_task_key)
    if target_bank_path.exists():
        raise CrossContextMemorySkillTransferError(
            "related target exact-memory identity already exists"
        )

    contract = _contract()
    source_reference = {
        "task_key": source_task_key,
        "input_domain": "object",
        "output_domain": "boolean",
        "support": source_support,
        "transfer_contract": contract,
    }

    def forbidden_oracle(_value: Any) -> Any:
        raise CrossContextMemorySkillTransferError(
            "compatible related memory should resolve the target without a new oracle"
        )

    resolved = resolve_verified_macro_with_related_memory(
        root,
        candidates=candidates,
        task_key=target_task_key,
        input_domain="object",
        output_domain="boolean",
        support=target_support,
        transfer_contract=contract,
        related_sources=(source_reference,),
        max_depth=1,
        max_related_sources=1,
        max_transferred_examples=4,
        max_rounds=0,
        oracle=forbidden_oracle,
    )
    transfer_report = resolved["report"]
    selected = tuple(resolved["selected"])
    selected_ids = [str(item["candidate_id"]) for item in selected]
    if transfer_report["initial_matching_route_count"] != 3:
        raise CrossContextMemorySkillTransferError(
            "related target was not initially ambiguous across three verified routes"
        )
    if transfer_report["exact_counterexample_count"] != 0:
        raise CrossContextMemorySkillTransferError(
            "related target unexpectedly used exact-context memory"
        )
    if transfer_report["route_counts_during_related_transfer"] != [3, 2, 1]:
        raise CrossContextMemorySkillTransferError(
            "related source memory did not monotonically isolate the target route"
        )
    if transfer_report["accepted_related_counterexample_count"] != 2:
        raise CrossContextMemorySkillTransferError(
            "related target did not accept exactly two source counterexamples"
        )
    if transfer_report["oracle_query_count"] != 0:
        raise CrossContextMemorySkillTransferError(
            "related target unexpectedly queried an oracle"
        )
    if selected_ids != [source_candidate_id]:
        raise CrossContextMemorySkillTransferError(
            "related target did not select the verified source materialized skill"
        )
    if target_bank_path.exists():
        raise CrossContextMemorySkillTransferError(
            "zero-oracle related transfer incorrectly created exact target memory"
        )

    incompatible_task_key = f"incompatible-memory-has-a-{token}"
    incompatible_bank_path = _bank_path(root, incompatible_task_key)
    if incompatible_bank_path.exists():
        raise CrossContextMemorySkillTransferError(
            "incompatible target exact-memory identity already exists"
        )
    incompatible_contract = {
        "family": "object-key-truthiness",
        "semantic_contract": "returns-truthiness-of-key-a-value",
        "version": 1,
    }
    incompatible_failed_closed = False
    incompatible_failure = ""
    try:
        resolve_verified_macro_with_related_memory(
            root,
            candidates=candidates,
            task_key=incompatible_task_key,
            input_domain="object",
            output_domain="boolean",
            support=target_support,
            transfer_contract=incompatible_contract,
            related_sources=(source_reference,),
            max_depth=1,
            max_related_sources=1,
            max_transferred_examples=4,
            max_rounds=0,
            oracle=None,
        )
    except CrossContextMemoryTransferError as exc:
        incompatible_failure = str(exc)
        incompatible_failed_closed = "no compatible related memory source" in incompatible_failure
    if not incompatible_failed_closed:
        raise CrossContextMemorySkillTransferError(
            "semantic-contract mismatch did not fail closed"
        )
    if incompatible_bank_path.exists():
        raise CrossContextMemorySkillTransferError(
            "incompatible transfer created target exact-memory state"
        )

    challenge_values = (
        {},
        {"a": 1},
        {"b": 1},
        {"a": False, "z": 1},
        {"z": 1},
    )
    fresh_engine_runs: list[str] = []
    replay_outputs: list[dict[str, Any]] = []
    for repeat_index in range(3):
        for case_index, value in enumerate(challenge_values):
            expected = _target_answer(value)
            selected_output, _selected_run, _selected_refs = _execute_chain(root, selected, value)
            engine = _mechanical_engine(root)
            run_id = _new_runtime_run(engine)
            response = _invoke(
                engine,
                run_id,
                scope=str(source_item["scope"]),
                tool_id=str(source_item["tool_id"]),
                value=value,
            )
            runtime_result = _validate_runtime_output(
                response,
                expected=expected,
                candidate_id=source_candidate_id,
            )
            runtime_output = runtime_result["output"]
            if selected_output != expected or runtime_output != expected:
                raise CrossContextMemorySkillTransferError(
                    "related selected route or fresh Engine replay diverged from target behavior"
                )
            fresh_engine_runs.append(run_id)
            replay_outputs.append(
                {
                    "repeat_index": repeat_index,
                    "case_index": case_index,
                    "input_sha256": _digest(value),
                    "expected": expected,
                    "selected_route_output": selected_output,
                    "fresh_engine_output": runtime_output,
                }
            )

    candidate_trials_after = {
        candidate_id: _candidate_trial_snapshot(root, candidate_id)
        for candidate_id in candidate_ids
    }
    if candidate_trials_after != candidate_trials_before:
        raise CrossContextMemorySkillTransferError(
            "related transfer or replay changed Candidate trial evidence"
        )
    if not source_bank_path.is_file() or source_bank_path.read_bytes() != source_bank_before:
        raise CrossContextMemorySkillTransferError(
            "related transfer or replay changed source bank bytes"
        )
    if target_bank_path.exists() or incompatible_bank_path.exists():
        raise CrossContextMemorySkillTransferError(
            "related zero-oracle checks created target-specific counterexample banks"
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "passed": True,
        "campaign_kind": "cross-context-memory-skill-transfer-v1",
        "source_task_key": source_task_key,
        "target_task_key": target_task_key,
        "source_and_target_task_keys_distinct": source_task_key != target_task_key,
        "source_support_sha256": source_support_sha256,
        "target_support_sha256": target_support_sha256,
        "source_and_target_support_digests_differ": (
            source_support_sha256 != target_support_sha256
        ),
        "transfer_contract": contract,
        "transfer_contract_sha256": transfer_report["target_contract_sha256"],
        "source_counterexample_count": len(source_bank_examples),
        "source_materialized_candidate_id": source_candidate_id,
        "source_materialized_scope": source_item["scope"],
        "source_materialized_candidate_promoted": bool(
            source_report["compiled_candidate_promoted"]
        ),
        "source_adverse_regression_rejected": bool(
            source_report["adverse_compiled_regression_rejected"]
        ),
        "source_fresh_engine_run_count": int(source_report["fresh_engine_run_count"]),
        "related_candidate_ids": candidate_ids,
        "related_distractor_candidate_ids": distractor_ids,
        "related_initial_matching_route_count": transfer_report[
            "initial_matching_route_count"
        ],
        "related_exact_counterexample_count": transfer_report[
            "exact_counterexample_count"
        ],
        "related_accepted_counterexample_count": transfer_report[
            "accepted_related_counterexample_count"
        ],
        "related_route_counts": transfer_report[
            "route_counts_during_related_transfer"
        ],
        "related_oracle_query_count": transfer_report["oracle_query_count"],
        "related_selected_candidate_ids": selected_ids,
        "verified_source_skill_reused_for_related_task": selected_ids == [source_candidate_id],
        "incompatible_contract_failed_closed": incompatible_failed_closed,
        "incompatible_failure": incompatible_failure,
        "target_exact_bank_created": target_bank_path.exists(),
        "incompatible_exact_bank_created": incompatible_bank_path.exists(),
        "source_bank_state_unchanged": source_bank_path.read_bytes() == source_bank_before,
        "candidate_trial_state_unchanged": candidate_trials_after == candidate_trials_before,
        "related_fresh_engine_run_count": len(fresh_engine_runs),
        "related_fresh_engine_run_ids_unique": len(fresh_engine_runs)
        == len(set(fresh_engine_runs)),
        "fresh_challenge_case_count": len(challenge_values),
        "all_fresh_outputs_equal": all(
            item["expected"]
            == item["selected_route_output"]
            == item["fresh_engine_output"]
            for item in replay_outputs
        ),
        "transfer_resolver_digest": transfer_report["digest"],
        "claim_boundary": (
            "Internal bounded related-context transfer and verified-skill reuse evidence only. The "
            "source exact bank, semantic contract, Candidates, regressions, tasks, challenges, and "
            "fresh mechanical Engines remain repository-authored. No target oracle was queried and no "
            "target exact bank was created, but this does not establish AGI, evaluator independence, "
            "or production robustness."
        ),
    }
    report["digest"] = _digest({key: value for key, value in report.items() if key != "digest"})
    _atomic_json(
        root
        / ".continual"
        / "evidence"
        / "cross-context-memory-skill-transfer"
        / f"cross-context-memory-skill-transfer-{token}.json",
        report,
    )
    return report
