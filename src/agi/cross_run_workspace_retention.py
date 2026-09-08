from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .benchmark import AgentState
from .evaluation import CRITERION_KEYS
from .workspace import (
    WORKSPACE_TOOLS,
    SandboxWorkspace,
    ToolEvent,
    WorkspaceAction,
    WorkspaceAgent,
    WorkspaceReport,
    WorkspaceTask,
    WorkspaceTaskResult,
    _evaluate_task,
    run_workspace_suite,
)


class CrossRunWorkspaceRetentionError(RuntimeError):
    pass


def cross_run_retention_probe_suite() -> tuple[WorkspaceTask, ...]:
    """Probe previously acquired workspace state without re-teaching it.

    The probe intentionally omits the learned value and procedure from task files. A fresh agent
    instance must use the explicitly persisted AgentState produced by the acquisition run. Two
    protected non-learning tasks are repeated to detect regressions while retained state is used.
    """

    return (
        WorkspaceTask(
            task_id="workspace-cross-run-memory-recall",
            criterion="continual_learning",
            domain="workspace-cross-run-memory-retention",
            goal=(
                "Read query.json. Without calling remember, use the already persistent memory "
                "namespace and key named by the query, then write that stored value itself as JSON "
                "to answer.json. The query file does not contain the stored value."
            ),
            files={"query.json": json.dumps({"namespace": "labels", "key": "azure"})},
            expected_json_files={"answer.json": 7},
            required_memory={"labels": {"azure": 7}},
            max_steps=6,
        ),
        WorkspaceTask(
            task_id="workspace-cross-run-procedure-reuse",
            criterion="self_improvement",
            domain="workspace-cross-run-procedure-retention",
            goal=(
                "Use the already persistent sorting procedure on values.json. Do not call "
                "adopt_procedure. Write the resulting ascending numeric JSON array to answer.json."
            ),
            files={"values.json": "[31, 4, 12, 2]"},
            expected_json_files={"answer.json": [2, 4, 12, 31]},
            required_procedure={"sorting": "numeric_ascending"},
            max_steps=6,
        ),
        WorkspaceTask(
            task_id="workspace-cross-run-protected-transfer",
            criterion="transfer",
            domain="workspace-cross-run-protected-symbolic-transfer",
            goal=(
                "Infer the transformation demonstrated in examples.json, apply it to query.txt, "
                "and write only the transformed string to answer.txt."
            ),
            files={
                "examples.json": '[["AB", "BAA"], ["DOG", "GODD"]]',
                "query.txt": "CAT",
            },
            expected_text_files={"answer.txt": "TACC"},
            max_steps=6,
        ),
        WorkspaceTask(
            task_id="workspace-cross-run-protected-robustness",
            criterion="robustness",
            domain="workspace-cross-run-protected-data-quality",
            goal=(
                "Read values.json. Keep only unique positive integers, sort ascending, and write the "
                "JSON array to answer.json. Booleans are not valid integers for this task."
            ),
            files={"values.json": '[3, null, "x", -2, 1, 3, 2, 0, 2, true]'},
            expected_json_files={"answer.json": [1, 2, 3]},
            max_steps=6,
        ),
    )


def _state_payload(state: AgentState) -> dict[str, Any]:
    return {
        "memory": deepcopy(state.memory),
        "procedure": deepcopy(state.procedure),
        "tool_failures_seen": int(state.tool_failures_seen),
    }


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _run_task_sequence(
    agent: WorkspaceAgent,
    tasks: Iterable[WorkspaceTask],
    *,
    state: AgentState,
    suite_id: str,
) -> WorkspaceReport:
    task_list = tuple(tasks)
    if not task_list:
        raise ValueError("retention probe requires at least one task")
    ids: set[str] = set()
    validation_errors: list[str] = []
    for task in task_list:
        validation_errors.extend(
            f"{task.task_id or '<missing>'}: {error}" for error in task.validate()
        )
        if task.task_id in ids:
            validation_errors.append(f"duplicate task_id: {task.task_id}")
        ids.add(task.task_id)
    if validation_errors:
        raise ValueError("invalid retention probe: " + "; ".join(validation_errors))

    results: list[WorkspaceTaskResult] = []
    for task in task_list:
        workspace = SandboxWorkspace(task, state)
        trace: list[ToolEvent] = []
        final_action: WorkspaceAction | None = None
        agent_error: str | None = None
        observation: dict[str, Any] = {
            "status": "start",
            "visible_files": workspace.visible_files(),
            "available_tools": list(WORKSPACE_TOOLS),
            "max_steps": task.max_steps,
        }
        for step in range(1, task.max_steps + 1):
            try:
                action = agent.act(task, state, observation, tuple(trace))
            except Exception as exc:
                agent_error = f"{type(exc).__name__}: {exc}"
                break
            if not isinstance(action, WorkspaceAction):
                agent_error = (
                    f"agent {agent.name} returned {type(action).__name__}, expected WorkspaceAction"
                )
                break
            action_errors = action.validate()
            if action_errors:
                agent_error = "; ".join(action_errors)
                break
            if action.kind == "final":
                final_action = action
                break
            event = workspace.execute(action, step)
            trace.append(event)
            observation = event.to_dict()

        checks = _evaluate_task(task, workspace, final_action, trace, agent_error)
        error = agent_error or ("; ".join(workspace.policy_violations) or None)
        results.append(
            WorkspaceTaskResult(
                task_id=task.task_id,
                criterion=task.criterion,
                domain=task.domain,
                success=all(checks.values()),
                checks=checks,
                final_answer=(final_action.answer if final_action is not None else None),
                visible_files=workspace.visible_snapshot(),
                trace=tuple(trace),
                error=error,
            )
        )

    serializable = [asdict(result) for result in results]
    coverage = {key: sum(result.criterion == key for result in results) for key in CRITERION_KEYS}
    return WorkspaceReport(
        suite_id=suite_id,
        agent=agent.name,
        task_results=tuple(results),
        coverage=coverage,
        final_memory=json.loads(json.dumps(state.memory, ensure_ascii=False, default=str)),
        final_procedure=json.loads(json.dumps(state.procedure, ensure_ascii=False, default=str)),
        passed=all(result.success for result in results),
        digest=_digest(serializable),
    )


def _state_seed_complete(state: AgentState) -> bool:
    labels = state.memory.get("labels")
    return (
        isinstance(labels, dict)
        and labels.get("azure") == 7
        and labels.get("amber") == 3
        and state.procedure.get("sorting") == "numeric_ascending"
    )


def run_cross_run_workspace_retention(
    *,
    acquisition_agent_factory: Callable[[], WorkspaceAgent],
    retention_agent_factory: Callable[[], WorkspaceAgent] | None = None,
) -> dict[str, Any]:
    """Acquire state in one agent instance and verify it in a fresh agent instance.

    This is a development gate, not claim-grade evidence. The retained state is transferred through
    the explicit harness state only; private model conversation state is neither required nor trusted.
    The retention probe forbids successful remember/adopt_procedure actions and requires the seeded
    state bytes to remain semantically unchanged while protected transfer/robustness tasks still pass.
    """

    acquisition_agent = acquisition_agent_factory()
    acquisition = run_workspace_suite(acquisition_agent)
    seed_state = AgentState(
        memory=deepcopy(acquisition.final_memory),
        procedure=deepcopy(acquisition.final_procedure),
    )
    seed_before = _state_payload(seed_state)

    retention_factory = retention_agent_factory or acquisition_agent_factory
    retention_agent = retention_factory()
    fresh_agent_instance = retention_agent is not acquisition_agent
    probe = _run_task_sequence(
        retention_agent,
        cross_run_retention_probe_suite(),
        state=seed_state,
        suite_id="workspace-cross-run-retention-v1",
    )
    seed_after = _state_payload(seed_state)

    state_mutation_events = [
        {
            "task_id": result.task_id,
            "step": event.step,
            "tool": event.tool,
            "arguments": deepcopy(event.arguments),
        }
        for result in probe.task_results
        for event in result.trace
        if event.success and event.tool in {"remember", "adopt_procedure"}
    ]
    task_success = {result.task_id: result.success for result in probe.task_results}
    protected_retained = all(
        task_success.get(task_id, False)
        for task_id in (
            "workspace-cross-run-protected-transfer",
            "workspace-cross-run-protected-robustness",
        )
    )
    seed_complete = _state_seed_complete(
        AgentState(memory=deepcopy(acquisition.final_memory), procedure=deepcopy(acquisition.final_procedure))
    )
    state_unchanged = seed_after == seed_before
    passed = all(
        (
            acquisition.passed,
            seed_complete,
            fresh_agent_instance,
            probe.passed,
            not state_mutation_events,
            state_unchanged,
            protected_retained,
        )
    )

    return {
        "schema_version": 1,
        "campaign_kind": "workspace-cross-run-retention-v1",
        "passed": passed,
        "acquisition": acquisition.to_dict(),
        "retention_probe": probe.to_dict(),
        "checks": {
            "acquisition_passed": acquisition.passed,
            "acquired_required_state": seed_complete,
            "fresh_agent_instance": fresh_agent_instance,
            "retention_probe_passed": probe.passed,
            "no_relearning_or_readoption_events": not state_mutation_events,
            "persistent_state_unchanged_during_probe": state_unchanged,
            "protected_transfer_and_robustness_retained": protected_retained,
        },
        "state_before_probe_sha256": _digest(seed_before),
        "state_after_probe_sha256": _digest(seed_after),
        "state_mutation_events": state_mutation_events,
        "evidence_class": "internal_development",
        "admissible_for_agi_claim": False,
        "claim_boundary": (
            "A pass demonstrates bounded explicit-state retention across fresh agent instances and "
            "protected-task retention inside the repository-authored workspace harness. It does not "
            "establish evaluator independence, production isolation, or AGI."
        ),
    }


def write_cross_run_workspace_retention(report: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "cross-run-workspace-retention.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return path


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m agi.cross_run_workspace_retention")
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    # Import lazily so deterministic tests and non-Copilot environments do not require the SDK.
    from .copilot_cli import CopilotWorkspaceAgent

    factory = lambda: CopilotWorkspaceAgent(args.model)  # noqa: E731
    report = run_cross_run_workspace_retention(acquisition_agent_factory=factory)
    path = write_cross_run_workspace_retention(report, args.output_dir)
    print(
        json.dumps(
            {
                "provider": "github-copilot-sdk",
                "model": args.model,
                "passed": report["passed"],
                "report": path.as_posix(),
                "warning": (
                    "Cross-run workspace retention is internal development evidence only and cannot "
                    "satisfy the independent production AGI gate."
                ),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
