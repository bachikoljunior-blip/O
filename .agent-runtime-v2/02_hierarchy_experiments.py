from __future__ import annotations

from pathlib import Path
from textwrap import dedent


def write(path: str, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dedent(content).lstrip(), encoding="utf-8")


write(
    "src/agi/experiments.py",
    r'''
    from __future__ import annotations

    import json
    import uuid
    from dataclasses import dataclass
    from pathlib import Path
    from typing import Any, Callable, Mapping, Sequence

    from .agent import GeneralAgent
    from .benchmark_runner import BenchmarkCase, Verifier, VerifierResult
    from .evidence import sha256_file, utc_now
    from .improvement import CandidateStore
    from .reasoner import Reasoner
    from .workspace import WorkspaceManager


    @dataclass(frozen=True, slots=True)
    class ExperimentCase:
        case: BenchmarkCase
        verifier: Verifier


    class CandidateExperimentRunner:
        """A/B-test a relevant improvement in isolated repository copies.

        The baseline receives no candidate overlay. The candidate variant receives
        only the selected candidate. Both variants are checked by the same independent
        verifier, and at least one regression case is mandatory before per-task
        adoption can be recorded.
        """

        def __init__(
            self,
            root: Path,
            reasoner_factory: Callable[[str, BenchmarkCase], Reasoner],
        ):
            self.root = root.resolve()
            self.reasoner_factory = reasoner_factory
            self.candidates = CandidateStore(self.root)
            self.workspaces = WorkspaceManager(self.root)
            self.artifacts = (
                self.root / ".continual" / "agi" / "artifacts" / "experiments"
            )

        @staticmethod
        def _terminal(agent: GeneralAgent, run_id: str) -> dict[str, Any]:
            snapshot = agent.run(run_id, max_transitions=50)
            cycles = 1
            while snapshot.get("status") == "continue" and cycles < 10:
                snapshot = agent.run(run_id, max_transitions=50)
                cycles += 1
            return snapshot

        def _variant(
            self,
            variant: str,
            workspace_root: Path,
            case: ExperimentCase,
            candidate_id: str,
        ) -> tuple[dict[str, Any], VerifierResult]:
            if not case.verifier.independent:
                raise ValueError("candidate experiments require an independent verifier")
            mode = "off" if variant == "baseline" else "specific"
            ids = () if variant == "baseline" else (candidate_id,)
            agent = GeneralAgent(
                workspace_root,
                self.reasoner_factory(variant, case.case),
                candidate_mode=mode,
                candidate_ids=ids,
            )
            run_id = agent.start(case.case.task)
            snapshot = self._terminal(agent, run_id)
            result = case.verifier.verify(snapshot)
            return snapshot, result

        def run(
            self,
            candidate_id: str,
            primary: ExperimentCase,
            regressions: Sequence[ExperimentCase],
            *,
            regression_tolerance: float = 0.0,
        ) -> dict[str, Any]:
            candidates = self.candidates.load()
            candidate = candidates.get(candidate_id)
            if candidate is None:
                raise KeyError(candidate_id)
            if candidate.status == "rejected":
                raise ValueError("rejected candidates cannot be evaluated")
            if not regressions:
                raise ValueError("at least one regression case is required")
            if not candidate.relevant(primary.case.task):
                raise ValueError("candidate is not relevant to the primary task")
            if regression_tolerance < 0:
                raise ValueError("regression_tolerance cannot be negative")

            experiment_id = f"experiment-{uuid.uuid4().hex[:16]}"
            primary_results: dict[str, Any] = {}
            primary_scores: dict[str, float] = {}
            workspace_ids: list[str] = []

            for variant in ("baseline", "candidate"):
                workspace = self.workspaces.create()
                workspace_ids.append(workspace["workspace_id"])
                work = self.workspaces._work(workspace["workspace_id"])
                snapshot, verified = self._variant(
                    variant, work, primary, candidate_id
                )
                primary_results[variant] = {
                    "run_id": snapshot.get("run_id"),
                    "status": snapshot.get("status"),
                    "result": snapshot.get("result"),
                    "verification": verified.as_dict(),
                    "workspace_id": workspace["workspace_id"],
                }
                primary_scores[variant] = float(verified.score)

            regression_results: list[dict[str, Any]] = []
            regressions_passed = True
            for regression in regressions:
                pair: dict[str, Any] = {"case_id": regression.case.case_id}
                scores: dict[str, float] = {}
                passes: dict[str, bool] = {}
                for variant in ("baseline", "candidate"):
                    workspace = self.workspaces.create()
                    workspace_ids.append(workspace["workspace_id"])
                    work = self.workspaces._work(workspace["workspace_id"])
                    snapshot, verified = self._variant(
                        variant, work, regression, candidate_id
                    )
                    pair[variant] = {
                        "run_id": snapshot.get("run_id"),
                        "status": snapshot.get("status"),
                        "result": snapshot.get("result"),
                        "verification": verified.as_dict(),
                        "workspace_id": workspace["workspace_id"],
                    }
                    scores[variant] = float(verified.score)
                    passes[variant] = bool(verified.passed)
                preserved = (
                    scores["candidate"] + regression_tolerance >= scores["baseline"]
                    and (not passes["baseline"] or passes["candidate"])
                )
                pair["preserved"] = preserved
                regressions_passed = regressions_passed and preserved
                regression_results.append(pair)

            baseline_score = primary_scores["baseline"]
            candidate_score = primary_scores["candidate"]
            improved = (
                candidate_score > baseline_score
                and primary_results["candidate"]["verification"]["passed"]
            )
            updated = self.candidates.record_evaluation(
                candidate_id,
                task_id=primary.case.case_id,
                domain=primary.case.domain,
                baseline_score=baseline_score,
                candidate_score=candidate_score,
                regression_passed=regressions_passed,
            )
            artifact = {
                "schema_version": 1,
                "experiment_id": experiment_id,
                "candidate_id": candidate_id,
                "created_at": utc_now(),
                "primary": {
                    "case_id": primary.case.case_id,
                    "domain": primary.case.domain,
                    "results": primary_results,
                    "baseline_score": baseline_score,
                    "candidate_score": candidate_score,
                    "delta": candidate_score - baseline_score,
                    "improved": improved,
                },
                "regressions": regression_results,
                "regressions_passed": regressions_passed,
                "candidate_status": updated.status,
                "adopted_for_primary_task": updated.adopted_scopes.get(
                    primary.case.case_id, False
                ),
                "workspace_ids": workspace_ids,
            }
            self.artifacts.mkdir(parents=True, exist_ok=True)
            path = self.artifacts / f"{experiment_id}.json"
            path.write_text(
                json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return {
                **artifact,
                "artifact": path.relative_to(self.root).as_posix(),
                "artifact_sha256": sha256_file(path),
            }
    ''',
)

# Add exact candidate selection helpers.
improvement_path = Path("src/agi/improvement.py")
improvement = improvement_path.read_text(encoding="utf-8")
anchor = '''        def overlays(self, task: TaskRequest) -> list[dict[str, Any]]:\n            return [\n                {\n                    "candidate_id": candidate.candidate_id,\n                    "target": candidate.target,\n                    "proposal": candidate.proposal,\n                    "status": candidate.status,\n                    "adopted_for_task": candidate.adopted_scopes.get(task.task_id, False),\n                }\n                for candidate in self.relevant(task)\n            ]\n\n'''
addition = anchor + '''        def overlays_for_ids(self, candidate_ids: Iterable[str]) -> list[dict[str, Any]]:\n            candidates = self.load()\n            result: list[dict[str, Any]] = []\n            for candidate_id in candidate_ids:\n                candidate = candidates.get(candidate_id)\n                if candidate is None:\n                    raise KeyError(candidate_id)\n                if candidate.status == "rejected":\n                    continue\n                result.append(\n                    {\n                        "candidate_id": candidate.candidate_id,\n                        "target": candidate.target,\n                        "proposal": candidate.proposal,\n                        "status": candidate.status,\n                        "forced_experiment_overlay": True,\n                    }\n                )\n            return result\n\n'''
if "def overlays_for_ids" not in improvement:
    if anchor not in improvement:
        raise RuntimeError("could not locate CandidateStore.overlays")
    improvement = improvement.replace(anchor, addition, 1)
improvement_path.write_text(improvement, encoding="utf-8")

# Allow hierarchical subtask steps.
tasking_path = Path("src/agi/tasking.py")
tasking = tasking_path.read_text(encoding="utf-8")
tasking = tasking.replace(
    'if kind not in {"reason", "tool", "skill"}:',
    'if kind not in {"reason", "tool", "skill", "subtask"}:',
    1,
)
tasking = tasking.replace(
    'f"plan step {index} kind must be reason, tool, or skill"',
    'f"plan step {index} kind must be reason, tool, skill, or subtask"',
    1,
)
tasking_path.write_text(tasking, encoding="utf-8")

# Add candidate modes, hierarchical child tasks, and exact criterion matching.
agent_path = Path("src/agi/agent.py")
agent = agent_path.read_text(encoding="utf-8")
old_signature = '''            candidates: CandidateStore | None = None,\n        ):\n'''
new_signature = '''            candidates: CandidateStore | None = None,\n            candidate_mode: str = "relevant",\n            candidate_ids: tuple[str, ...] = (),\n            max_subtask_depth: int = 4,\n        ):\n'''
if old_signature in agent:
    agent = agent.replace(old_signature, new_signature, 1)
elif "candidate_mode: str" not in agent:
    raise RuntimeError("could not extend GeneralAgent constructor")
old_assignment = '''            self.candidates = candidates or CandidateStore(self.root)\n'''
new_assignment = '''            self.candidates = candidates or CandidateStore(self.root)\n            if candidate_mode not in {"off", "relevant", "specific"}:\n                raise ValueError("candidate_mode must be off, relevant, or specific")\n            if max_subtask_depth < 0:\n                raise ValueError("max_subtask_depth cannot be negative")\n            self.candidate_mode = candidate_mode\n            self.candidate_ids = tuple(candidate_ids)\n            self.max_subtask_depth = max_subtask_depth\n'''
if old_assignment in agent:
    agent = agent.replace(old_assignment, new_assignment, 1)
elif "self.candidate_mode" not in agent:
    raise RuntimeError("could not add GeneralAgent candidate assignments")

orient_anchor = '''        def _orient(self, snapshot: dict[str, Any]) -> dict[str, Any]:\n            task = self._task(snapshot)\n            memory_context = self.memory.context(task.goal, task.tags)\n            skill_context = self.skills.context(task)\n            overlays = self.candidates.overlays(task)\n'''
orient_replacement = '''        def _candidate_overlays(self, task: TaskRequest) -> list[dict[str, Any]]:\n            if self.candidate_mode == "off":\n                return []\n            if self.candidate_mode == "specific":\n                return self.candidates.overlays_for_ids(self.candidate_ids)\n            return self.candidates.overlays(task)\n\n        def _orient(self, snapshot: dict[str, Any]) -> dict[str, Any]:\n            task = self._task(snapshot)\n            memory_context = self.memory.context(task.goal, task.tags)\n            skill_context = self.skills.context(task)\n            overlays = self._candidate_overlays(task)\n'''
if orient_anchor in agent:
    agent = agent.replace(orient_anchor, orient_replacement, 1)
elif "def _candidate_overlays" not in agent:
    raise RuntimeError("could not patch candidate overlays")

plan_tools = '''                "available_tools": self.tools.describe(),\n                "plan_revision": snapshot.get("plan_revisions", 0),\n'''
plan_tools_new = '''                "available_tools": self.tools.describe(),\n                "candidate_overlays": snapshot.get("candidate_overlays", []),\n                "plan_revision": snapshot.get("plan_revisions", 0),\n'''
if plan_tools in agent:
    agent = agent.replace(plan_tools, plan_tools_new, 1)

act_anchor = '''        def _act(self, snapshot: dict[str, Any]) -> dict[str, Any]:\n'''
subtask_method = '''        def _run_subtask(\n            self,\n            snapshot: Mapping[str, Any],\n            task: TaskRequest,\n            step: PlanStep,\n        ) -> dict[str, Any]:\n            depth = int(task.metadata.get("subtask_depth", 0))\n            if depth >= self.max_subtask_depth:\n                raise RuntimeError("maximum subtask depth reached")\n            raw_success = step.arguments.get("success_criteria")\n            if not isinstance(raw_success, list) or not raw_success:\n                raw_success = [step.success_hint or step.description]\n            raw_constraints = step.arguments.get("constraints", [])\n            raw_tags = step.arguments.get("tags", [])\n            child_task = TaskRequest.from_mapping(\n                {\n                    "task_id": f"{task.task_id}:{step.step_id}",\n                    "goal": step.description,\n                    "success_criteria": raw_success,\n                    "constraints": [*task.constraints, *raw_constraints],\n                    "tags": [*task.tags, *raw_tags],\n                    "max_plan_revisions": step.arguments.get("max_plan_revisions", 1),\n                    "metadata": {\n                        "parent_run_id": snapshot["run_id"],\n                        "parent_task_id": task.task_id,\n                        "subtask_depth": depth + 1,\n                    },\n                }\n            )\n            child = GeneralAgent(\n                self.root,\n                self.reasoner,\n                tools=self.tools,\n                memory=self.memory,\n                skills=self.skills,\n                candidates=self.candidates,\n                candidate_mode=self.candidate_mode,\n                candidate_ids=self.candidate_ids,\n                max_subtask_depth=self.max_subtask_depth,\n            )\n            child_run_id = child.start(child_task)\n            snapshot_child = child.run(\n                child_run_id,\n                max_transitions=int(step.arguments.get("max_transitions", 40)),\n            )\n            cycles = 1\n            max_cycles = int(step.arguments.get("max_cycles", 5))\n            while snapshot_child.get("status") == "continue" and cycles < max_cycles:\n                snapshot_child = child.run(\n                    child_run_id,\n                    max_transitions=int(step.arguments.get("max_transitions", 40)),\n                )\n                cycles += 1\n            return {\n                "child_run_id": child_run_id,\n                "status": snapshot_child.get("status"),\n                "result": snapshot_child.get("result"),\n                "verification": snapshot_child.get("verification", {}),\n                "cycles": cycles,\n            }\n\n'''
if "def _run_subtask(" not in agent:
    if act_anchor not in agent:
        raise RuntimeError("could not locate GeneralAgent._act")
    agent = agent.replace(act_anchor, subtask_method + act_anchor, 1)

skill_branch = '''            elif step.kind == "skill":\n                output = self._reason(\n                    snapshot,\n                    "skill",\n                    self._skill_context(task, step),\n                )\n            else:\n'''
subtask_branch = '''            elif step.kind == "skill":\n                output = self._reason(\n                    snapshot,\n                    "skill",\n                    self._skill_context(task, step),\n                )\n            elif step.kind == "subtask":\n                output = self._run_subtask(snapshot, task, step)\n            else:\n'''
if skill_branch in agent:
    agent = agent.replace(skill_branch, subtask_branch, 1)
elif 'elif step.kind == "subtask"' not in agent:
    raise RuntimeError("could not add subtask action branch")

old_verified = '''            verified = (\n                complete\n                and len(criteria) >= len(task.success_criteria)\n                and all(criteria.values())\n            )\n            verification = {\n                **response,\n                "criteria": criteria,\n                "complete": verified,\n            }\n'''
new_verified = '''            expected = set(task.success_criteria)\n            supplied = set(criteria)\n            verified = (\n                complete\n                and supplied == expected\n                and all(criteria[item] for item in expected)\n            )\n            verification = {\n                **response,\n                "criteria": criteria,\n                "complete": verified,\n                "missing_criteria": sorted(expected - supplied),\n                "unexpected_criteria": sorted(supplied - expected),\n            }\n'''
if old_verified in agent:
    agent = agent.replace(old_verified, new_verified, 1)
elif '"unexpected_criteria"' not in agent:
    raise RuntimeError("could not patch exact verification criteria")
agent_path.write_text(agent, encoding="utf-8")
