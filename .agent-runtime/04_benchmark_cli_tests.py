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


write(
    "src/agi/benchmark_runner.py",
    r'''
    from __future__ import annotations

    import json
    import uuid
    from dataclasses import asdict, dataclass, field
    from pathlib import Path
    from typing import Any, Callable, Mapping, Protocol

    from .agent import GeneralAgent
    from .campaign import CampaignManager
    from .evidence import CRITERION_KEYS, EvidenceRecord, sha256_file, utc_now
    from .tasking import TaskRequest, json_copy


    @dataclass(frozen=True, slots=True)
    class VerifierResult:
        passed: bool
        score: float
        details: dict[str, Any] = field(default_factory=dict)

        def as_dict(self) -> dict[str, Any]:
            return asdict(self)


    class Verifier(Protocol):
        name: str
        independent: bool

        def verify(self, snapshot: Mapping[str, Any]) -> VerifierResult:
            ...


    class FunctionVerifier:
        def __init__(
            self,
            name: str,
            handler: Callable[[Mapping[str, Any]], VerifierResult],
            *,
            independent: bool = True,
        ):
            if not name.strip():
                raise ValueError("verifier name cannot be empty")
            self.name = name.strip()
            self.handler = handler
            self.independent = independent

        def verify(self, snapshot: Mapping[str, Any]) -> VerifierResult:
            result = self.handler(json_copy(dict(snapshot)))
            if not isinstance(result, VerifierResult):
                raise TypeError("verifier handler must return VerifierResult")
            return result


    @dataclass(frozen=True, slots=True)
    class BenchmarkCase:
        case_id: str
        criterion: str
        domain: str
        task: TaskRequest
        sealed: bool = False
        qualifies_for_evidence: bool = False
        held_out: bool = False
        perturbations: tuple[str, ...] = ()
        evidence_metadata: dict[str, Any] = field(default_factory=dict)

        def __post_init__(self) -> None:
            if not self.case_id.strip():
                raise ValueError("benchmark case_id cannot be empty")
            if self.criterion not in CRITERION_KEYS:
                raise ValueError(f"unknown benchmark criterion: {self.criterion}")
            if not self.domain.strip():
                raise ValueError("benchmark domain cannot be empty")
            if self.criterion == "transfer" and self.qualifies_for_evidence and not self.held_out:
                raise ValueError("qualifying transfer cases must be held out")


    class BenchmarkRunner:
        """Run a task, persist a verifier artifact, and conditionally append evidence.

        Unit tests and demos default to `qualifies_for_evidence=False`.  A record is
        appended only when the case is explicitly sealed and the verifier declares
        independence.
        """

        def __init__(self, root: Path, agent: GeneralAgent):
            self.root = root.resolve()
            self.agent = agent
            self.artifact_directory = (
                self.root / ".continual" / "agi" / "artifacts" / "benchmarks"
            )

        def run_case(
            self,
            case: BenchmarkCase,
            verifier: Verifier,
            *,
            max_transitions_per_cycle: int = 50,
            max_cycles: int = 10,
        ) -> dict[str, Any]:
            run_id = self.agent.start(case.task)
            snapshot = self.agent.run(
                run_id, max_transitions=max_transitions_per_cycle
            )
            cycles = 1
            while snapshot.get("status") == "continue" and cycles < max_cycles:
                snapshot = self.agent.run(
                    run_id, max_transitions=max_transitions_per_cycle
                )
                cycles += 1
            verifier_result = verifier.verify(snapshot)
            artifact = {
                "schema_version": 1,
                "case_id": case.case_id,
                "criterion": case.criterion,
                "domain": case.domain,
                "sealed": case.sealed,
                "qualifies_for_evidence": case.qualifies_for_evidence,
                "run_id": run_id,
                "snapshot_revision": snapshot.get("revision"),
                "snapshot_status": snapshot.get("status"),
                "result": snapshot.get("result"),
                "verification": verifier_result.as_dict(),
                "verifier": {
                    "name": verifier.name,
                    "independent": verifier.independent,
                },
                "created_at": utc_now(),
            }
            self.artifact_directory.mkdir(parents=True, exist_ok=True)
            artifact_path = self.artifact_directory / f"{case.case_id}-{run_id}.json"
            artifact_path.write_text(
                json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            evidence_appended = False
            evidence_id: str | None = None
            campaign_state: dict[str, Any] | None = None
            qualification_reason = "case is not marked as qualifying evidence"
            if case.qualifies_for_evidence:
                if not case.sealed:
                    qualification_reason = "qualifying case is not sealed"
                elif not verifier.independent:
                    qualification_reason = "verifier is not independent"
                elif not verifier_result.passed:
                    qualification_reason = "verifier did not pass the result"
                elif snapshot.get("status") != "completed":
                    qualification_reason = "agent run did not complete"
                else:
                    evidence_id = f"benchmark-{case.case_id}-{uuid.uuid4().hex[:12]}"
                    details = verifier_result.details
                    record = EvidenceRecord.from_mapping(
                        {
                            "evidence_id": evidence_id,
                            "criterion": case.criterion,
                            "task_id": case.case_id,
                            "domain": case.domain,
                            "kind": "benchmark",
                            "passed": True,
                            "verifier": verifier.name,
                            "artifact": artifact_path.relative_to(self.root).as_posix(),
                            "artifact_sha256": sha256_file(artifact_path),
                            "run_id": run_id,
                            "held_out": case.held_out,
                            "steps": int(snapshot.get("transitions", 0)),
                            "recovered_failures": int(
                                case.evidence_metadata.get("recovered_failures", 0)
                            ),
                            "perturbations": list(case.perturbations),
                            "baseline_score": details.get("baseline_score"),
                            "candidate_score": details.get("candidate_score"),
                            "regression_score": details.get("regression_score"),
                            "regression_floor": details.get("regression_floor"),
                            "metadata": dict(case.evidence_metadata),
                        }
                    )
                    campaign = CampaignManager(self.root)
                    campaign.initialize()
                    campaign.ledger.append(record)
                    campaign_state = campaign.advance()
                    evidence_appended = True
                    qualification_reason = "objective evidence appended"

            return {
                "case_id": case.case_id,
                "run_id": run_id,
                "snapshot": snapshot,
                "verifier_result": verifier_result.as_dict(),
                "artifact": artifact_path.relative_to(self.root).as_posix(),
                "evidence_appended": evidence_appended,
                "evidence_id": evidence_id,
                "qualification_reason": qualification_reason,
                "campaign": campaign_state,
            }
    ''',
)

write(
    "src/agi/agent_cli.py",
    r'''
    from __future__ import annotations

    import argparse
    import json
    import shlex
    from pathlib import Path
    from typing import Any

    from .agent import GeneralAgent
    from .improvement import CandidateStore
    from .memory import EpisodeStore
    from .reasoner import JsonCommandReasoner, scripted_reasoner_from_file
    from .tasking import TaskRequest


    def emit(value: Any) -> None:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


    def build_parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="agi-agent",
            description="Run or resume the repository's explicit-context general agent.",
        )
        parser.add_argument("--root", type=Path, default=Path.cwd())
        subcommands = parser.add_subparsers(dest="command", required=True)

        run = subcommands.add_parser("run")
        run.add_argument("task_file", type=Path)
        run.add_argument("--script", type=Path)
        run.add_argument("--reasoner-command")
        run.add_argument("--max-transitions", type=int, default=50)

        resume = subcommands.add_parser("resume")
        resume.add_argument("run_id")
        resume.add_argument("--script", type=Path)
        resume.add_argument("--reasoner-command")
        resume.add_argument("--max-transitions", type=int, default=50)

        show = subcommands.add_parser("show")
        show.add_argument("run_id")
        subcommands.add_parser("memory")
        subcommands.add_parser("candidates")
        return parser


    def reasoner(args: argparse.Namespace, root: Path):
        if args.script is not None and args.reasoner_command:
            raise ValueError("choose either --script or --reasoner-command")
        if args.script is not None:
            return scripted_reasoner_from_file(args.script)
        if args.reasoner_command:
            command = shlex.split(args.reasoner_command)
            return JsonCommandReasoner(root, command)
        raise ValueError("run and resume require --script or --reasoner-command")


    def main(argv: list[str] | None = None) -> int:
        args = build_parser().parse_args(argv)
        root = args.root.resolve()
        if args.command == "show":
            from .agent import AgentRunStore

            emit(AgentRunStore(root).load(args.run_id))
            return 0
        if args.command == "memory":
            store = EpisodeStore(root)
            emit(
                {
                    "episodes": [episode.as_dict() for episode in store.load()],
                    "views": store.consolidate(),
                }
            )
            return 0
        if args.command == "candidates":
            emit(
                {
                    "candidates": [
                        candidate.as_dict()
                        for candidate in CandidateStore(root).load().values()
                    ]
                }
            )
            return 0

        agent = GeneralAgent(root, reasoner(args, root))
        if args.command == "run":
            value = json.loads(args.task_file.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("task file root must be an object")
            run_id = agent.start(TaskRequest.from_mapping(value))
            emit(agent.run(run_id, max_transitions=args.max_transitions))
            return 0
        if args.command == "resume":
            emit(agent.run(args.run_id, max_transitions=args.max_transitions))
            return 0
        raise AssertionError(args.command)


    if __name__ == "__main__":
        raise SystemExit(main())
    ''',
)

write(
    "src/agi/__init__.py",
    r'''
    """Evidence-driven, resumable general-agent research runtime."""

    from .agent import AgentRunStore, GeneralAgent
    from .benchmark_runner import BenchmarkCase, BenchmarkRunner, FunctionVerifier, VerifierResult
    from .evaluation import CRITERIA, EvaluationPolicy, evaluate_evidence, evaluate_records
    from .evidence import EvidenceLedger, EvidenceRecord
    from .improvement import CandidateStore, ImprovementCandidate
    from .memory import Episode, EpisodeStore
    from .skills import SkillDefinition, SkillRegistry
    from .tasking import AgentPlan, PlanStep, TaskRequest

    __all__ = [
        "AgentPlan",
        "AgentRunStore",
        "BenchmarkCase",
        "BenchmarkRunner",
        "CRITERIA",
        "CandidateStore",
        "Episode",
        "EpisodeStore",
        "EvaluationPolicy",
        "EvidenceLedger",
        "EvidenceRecord",
        "FunctionVerifier",
        "GeneralAgent",
        "ImprovementCandidate",
        "PlanStep",
        "SkillDefinition",
        "SkillRegistry",
        "TaskRequest",
        "VerifierResult",
        "evaluate_evidence",
        "evaluate_records",
    ]
    __version__ = "0.3.0"
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
    from .improvement import CandidateStore, ImprovementCandidate
    from .memory import Episode, EpisodeStore
    from .skills import SkillDefinition, SkillRegistry
    from .tasking import AgentPlan, PlanStep, TaskRequest

    __all__ = [
        "AgentPlan", "AgentRunStore", "BenchmarkCase", "BenchmarkRunner", "CRITERIA",
        "CandidateStore", "Episode", "EpisodeStore", "EvaluationPolicy",
        "EvidenceLedger", "EvidenceRecord", "FunctionVerifier", "GeneralAgent",
        "ImprovementCandidate", "PlanStep", "SkillDefinition", "SkillRegistry",
        "TaskRequest", "VerifierResult", "evaluate_evidence", "evaluate_records"
    ]
    ''',
)

pyproject = Path("pyproject.toml")
text = pyproject.read_text(encoding="utf-8")
if 'agi-agent = "agi.agent_cli:main"' not in text:
    header = "[project.scripts]\n"
    if header not in text:
        raise RuntimeError("pyproject.toml is missing [project.scripts]")
    text = text.replace(header, header + 'agi-agent = "agi.agent_cli:main"\n', 1)
pyproject.write_text(text, encoding="utf-8")

write(
    "tests/test_general_agent.py",
    r'''
    from __future__ import annotations

    from pathlib import Path

    from agi.agent import GeneralAgent
    from agi.reasoner import ScriptedReasoner
    from agi.tasking import TaskRequest


    def successful_reasoner(*, skill: bool = False) -> ScriptedReasoner:
        step = (
            {
                "step_id": "use-skill",
                "description": "Solve the isolated subtask",
                "kind": "skill",
                "skill_id": "root-task",
                "success_hint": "return 42",
            }
            if skill
            else {
                "step_id": "reason",
                "description": "Derive the answer",
                "kind": "reason",
            }
        )
        scripts = {
            "orient": [{"task_summary": "Find the requested answer", "risks": []}],
            "plan": [{"steps": [step], "rationale": "one bounded step"}],
            "verify": [
                {
                    "criteria": {"answer equals 42": True},
                    "complete": True,
                    "result": {"answer": 42},
                    "feedback": "verified",
                }
            ],
            "reflect": [
                {
                    "summary": "The bounded plan succeeded.",
                    "lessons": ["Verify the computed answer before completion."],
                    "failures": [],
                    "improvements": [
                        {
                            "target": "planner",
                            "proposal": "Prefer one-step plans for atomic arithmetic tasks.",
                            "trigger_tags": ["math"],
                            "trigger_terms": ["calculate"],
                        }
                    ],
                }
            ],
        }
        scripts["skill" if skill else "act"] = [{"answer": 42}]
        return ScriptedReasoner(scripts)


    def task() -> TaskRequest:
        return TaskRequest.from_mapping(
            {
                "task_id": "answer-42",
                "goal": "Calculate the required answer.",
                "success_criteria": ["answer equals 42"],
                "constraints": ["preserve evidence"],
                "tags": ["math"],
            }
        )


    def test_run_pauses_and_resumes_without_losing_phase(tmp_path: Path) -> None:
        reasoner = successful_reasoner()
        agent = GeneralAgent(tmp_path, reasoner)
        run_id = agent.start(task())
        paused = agent.run(run_id, max_transitions=2)
        assert paused["status"] == "continue"
        assert paused["phase"] == "act"
        assert paused["pause_reason"] == "transition_budget_exhausted"
        completed = agent.run(run_id, max_transitions=20)
        assert completed["status"] == "completed"
        assert completed["phase"] == "complete"
        assert completed["result"] == {"answer": 42}
        assert completed["learned"] is True
        assert len(agent.memory.load()) == 1
        assert agent.memory.recent_path.is_file()
        assert len(agent.candidates.load()) == 1


    def test_child_skill_receives_isolated_context(tmp_path: Path) -> None:
        reasoner = successful_reasoner(skill=True)
        agent = GeneralAgent(tmp_path, reasoner)
        run_id = agent.start(task())
        completed = agent.run(run_id, max_transitions=20)
        assert completed["status"] == "completed"
        child = next(call for call in reasoner.calls if call["component"] == "skill")
        assert set(child["context"]) == {
            "task_fragment",
            "constraints",
            "skill",
            "memory",
        }
        assert "orientation" not in child["context"]
        assert "plan" not in child["context"]
        assert "observations" not in child["context"]
        assert child["context"]["task_fragment"]["goal"] == "Solve the isolated subtask"
    ''',
)

write(
    "tests/test_agent_tools.py",
    r'''
    from pathlib import Path

    from agi.tools import ToolDefinition, ToolRegistry, default_tool_registry


    def test_tool_calls_are_idempotent_by_logical_call_id(tmp_path: Path) -> None:
        calls = {"count": 0}

        def increment(root: Path, arguments: dict[str, object]) -> dict[str, int]:
            calls["count"] += 1
            return {"value": calls["count"]}

        registry = ToolRegistry(tmp_path)
        registry.register(ToolDefinition("increment", increment, mutating=True))
        first = registry.execute("increment", {}, run_id="run-1", call_id="step-1")
        second = registry.execute("increment", {}, run_id="run-1", call_id="step-1")
        assert first["output"] == {"value": 1}
        assert second["output"] == {"value": 1}
        assert second["cached"] is True
        assert calls["count"] == 1


    def test_default_tools_restrict_writes_to_artifacts(tmp_path: Path) -> None:
        registry = default_tool_registry(tmp_path)
        denied = registry.execute(
            "write_artifact",
            {"path": "src/forbidden.txt", "content": "x"},
            run_id="run-1",
            call_id="denied",
        )
        assert denied["ok"] is False
        allowed = registry.execute(
            "write_artifact",
            {"path": "artifacts/result.txt", "content": "ok"},
            run_id="run-1",
            call_id="allowed",
        )
        assert allowed["ok"] is True
        assert (tmp_path / "artifacts/result.txt").read_text() == "ok"
    ''',
)

write(
    "tests/test_agent_memory_skills_improvement.py",
    r'''
    from __future__ import annotations

    import json
    from pathlib import Path

    import pytest

    from agi.improvement import CandidateStore
    from agi.memory import Episode, EpisodeStore
    from agi.skills import SkillGraphError, SkillRegistry
    from agi.tasking import TaskRequest, utc_now


    def episode(index: int) -> Episode:
        return Episode(
            episode_id=f"episode-{index}",
            run_id=f"run-{index}",
            task_id=f"task-{index}",
            goal="Solve a reusable coding task",
            tags=("code",),
            status="completed",
            summary="The verifier passed.",
            lessons=("Run the verifier before completion.",),
            failures=(),
            skills=("root-task",),
            created_at=utc_now(),
        )


    def test_three_successful_episodes_create_stable_long_memory(tmp_path: Path) -> None:
        store = EpisodeStore(tmp_path)
        for index in range(3):
            store.append(episode(index))
        views = store.consolidate()
        assert views["long"]["stable_lessons"] == [
            {
                "lesson": "Run the verifier before completion.",
                "successful_occurrences": 3,
            }
        ]
        assert store.retrieve("verifier coding", ["code"])[0].episode_id == "episode-2"


    def test_skill_graph_detects_cycles(tmp_path: Path) -> None:
        directory = tmp_path / ".continual/skills"
        directory.mkdir(parents=True)
        base = {
            "version": 1,
            "description": "cycle test skill",
            "triggers": ["*"],
            "instructions": "test",
            "input_contract": {},
            "output_contract": {},
            "evidence_score": 0,
            "status": "active",
        }
        (directory / "a.json").write_text(
            json.dumps({**base, "skill_id": "a", "children": ["b"]}),
            encoding="utf-8",
        )
        (directory / "b.json").write_text(
            json.dumps({**base, "skill_id": "b", "children": ["a"]}),
            encoding="utf-8",
        )
        with pytest.raises(SkillGraphError):
            SkillRegistry(tmp_path).expand(["a"])


    def test_candidate_is_evaluated_only_on_relevant_tasks_then_adopted(tmp_path: Path) -> None:
        store = CandidateStore(tmp_path)
        candidate = store.add_from_reflection(
            "episode-1",
            [
                {
                    "target": "planner",
                    "proposal": "Use a dependency graph for code tasks.",
                    "trigger_tags": ["code"],
                    "trigger_terms": ["repository"],
                }
            ],
            ["code"],
        )[0]
        relevant = TaskRequest.from_mapping(
            {
                "task_id": "code-task",
                "goal": "Improve this repository",
                "success_criteria": ["tests pass"],
                "tags": ["code"],
            }
        )
        irrelevant = TaskRequest.from_mapping(
            {
                "task_id": "music-task",
                "goal": "Compose a melody",
                "success_criteria": ["melody produced"],
                "tags": ["music"],
            }
        )
        assert [item.candidate_id for item in store.relevant(relevant)] == [candidate.candidate_id]
        assert store.relevant(irrelevant) == []
        for index, domain in enumerate(("code", "planning", "tools"), 1):
            updated = store.record_evaluation(
                candidate.candidate_id,
                task_id=f"task-{index}",
                domain=domain,
                baseline_score=0.5,
                candidate_score=0.6,
                regression_passed=True,
            )
        assert updated.status == "adopted"
        assert all(updated.adopted_scopes.values())
    ''',
)

write(
    "tests/test_agent_benchmark_runner.py",
    r'''
    from __future__ import annotations

    from pathlib import Path

    from agi.agent import GeneralAgent
    from agi.benchmark_runner import (
        BenchmarkCase,
        BenchmarkRunner,
        FunctionVerifier,
        VerifierResult,
    )
    from agi.campaign import CampaignManager
    from agi.reasoner import ScriptedReasoner
    from agi.tasking import TaskRequest


    def reasoner() -> ScriptedReasoner:
        return ScriptedReasoner(
            {
                "orient": [{"summary": "answer"}],
                "plan": [
                    {
                        "steps": [
                            {
                                "step_id": "answer",
                                "description": "Produce 42",
                                "kind": "reason",
                            }
                        ]
                    }
                ],
                "act": [{"answer": 42}],
                "verify": [
                    {
                        "criteria": {"answer equals 42": True},
                        "complete": True,
                        "result": {"answer": 42},
                    }
                ],
                "reflect": [
                    {
                        "summary": "passed",
                        "lessons": [],
                        "failures": [],
                        "improvements": [],
                    }
                ],
            }
        )


    def case(*, qualifies: bool) -> BenchmarkCase:
        return BenchmarkCase(
            case_id="sealed-answer" if qualifies else "demo-answer",
            criterion="breadth",
            domain="quantitative-reasoning",
            task=TaskRequest.from_mapping(
                {
                    "task_id": "benchmark-answer",
                    "goal": "Return 42",
                    "success_criteria": ["answer equals 42"],
                    "tags": ["math"],
                }
            ),
            sealed=qualifies,
            qualifies_for_evidence=qualifies,
        )


    def verifier() -> FunctionVerifier:
        return FunctionVerifier(
            "independent-json-verifier",
            lambda snapshot: VerifierResult(
                passed=snapshot.get("result") == {"answer": 42},
                score=1.0,
            ),
            independent=True,
        )


    def test_demo_benchmark_does_not_pollute_agi_evidence(tmp_path: Path) -> None:
        runner = BenchmarkRunner(tmp_path, GeneralAgent(tmp_path, reasoner()))
        result = runner.run_case(case(qualifies=False), verifier())
        assert result["verifier_result"]["passed"] is True
        assert result["evidence_appended"] is False
        assert not CampaignManager(tmp_path).paths.ledger.exists()


    def test_sealed_independent_benchmark_appends_hash_checked_record(tmp_path: Path) -> None:
        runner = BenchmarkRunner(tmp_path, GeneralAgent(tmp_path, reasoner()))
        result = runner.run_case(case(qualifies=True), verifier())
        assert result["evidence_appended"] is True
        campaign = CampaignManager(tmp_path)
        records = campaign.ledger.load()
        assert len(records) == 1
        assert records[0].task_id == "sealed-answer"
        assert campaign.ledger.verify_artifacts(tmp_path) == []
        assert result["campaign"]["status"] == "continue"
    ''',
)

write(
    "tests/test_agent_cli.py",
    r'''
    import json
    import subprocess
    import sys
    from pathlib import Path


    def test_cli_runs_scripted_task(tmp_path: Path) -> None:
        task = tmp_path / "task.json"
        task.write_text(
            json.dumps(
                {
                    "task_id": "cli-task",
                    "goal": "Return 42",
                    "success_criteria": ["answer equals 42"],
                }
            ),
            encoding="utf-8",
        )
        script = tmp_path / "script.json"
        script.write_text(
            json.dumps(
                {
                    "orient": [{"summary": "orient"}],
                    "plan": [
                        {
                            "steps": [
                                {
                                    "step_id": "answer",
                                    "description": "Return 42",
                                    "kind": "reason",
                                }
                            ]
                        }
                    ],
                    "act": [{"answer": 42}],
                    "verify": [
                        {
                            "criteria": {"answer equals 42": True},
                            "complete": True,
                            "result": {"answer": 42},
                        }
                    ],
                    "reflect": [
                        {
                            "summary": "done",
                            "lessons": [],
                            "failures": [],
                            "improvements": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "agi.agent_cli",
                "--root",
                str(tmp_path),
                "run",
                str(task),
                "--script",
                str(script),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        value = json.loads(completed.stdout)
        assert value["status"] == "completed"
        assert value["result"] == {"answer": 42}
    ''',
)

write(
    "agi/AGENT_RUNTIME.md",
    r'''
    # Resumable general-agent runtime

    The runtime is a capability-construction layer beneath the AGI evidence gate. It
    does not declare itself AGI. It provides a repeatable substrate for testing
    increasingly general behavior:

    1. **Orient** with only the explicit task, retrieved memory, selected skills,
       relevant candidate overlays, and declared tools.
    2. **Plan** into typed reason, tool, or child-skill steps.
    3. **Act** with durable idempotency keys for tools. A child skill receives a fresh
       process/session context containing only its task fragment, contract, bounded
       memory, and immutable constraints.
    4. **Verify** every observable success criterion. Failed verification replans up
       to the task limit, then records a blocked outcome rather than claiming success.
    5. **Reflect and learn once** after either completion or blocking. The episode is
       hash-chained, multi-horizon memory is rebuilt, and narrowly triggered
       improvement candidates are stored. Learning does not recursively call itself.
    6. **Resume** from the persisted phase after any transition budget is exhausted.

    Candidate improvements are evaluated lazily only when a later task matches their
    trigger. A positive result is adopted for that task scope; global adoption
    requires positive, regression-safe evaluations across at least three domains.

    ## CLI

    A reasoner is supplied explicitly as either a JSON response script or a stateless
    JSON-in/JSON-out command:

    ```bash
    agi-agent --root . run task.json --script responses.json
    agi-agent --root . run task.json --reasoner-command "your-reasoner-command"
    agi-agent --root . resume RUN_ID --reasoner-command "your-reasoner-command"
    agi-agent --root . show RUN_ID
    agi-agent --root . memory
    agi-agent --root . candidates
    ```

    The command reasoner is launched without a shell and receives a scrubbed
    environment. Production benchmark evidence is appended only for explicitly
    sealed cases checked by an independent verifier; demos and unit tests cannot
    silently become AGI evidence.
    ''',
)

append_once(
    "README.md",
    "## Resumable general-agent runtime",
    r'''
    ## Resumable general-agent runtime

    `agi-agent` runs an explicit-context orient → plan → act → verify → reflect →
    learn loop with resumable snapshots, context-isolated child skills, idempotent
    tools, multi-horizon episodic memory, and lazily evaluated improvement
    candidates. See `agi/AGENT_RUNTIME.md`. Capability tests remain separate from the
    stricter AGI evidence report.
    ''',
)

append_once(
    "SYSTEM_DESIGN.md",
    "## General-agent capability plane",
    r'''
    ## General-agent capability plane

    The capability plane uses event-sourced run snapshots and explicit context
    envelopes. It selects the same compositional skill set for arbitrary tasks,
    isolates child-skill calls from parent conversation state, persists tool results
    by logical call ID, verifies before completion, learns once after every terminal
    outcome, and evaluates improvement candidates only on later tasks they can
    affect. The benchmark runner is the sole bridge from capability execution into
    the independent evidence ledger.
    ''',
)

append_once(
    "AGENTS.md",
    "## General-agent runtime rules",
    r'''
    ## General-agent runtime rules

    - Preserve run phase and return status `continue` when a transition budget ends;
      never reinterpret a pause as completion.
    - Child skills receive only a task fragment, immutable constraints, the selected
      skill contract, and bounded retrieved memory.
    - Repeated tool calls must reuse their logical call ID so side effects are not
      duplicated after resume.
    - Verify every success criterion before setting a completed outcome.
    - Run learning exactly once after completion or blocking, with the execution
      context still available; learning itself must not trigger recursive learning.
    - Evaluate improvement candidates only on tasks matching their triggers. Keep
      adoption scoped to that task until cross-domain evidence justifies global
      adoption.
    - Do not append benchmark evidence from demos, unit tests, unsealed tasks, or
      non-independent verifiers.
    ''',
)
