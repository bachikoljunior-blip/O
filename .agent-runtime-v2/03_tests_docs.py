from __future__ import annotations

from pathlib import Path
from textwrap import dedent


def write(path: str, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dedent(content).lstrip(), encoding="utf-8")


def append_once(path: str, marker: str, content: str) -> None:
    target = Path(path)
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    if marker not in existing:
        target.write_text(
            existing.rstrip() + "\n\n" + dedent(content).strip() + "\n",
            encoding="utf-8",
        )


# Tighten child-task list inputs before compiling and testing generated sources.
agent_path = Path("src/agi/agent.py")
agent = agent_path.read_text(encoding="utf-8")
old = '''            raw_constraints = step.arguments.get("constraints", [])\n            raw_tags = step.arguments.get("tags", [])\n            child_task = TaskRequest.from_mapping(\n'''
new = '''            raw_constraints = step.arguments.get("constraints", [])\n            raw_tags = step.arguments.get("tags", [])\n            if not isinstance(raw_constraints, list):\n                raise ValueError("subtask constraints must be a list")\n            if not isinstance(raw_tags, list):\n                raise ValueError("subtask tags must be a list")\n            child_task = TaskRequest.from_mapping(\n'''
if old in agent:
    agent = agent.replace(old, new, 1)
agent_path.write_text(agent, encoding="utf-8")

write(
    "src/agi/__init__.py",
    r'''
    """Evidence-driven, hierarchical, resumable general-agent research runtime."""

    from .agent import AgentRunStore, GeneralAgent
    from .benchmark_runner import BenchmarkCase, BenchmarkRunner, FunctionVerifier, VerifierResult
    from .evaluation import CRITERIA, EvaluationPolicy, evaluate_evidence, evaluate_records
    from .evidence import EvidenceLedger, EvidenceRecord
    from .experiments import CandidateExperimentRunner, ExperimentCase
    from .improvement import CandidateStore, ImprovementCandidate
    from .memory import Episode, EpisodeStore
    from .perturbations import FaultRule, PerturbingToolRegistry
    from .skills import SkillDefinition, SkillRegistry
    from .tasking import AgentPlan, PlanStep, TaskRequest
    from .workspace import WorkspaceManager

    __all__ = [
        "AgentPlan",
        "AgentRunStore",
        "BenchmarkCase",
        "BenchmarkRunner",
        "CRITERIA",
        "CandidateExperimentRunner",
        "CandidateStore",
        "Episode",
        "EpisodeStore",
        "EvaluationPolicy",
        "EvidenceLedger",
        "EvidenceRecord",
        "ExperimentCase",
        "FaultRule",
        "FunctionVerifier",
        "GeneralAgent",
        "ImprovementCandidate",
        "PerturbingToolRegistry",
        "PlanStep",
        "SkillDefinition",
        "SkillRegistry",
        "TaskRequest",
        "VerifierResult",
        "WorkspaceManager",
        "evaluate_evidence",
        "evaluate_records",
    ]
    __version__ = "0.4.0"
    ''',
)

write(
    "agi/__init__.py",
    r'''
    """Checkout wrapper for the installable src/agi package."""

    from pathlib import Path as _Path

    _src_package = _Path(__file__).resolve().parents[1] / "src" / "agi"
    if _src_package.is_dir():
        __path__.append(str(_src_package))

    from .agent import AgentRunStore, GeneralAgent
    from .benchmark_runner import BenchmarkCase, BenchmarkRunner, FunctionVerifier, VerifierResult
    from .evaluation import CRITERIA, EvaluationPolicy, evaluate_evidence, evaluate_records
    from .evidence import EvidenceLedger, EvidenceRecord
    from .experiments import CandidateExperimentRunner, ExperimentCase
    from .improvement import CandidateStore, ImprovementCandidate
    from .memory import Episode, EpisodeStore
    from .perturbations import FaultRule, PerturbingToolRegistry
    from .skills import SkillDefinition, SkillRegistry
    from .tasking import AgentPlan, PlanStep, TaskRequest
    from .workspace import WorkspaceManager

    __all__ = [
        "AgentPlan", "AgentRunStore", "BenchmarkCase", "BenchmarkRunner", "CRITERIA",
        "CandidateExperimentRunner", "CandidateStore", "Episode", "EpisodeStore",
        "EvaluationPolicy", "EvidenceLedger", "EvidenceRecord", "ExperimentCase",
        "FaultRule", "FunctionVerifier", "GeneralAgent", "ImprovementCandidate",
        "PerturbingToolRegistry", "PlanStep", "SkillDefinition", "SkillRegistry",
        "TaskRequest", "VerifierResult", "WorkspaceManager", "evaluate_evidence",
        "evaluate_records"
    ]
    ''',
)

write(
    "tests/test_context_budget.py",
    r'''
    import json

    from agi.context import bound_context


    def test_large_context_is_deterministically_bounded() -> None:
        context = {
            "observations": [
                {"index": index, "text": "x" * 1000} for index in range(40)
            ],
            "goal": "preserve the task",
        }
        bounded = bound_context(context, character_budget=2500)
        assert len(json.dumps(bounded, ensure_ascii=False)) <= 2500
        assert "_context_truncation" in bounded
        assert bounded["goal"]
    ''',
)

write(
    "tests/test_transactional_workspace.py",
    r'''
    from __future__ import annotations

    import json
    from pathlib import Path

    from agi.workspace import WorkspaceManager


    def test_workspace_changes_are_isolated_verified_and_diffed(tmp_path: Path) -> None:
        (tmp_path / "hello.txt").write_text("old\n", encoding="utf-8")
        profile = tmp_path / ".continual/agi/verifier-profiles.json"
        profile.parent.mkdir(parents=True)
        profile.write_text(
            json.dumps(
                {
                    "profiles": {
                        "check-hello": {
                            "command": [
                                "{python}",
                                "-c",
                                "from pathlib import Path; assert Path('hello.txt').read_text() == 'new\\n'",
                            ],
                            "timeout_seconds": 30,
                            "max_output_chars": 5000,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        manager = WorkspaceManager(tmp_path)
        workspace = manager.create()
        workspace_id = workspace["workspace_id"]
        manager.write_text(workspace_id, "hello.txt", "new\n")
        assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "old\n"
        verification = manager.run_profile(workspace_id, "check-hello")
        assert verification["passed"] is True
        diff = manager.diff(workspace_id)
        assert diff["changed_files"] == [
            {
                "path": "hello.txt",
                "status": "modified",
                "before_sha256": diff["changed_files"][0]["before_sha256"],
                "after_sha256": diff["changed_files"][0]["after_sha256"],
            }
        ]
        assert "-old" in diff["preview"]
        assert "+new" in diff["preview"]
        assert (tmp_path / diff["patch"]).is_file()
        assert (tmp_path / diff["manifest"]).is_file()
    ''',
)

write(
    "tests/test_tool_perturbations.py",
    r'''
    from pathlib import Path

    from agi.perturbations import FaultRule, PerturbingToolRegistry
    from agi.tools import ToolDefinition, ToolRegistry


    def test_injected_failure_is_replayable_and_later_call_recovers(tmp_path: Path) -> None:
        base = ToolRegistry(tmp_path)
        base.register(
            ToolDefinition(
                "echo",
                lambda root, arguments: {"value": arguments["value"]},
            )
        )
        registry = PerturbingToolRegistry(
            base,
            [FaultRule("echo", (1,), "temporary injected failure")],
        )
        first = registry.execute(
            "echo", {"value": 1}, run_id="run-1", call_id="attempt-1"
        )
        replay = registry.execute(
            "echo", {"value": 1}, run_id="run-1", call_id="attempt-1"
        )
        recovered = registry.execute(
            "echo", {"value": 2}, run_id="run-1", call_id="attempt-2"
        )
        assert first == replay
        assert first["ok"] is False
        assert first["perturbation"]["ordinal"] == 1
        assert recovered["ok"] is True
        assert recovered["output"] == {"value": 2}
    ''',
)

write(
    "tests/test_hierarchical_agent.py",
    r'''
    from __future__ import annotations

    from pathlib import Path

    from agi.agent import GeneralAgent
    from agi.reasoner import ScriptedReasoner
    from agi.tasking import TaskRequest


    def test_subtask_runs_as_separate_run_without_parent_hidden_context(tmp_path: Path) -> None:
        reasoner = ScriptedReasoner(
            {
                "orient": [
                    {"summary": "parent orientation"},
                    {"summary": "child orientation"},
                ],
                "plan": [
                    {
                        "steps": [
                            {
                                "step_id": "delegate",
                                "description": "Compute seven in a child run",
                                "kind": "subtask",
                                "arguments": {
                                    "success_criteria": ["child answer equals 7"],
                                    "tags": ["math"],
                                },
                            }
                        ]
                    },
                    {
                        "steps": [
                            {
                                "step_id": "child-reason",
                                "description": "Return seven",
                                "kind": "reason",
                            }
                        ]
                    },
                ],
                "act": [{"answer": 7}],
                "verify": [
                    {
                        "criteria": {"child answer equals 7": True},
                        "complete": True,
                        "result": {"answer": 7},
                    },
                    {
                        "criteria": {"delegated result equals 7": True},
                        "complete": True,
                        "result": {"answer": 7},
                    },
                ],
                "reflect": [
                    {
                        "summary": "child completed",
                        "lessons": [],
                        "failures": [],
                        "improvements": [],
                    },
                    {
                        "summary": "parent completed",
                        "lessons": [],
                        "failures": [],
                        "improvements": [],
                    },
                ],
            }
        )
        agent = GeneralAgent(tmp_path, reasoner)
        run_id = agent.start(
            TaskRequest.from_mapping(
                {
                    "task_id": "parent-task",
                    "goal": "Delegate a computation",
                    "success_criteria": ["delegated result equals 7"],
                    "tags": ["planning"],
                }
            )
        )
        snapshot = agent.run(run_id, max_transitions=30)
        assert snapshot["status"] == "completed"
        observation = snapshot["observations"][0]["output"]
        assert observation["status"] == "completed"
        assert observation["result"] == {"answer": 7}
        assert observation["child_run_id"] != run_id
        child_orient = [
            call
            for call in reasoner.calls
            if call["component"] == "orient"
            and call["context"]["task"]["task_id"] == "parent-task:delegate"
        ][0]
        assert "plan" not in child_orient["context"]
        assert "observations" not in child_orient["context"]
        assert child_orient["context"]["task"]["metadata"]["parent_run_id"] == run_id
        assert len(agent.memory.load()) == 2


    def test_unexpected_verification_key_blocks_completion(tmp_path: Path) -> None:
        reasoner = ScriptedReasoner(
            {
                "orient": [{"summary": "orient"}],
                "plan": [
                    {
                        "steps": [
                            {
                                "step_id": "answer",
                                "description": "answer",
                                "kind": "reason",
                            }
                        ]
                    }
                ],
                "act": [{"answer": 1}],
                "verify": [
                    {
                        "criteria": {"different criterion": True},
                        "complete": True,
                        "result": {"answer": 1},
                    }
                ],
                "reflect": [
                    {
                        "summary": "verification mismatch",
                        "lessons": [],
                        "failures": ["criterion mismatch"],
                        "improvements": [],
                    }
                ],
            }
        )
        agent = GeneralAgent(tmp_path, reasoner)
        run_id = agent.start(
            TaskRequest.from_mapping(
                {
                    "task_id": "strict-verification",
                    "goal": "Answer exactly",
                    "success_criteria": ["answer equals 1"],
                    "max_plan_revisions": 0,
                }
            )
        )
        snapshot = agent.run(run_id, max_transitions=20)
        assert snapshot["status"] == "blocked"
        assert snapshot["verification"]["missing_criteria"] == ["answer equals 1"]
        assert snapshot["verification"]["unexpected_criteria"] == ["different criterion"]
    ''',
)

write(
    "tests/test_candidate_experiments.py",
    r'''
    from __future__ import annotations

    from pathlib import Path

    from agi.benchmark_runner import BenchmarkCase, FunctionVerifier, VerifierResult
    from agi.experiments import CandidateExperimentRunner, ExperimentCase
    from agi.improvement import CandidateStore
    from agi.reasoner import FunctionReasoner
    from agi.tasking import TaskRequest


    def make_case(case_id: str, domain: str, tag: str) -> BenchmarkCase:
        return BenchmarkCase(
            case_id=case_id,
            criterion="self_improvement",
            domain=domain,
            task=TaskRequest.from_mapping(
                {
                    "task_id": case_id,
                    "goal": f"Solve {case_id}",
                    "success_criteria": [f"{case_id} result produced"],
                    "tags": [tag],
                    "max_plan_revisions": 0,
                }
            ),
        )


    def verifier() -> FunctionVerifier:
        return FunctionVerifier(
            "independent-score-verifier",
            lambda snapshot: VerifierResult(
                passed=snapshot.get("status") == "completed",
                score=float(snapshot.get("result", {}).get("score", 0.0)),
            ),
            independent=True,
        )


    def reasoner_factory(variant: str, case: BenchmarkCase) -> FunctionReasoner:
        state = {"candidate": False}

        def handler(component: str, context: dict[str, object]) -> dict[str, object]:
            if component == "orient":
                state["candidate"] = bool(context.get("candidate_overlays"))
                return {"summary": f"{variant} oriented"}
            if component == "plan":
                return {
                    "steps": [
                        {
                            "step_id": "solve",
                            "description": "produce the scored result",
                            "kind": "reason",
                        }
                    ]
                }
            if component == "act":
                if case.case_id == "primary":
                    return {"score": 1.0 if state["candidate"] else 0.4}
                return {"score": 0.8}
            if component == "verify":
                criterion = case.task.success_criteria[0]
                latest = context["observations"][-1]["output"]
                return {
                    "criteria": {criterion: True},
                    "complete": True,
                    "result": latest,
                }
            if component == "reflect":
                return {
                    "summary": "experiment run completed",
                    "lessons": [],
                    "failures": [],
                    "improvements": [],
                }
            raise AssertionError(component)

        return FunctionReasoner(handler)


    def test_candidate_ab_experiment_requires_and_preserves_regression(tmp_path: Path) -> None:
        store = CandidateStore(tmp_path)
        candidate = store.add_from_reflection(
            "episode-source",
            [
                {
                    "target": "planner",
                    "proposal": "Use the candidate planning rule.",
                    "trigger_tags": ["code"],
                    "trigger_terms": ["primary"],
                }
            ],
            ["code"],
        )[0]
        primary = ExperimentCase(make_case("primary", "code", "code"), verifier())
        regression = ExperimentCase(
            make_case("regression", "planning", "planning"), verifier()
        )
        runner = CandidateExperimentRunner(tmp_path, reasoner_factory)
        result = runner.run(candidate.candidate_id, primary, [regression])
        assert result["primary"]["improved"] is True
        assert result["regressions_passed"] is True
        assert result["adopted_for_primary_task"] is True
        assert (tmp_path / result["artifact"]).is_file()
        updated = CandidateStore(tmp_path).load()[candidate.candidate_id]
        assert updated.adopted_scopes["primary"] is True
        assert updated.status == "candidate"
    ''',
)

write(
    "agi/RUNTIME_V2.md",
    r'''
    # Runtime v2: hierarchy, experiments, and transactional workspaces

    This iteration closes three gaps in the first capability runtime.

    ## Hierarchical child tasks

    A plan may now contain a `subtask` step. The child receives a newly validated
    `TaskRequest`, a new durable run ID, inherited immutable constraints, bounded
    memory selected for the child goal, and the same general skill registry. It does
    not receive the parent's orientation, plan, or observation history. Maximum
    recursion depth and per-child transition budgets are enforced.

    ## Transactional repository work

    Agent tools can create a repository copy, edit only that copy, run fixed verifier
    profiles without a shell, and emit a hash-checked patch plus manifest. The source
    repository is unchanged until a separate reviewed adoption step. The workspace
    uses environment scrubbing and resource limits, but it is an accidental-mutation
    boundary rather than a claim of complete hostile-code isolation.

    ## Real candidate A/B evaluation

    A `CandidateExperimentRunner` executes baseline and candidate variants in
    separate repository copies. It requires an independent verifier and at least one
    regression case. Only a measured improvement with preserved regressions is
    adopted for the evaluated task. Global adoption still requires positive evidence
    across at least three domains. Experiment artifacts are auditable but do not
    automatically count as AGI evidence.

    ## Robustness instrumentation

    `PerturbingToolRegistry` injects deterministic, replayable failures by logical
    call ordinal. This makes recovery behavior measurable instead of relying on a
    model's claim that it would recover.
    ''',
)

append_once(
    "README.md",
    "## Hierarchical experiments and transactional workspaces",
    r'''
    ## Hierarchical experiments and transactional workspaces

    The agent can now spawn context-isolated child tasks, edit and test repository
    copies without mutating the source tree, inject replayable tool failures, and run
    baseline/candidate A/B experiments with mandatory regression checks. See
    `agi/RUNTIME_V2.md`.
    ''',
)

append_once(
    "SYSTEM_DESIGN.md",
    "## Hierarchical experiment plane",
    r'''
    ## Hierarchical experiment plane

    Child tasks are independent durable runs with explicit lineage rather than hidden
    continuation of a parent conversation. Candidate changes are evaluated in paired
    transactional workspaces, under the same independent verifier and a mandatory
    regression set. Workspace patches and experiment results are artifacts; neither
    modifies active policy nor enters the AGI ledger without a separate evidence
    qualification step.
    ''',
)

append_once(
    "AGENTS.md",
    "## Hierarchical and experiment rules",
    r'''
    ## Hierarchical and experiment rules

    - Use a `subtask` only when decomposition reduces uncertainty or isolates a
      reusable result; preserve parent/child lineage and recursion limits.
    - Never pass parent orientation, plan, or full observations into a child run.
    - Make code changes in a transactional workspace, run an allowlisted verifier,
      and preserve the patch artifact before considering adoption.
    - A candidate experiment requires an independent verifier and at least one
      regression case. Scope adoption to the evaluated task until cross-domain
      evidence satisfies the global rule.
    - Injected failures must be replayable by run ID and logical call ID so robustness
      results can be independently reproduced.
    ''',
)

for source in (
    "src/agi/context.py",
    "src/agi/workspace.py",
    "src/agi/perturbations.py",
    "src/agi/experiments.py",
    "src/agi/tasking.py",
    "src/agi/improvement.py",
    "src/agi/agent.py",
):
    compile(Path(source).read_text(encoding="utf-8"), source, "exec")
