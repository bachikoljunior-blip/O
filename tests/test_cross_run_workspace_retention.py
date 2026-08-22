from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

from agi.benchmark import AgentState
from agi.copilot_cli import build_parser as build_copilot_parser
from agi.cross_run_workspace_retention import run_cross_run_workspace_retention
from agi.workspace import (
    ReferenceWorkspaceAgent,
    ToolEvent,
    WorkspaceAction,
    WorkspaceTask,
)


class RetentionReferenceAgent:
    name = "retention-reference-agent"

    def __init__(self, *, mutate_state: bool = False) -> None:
        self._positions: dict[str, int] = {}
        self._mutate_state = mutate_state

    def act(
        self,
        task: WorkspaceTask,
        state: AgentState,
        observation: Mapping[str, object],
        trace: Sequence[ToolEvent],
    ) -> WorkspaceAction:
        plans = self._plans()
        plan = plans[task.task_id]
        position = self._positions.get(task.task_id, 0)
        self._positions[task.task_id] = position + 1
        if position >= len(plan):
            return WorkspaceAction("final", answer="done")
        return plan[position]

    def _plans(self) -> dict[str, tuple[WorkspaceAction, ...]]:
        tool = lambda name, **arguments: WorkspaceAction("tool", name, arguments)  # noqa: E731
        final = WorkspaceAction("final", answer="done")
        memory_prefix = (
            (tool("remember", namespace="labels", key="azure", value=7),)
            if self._mutate_state
            else ()
        )
        return {
            "workspace-cross-run-memory-recall": (
                *memory_prefix,
                tool("read_file", path="query.json"),
                tool("write_file", path="answer.json", content="7"),
                final,
            ),
            "workspace-cross-run-procedure-reuse": (
                tool("read_file", path="values.json"),
                tool("write_file", path="answer.json", content="[2, 4, 12, 31]"),
                final,
            ),
            "workspace-cross-run-protected-transfer": (
                tool("read_file", path="examples.json"),
                tool("read_file", path="query.txt"),
                tool("write_file", path="answer.txt", content="TACC"),
                final,
            ),
            "workspace-cross-run-protected-robustness": (
                tool("read_file", path="values.json"),
                tool("write_file", path="answer.json", content="[1, 2, 3]"),
                final,
            ),
        }


def test_cross_run_retention_requires_fresh_agent_and_preserves_state():
    report = run_cross_run_workspace_retention(
        acquisition_agent_factory=ReferenceWorkspaceAgent,
        retention_agent_factory=RetentionReferenceAgent,
    )

    assert report["passed"] is True
    assert report["checks"] == {
        "acquisition_passed": True,
        "acquired_required_state": True,
        "fresh_agent_instance": True,
        "retention_probe_passed": True,
        "no_relearning_or_readoption_events": True,
        "persistent_state_unchanged_during_probe": True,
        "protected_transfer_and_robustness_retained": True,
    }
    assert report["state_before_probe_sha256"] == report["state_after_probe_sha256"]
    assert report["state_mutation_events"] == []
    assert report["admissible_for_agi_claim"] is False


def test_cross_run_retention_rejects_relearning_even_when_outputs_pass():
    report = run_cross_run_workspace_retention(
        acquisition_agent_factory=ReferenceWorkspaceAgent,
        retention_agent_factory=lambda: RetentionReferenceAgent(mutate_state=True),
    )

    assert report["retention_probe"]["passed"] is True
    assert report["checks"]["no_relearning_or_readoption_events"] is False
    assert report["state_mutation_events"][0]["tool"] == "remember"
    assert report["passed"] is False


def test_copilot_cli_exposes_bounded_cross_run_retention_command():
    args = build_copilot_parser().parse_args(
        ["run-retention", "--model", "gpt-5.4", "--output-dir", "/tmp/retention"]
    )

    assert args.cmd == "run-retention"
    assert args.model == "gpt-5.4"
    assert args.output_dir == Path("/tmp/retention")
