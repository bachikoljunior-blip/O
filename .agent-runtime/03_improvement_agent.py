from __future__ import annotations

from pathlib import Path
from textwrap import dedent


def write(path: str, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dedent(content).lstrip(), encoding="utf-8")


write(
    "src/agi/improvement.py",
    r'''
    from __future__ import annotations

    import hashlib
    import json
    import os
    import tempfile
    from dataclasses import asdict, dataclass, field
    from pathlib import Path
    from typing import Any, Iterable, Mapping

    from .evidence import canonical_json
    from .tasking import TaskRequest, json_copy, utc_now


    def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise


    @dataclass(slots=True)
    class ImprovementCandidate:
        candidate_id: str
        target: str
        proposal: str
        trigger_tags: tuple[str, ...]
        trigger_terms: tuple[str, ...]
        created_from_episode: str
        created_at: str
        status: str = "candidate"
        evaluations: list[dict[str, Any]] = field(default_factory=list)
        adopted_scopes: dict[str, bool] = field(default_factory=dict)

        @classmethod
        def from_mapping(cls, value: Mapping[str, Any]) -> "ImprovementCandidate":
            if not isinstance(value, Mapping):
                raise ValueError("candidate must be an object")

            def text(name: str) -> str:
                item = value.get(name)
                if not isinstance(item, str) or not item.strip():
                    raise ValueError(f"candidate {name} must be a non-empty string")
                return item.strip()

            def strings(name: str) -> tuple[str, ...]:
                items = value.get(name, [])
                if not isinstance(items, (list, tuple)) or not all(
                    isinstance(item, str) and item.strip() for item in items
                ):
                    raise ValueError(f"candidate {name} must be a list of strings")
                return tuple(item.strip() for item in items)

            evaluations = value.get("evaluations", [])
            scopes = value.get("adopted_scopes", {})
            if not isinstance(evaluations, list) or not all(
                isinstance(item, Mapping) for item in evaluations
            ):
                raise ValueError("candidate evaluations must be a list of objects")
            if not isinstance(scopes, Mapping) or not all(
                isinstance(key, str) and isinstance(item, bool)
                for key, item in scopes.items()
            ):
                raise ValueError("candidate adopted_scopes must map strings to booleans")
            status = value.get("status", "candidate")
            if status not in {"candidate", "adopted", "rejected"}:
                raise ValueError("candidate status is invalid")
            return cls(
                candidate_id=text("candidate_id"),
                target=text("target"),
                proposal=text("proposal"),
                trigger_tags=strings("trigger_tags"),
                trigger_terms=strings("trigger_terms"),
                created_from_episode=text("created_from_episode"),
                created_at=text("created_at"),
                status=status,
                evaluations=[json_copy(dict(item)) for item in evaluations],
                adopted_scopes=dict(scopes),
            )

        def as_dict(self) -> dict[str, Any]:
            value = asdict(self)
            value["trigger_tags"] = list(self.trigger_tags)
            value["trigger_terms"] = list(self.trigger_terms)
            return value

        def relevant(self, task: TaskRequest) -> bool:
            if self.status == "rejected":
                return False
            tags = {tag.lower() for tag in task.tags}
            goal = task.goal.lower()
            return bool(
                tags & {tag.lower() for tag in self.trigger_tags}
                or any(term.lower() in goal for term in self.trigger_terms)
                or self.adopted_scopes.get(task.task_id)
            )


    class CandidateStore:
        """Lazy per-task evaluation with global adoption only after broad evidence."""

        def __init__(self, root: Path):
            self.root = root.resolve()
            self.path = (
                self.root
                / ".continual"
                / "agi"
                / "improvements"
                / "candidates.json"
            )

        def load(self) -> dict[str, ImprovementCandidate]:
            if not self.path.exists():
                return {}
            value = json.loads(self.path.read_text(encoding="utf-8"))
            raw = value.get("candidates", []) if isinstance(value, Mapping) else []
            if not isinstance(raw, list):
                raise ValueError("candidate index candidates must be a list")
            candidates = [ImprovementCandidate.from_mapping(item) for item in raw]
            return {candidate.candidate_id: candidate for candidate in candidates}

        def _save(self, candidates: Mapping[str, ImprovementCandidate]) -> None:
            _atomic_json(
                self.path,
                {
                    "schema_version": 1,
                    "updated_at": utc_now(),
                    "candidates": [
                        candidate.as_dict()
                        for candidate in sorted(
                            candidates.values(), key=lambda item: item.candidate_id
                        )
                    ],
                },
            )

        def add(self, candidate: ImprovementCandidate) -> ImprovementCandidate:
            candidates = self.load()
            existing = candidates.get(candidate.candidate_id)
            if existing is not None:
                return existing
            candidates[candidate.candidate_id] = candidate
            self._save(candidates)
            return candidate

        def add_from_reflection(
            self,
            episode_id: str,
            proposals: Iterable[Mapping[str, Any]],
            default_tags: Iterable[str],
        ) -> list[ImprovementCandidate]:
            result: list[ImprovementCandidate] = []
            for raw in proposals:
                if not isinstance(raw, Mapping):
                    continue
                target = raw.get("target")
                proposal = raw.get("proposal")
                if not isinstance(target, str) or not target.strip():
                    continue
                if not isinstance(proposal, str) or not proposal.strip():
                    continue
                tags = raw.get("trigger_tags", list(default_tags))
                terms = raw.get("trigger_terms", [])
                if not isinstance(tags, list) or not all(
                    isinstance(item, str) and item.strip() for item in tags
                ):
                    tags = list(default_tags)
                if not isinstance(terms, list) or not all(
                    isinstance(item, str) and item.strip() for item in terms
                ):
                    terms = []
                identity = {
                    "episode_id": episode_id,
                    "target": target.strip(),
                    "proposal": proposal.strip(),
                    "trigger_tags": sorted(item.strip() for item in tags),
                    "trigger_terms": sorted(item.strip() for item in terms),
                }
                candidate_id = "candidate-" + hashlib.sha256(
                    canonical_json(identity).encode("utf-8")
                ).hexdigest()[:16]
                result.append(
                    self.add(
                        ImprovementCandidate(
                            candidate_id=candidate_id,
                            target=target.strip(),
                            proposal=proposal.strip(),
                            trigger_tags=tuple(item.strip() for item in tags),
                            trigger_terms=tuple(item.strip() for item in terms),
                            created_from_episode=episode_id,
                            created_at=utc_now(),
                        )
                    )
                )
            return result

        def relevant(self, task: TaskRequest) -> list[ImprovementCandidate]:
            return [
                candidate
                for candidate in self.load().values()
                if candidate.relevant(task)
            ]

        def overlays(self, task: TaskRequest) -> list[dict[str, Any]]:
            return [
                {
                    "candidate_id": candidate.candidate_id,
                    "target": candidate.target,
                    "proposal": candidate.proposal,
                    "status": candidate.status,
                    "adopted_for_task": candidate.adopted_scopes.get(task.task_id, False),
                }
                for candidate in self.relevant(task)
            ]

        def record_evaluation(
            self,
            candidate_id: str,
            *,
            task_id: str,
            domain: str,
            baseline_score: float,
            candidate_score: float,
            regression_passed: bool,
        ) -> ImprovementCandidate:
            candidates = self.load()
            candidate = candidates.get(candidate_id)
            if candidate is None:
                raise KeyError(candidate_id)
            if any(item.get("task_id") == task_id for item in candidate.evaluations):
                return candidate
            delta = float(candidate_score) - float(baseline_score)
            evaluation = {
                "task_id": task_id,
                "domain": domain,
                "baseline_score": float(baseline_score),
                "candidate_score": float(candidate_score),
                "delta": delta,
                "regression_passed": bool(regression_passed),
                "evaluated_at": utc_now(),
            }
            candidate.evaluations.append(evaluation)
            candidate.adopted_scopes[task_id] = delta > 0 and regression_passed

            positive = [
                item
                for item in candidate.evaluations
                if item["delta"] > 0 and item["regression_passed"]
            ]
            domains = {item["domain"] for item in positive}
            mean_delta = (
                sum(item["delta"] for item in candidate.evaluations)
                / len(candidate.evaluations)
            )
            if len(positive) >= 3 and len(domains) >= 3 and mean_delta >= 0.05:
                candidate.status = "adopted"
            elif len(candidate.evaluations) >= 3 and mean_delta <= 0:
                candidate.status = "rejected"
            candidates[candidate_id] = candidate
            self._save(candidates)
            return candidate
    ''',
)

write(
    "src/agi/agent.py",
    r'''
    from __future__ import annotations

    import hashlib
    import json
    import os
    import tempfile
    import uuid
    from pathlib import Path
    from typing import Any, Mapping

    from .evidence import canonical_json
    from .improvement import CandidateStore
    from .memory import Episode, EpisodeStore
    from .reasoner import Reasoner
    from .skills import SkillRegistry
    from .tasking import AgentPlan, PlanStep, TaskRequest, json_copy, utc_now
    from .tools import ToolRegistry, default_tool_registry


    class RunConflictError(RuntimeError):
        pass


    class AgentRunStore:
        def __init__(self, root: Path):
            self.root = root.resolve()
            self.directory = self.root / ".continual" / "agi" / "agent-runs"

        def run_dir(self, run_id: str) -> Path:
            return self.directory / run_id

        def _atomic(self, path: Path, value: Mapping[str, Any]) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            except BaseException:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
                raise

        def create(self, task: TaskRequest) -> dict[str, Any]:
            run_id = f"run-{uuid.uuid4().hex[:16]}"
            snapshot = {
                "schema_version": 1,
                "run_id": run_id,
                "task": task.as_dict(),
                "phase": "orient",
                "status": "running",
                "revision": 0,
                "transitions": 0,
                "orientation": {},
                "plan": None,
                "step_index": 0,
                "plan_revisions": 0,
                "observations": [],
                "verification": {},
                "reflection": {},
                "result": None,
                "errors": [],
                "selected_skills": [],
                "memory_context": [],
                "candidate_overlays": [],
                "learned": False,
                "outcome_status": None,
                "pause_reason": None,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            path = self.run_dir(run_id) / "snapshot.json"
            if path.exists():
                raise FileExistsError(run_id)
            self._atomic(path, snapshot)
            self.append_event(run_id, {"type": "run_created", "task_id": task.task_id})
            return json_copy(snapshot)

        def load(self, run_id: str) -> dict[str, Any]:
            path = self.run_dir(run_id) / "snapshot.json"
            if not path.exists():
                raise FileNotFoundError(run_id)
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("run snapshot must be an object")
            return value

        def save(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
            value = json_copy(dict(snapshot))
            run_id = value.get("run_id")
            if not isinstance(run_id, str):
                raise ValueError("snapshot run_id is missing")
            current = self.load(run_id)
            expected = value.get("revision")
            if expected != current.get("revision"):
                raise RunConflictError(
                    f"run {run_id} revision conflict: expected {expected}, "
                    f"current {current.get('revision')}"
                )
            value["revision"] = int(expected) + 1
            value["updated_at"] = utc_now()
            self._atomic(self.run_dir(run_id) / "snapshot.json", value)
            return json_copy(value)

        def append_event(self, run_id: str, event: Mapping[str, Any]) -> None:
            path = self.run_dir(run_id) / "events.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            value = {"at": utc_now(), **json_copy(dict(event))}
            with path.open("a", encoding="utf-8") as handle:
                handle.write(canonical_json(value) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

        def write_context(
            self,
            run_id: str,
            component: str,
            context: Mapping[str, Any],
        ) -> dict[str, Any]:
            directory = self.run_dir(run_id) / "contexts"
            directory.mkdir(parents=True, exist_ok=True)
            index = len(list(directory.glob("*.json")))
            safe = json_copy(dict(context))
            digest = hashlib.sha256(canonical_json(safe).encode("utf-8")).hexdigest()
            value = {"component": component, "digest": digest, "context": safe}
            self._atomic(directory / f"{index:04d}-{component}.json", value)
            return {"component": component, "digest": digest, "index": index}


    class GeneralAgent:
        """Resumable orient-plan-act-verify-reflect-learn agent.

        Reasoner calls are stateless and receive explicit bounded context. Child skill
        calls use a separate envelope that excludes the parent plan, orientation, and
        full observation history.
        """

        def __init__(
            self,
            root: Path,
            reasoner: Reasoner,
            *,
            tools: ToolRegistry | None = None,
            memory: EpisodeStore | None = None,
            skills: SkillRegistry | None = None,
            candidates: CandidateStore | None = None,
        ):
            self.root = root.resolve()
            self.reasoner = reasoner
            self.store = AgentRunStore(self.root)
            self.tools = tools or default_tool_registry(self.root)
            self.memory = memory or EpisodeStore(self.root)
            self.skills = skills or SkillRegistry(self.root)
            self.candidates = candidates or CandidateStore(self.root)

        def start(self, task: TaskRequest | Mapping[str, Any]) -> str:
            request = task if isinstance(task, TaskRequest) else TaskRequest.from_mapping(task)
            return self.store.create(request)["run_id"]

        def _task(self, snapshot: Mapping[str, Any]) -> TaskRequest:
            return TaskRequest.from_mapping(snapshot["task"])

        def _commit(self, snapshot: dict[str, Any], event: str) -> dict[str, Any]:
            snapshot["transitions"] = int(snapshot.get("transitions", 0)) + 1
            saved = self.store.save(snapshot)
            self.store.append_event(
                saved["run_id"],
                {"type": event, "phase": saved["phase"], "status": saved["status"]},
            )
            return saved

        def _reason(
            self,
            snapshot: Mapping[str, Any],
            component: str,
            context: Mapping[str, Any],
        ) -> dict[str, Any]:
            self.store.write_context(snapshot["run_id"], component, context)
            value = self.reasoner.reason(component, json_copy(dict(context)))
            if not isinstance(value, Mapping):
                raise ValueError(f"{component} response must be an object")
            return json_copy(dict(value))

        @staticmethod
        def _string_list(value: Any) -> list[str]:
            if not isinstance(value, list):
                return []
            return [item.strip() for item in value if isinstance(item, str) and item.strip()]

        def _orient(self, snapshot: dict[str, Any]) -> dict[str, Any]:
            task = self._task(snapshot)
            memory_context = self.memory.context(task.goal, task.tags)
            skill_context = self.skills.context(task)
            overlays = self.candidates.overlays(task)
            context = {
                "task": task.as_dict(),
                "phase": "orient",
                "memory": memory_context,
                "skills": skill_context,
                "candidate_overlays": overlays,
                "available_tools": self.tools.describe(),
            }
            response = self._reason(snapshot, "orient", context)
            snapshot["orientation"] = response
            snapshot["memory_context"] = memory_context
            snapshot["selected_skills"] = [item["skill_id"] for item in skill_context]
            snapshot["candidate_overlays"] = overlays
            snapshot["phase"] = "plan"
            return self._commit(snapshot, "oriented")

        def _plan(self, snapshot: dict[str, Any]) -> dict[str, Any]:
            task = self._task(snapshot)
            context = {
                "task": task.as_dict(),
                "phase": "plan",
                "orientation": snapshot.get("orientation", {}),
                "previous_verification": snapshot.get("verification", {}),
                "skills": snapshot.get("selected_skills", []),
                "available_tools": self.tools.describe(),
                "plan_revision": snapshot.get("plan_revisions", 0),
            }
            response = self._reason(snapshot, "plan", context)
            plan = AgentPlan.from_mapping(response)
            snapshot["plan"] = plan.as_dict()
            snapshot["step_index"] = 0
            snapshot["phase"] = "act"
            return self._commit(snapshot, "planned")

        def _skill_context(
            self,
            task: TaskRequest,
            step: PlanStep,
        ) -> dict[str, Any]:
            skills = self.skills.load()
            skill = skills.get(step.skill_id or "")
            if skill is None:
                raise KeyError(f"unknown skill: {step.skill_id}")
            return {
                "task_fragment": {
                    "step_id": step.step_id,
                    "goal": step.description,
                    "success_hint": step.success_hint,
                },
                "constraints": list(task.constraints),
                "skill": skill.as_dict(),
                "memory": self.memory.context(
                    step.description, task.tags, character_budget=1200
                ),
            }

        def _act(self, snapshot: dict[str, Any]) -> dict[str, Any]:
            task = self._task(snapshot)
            plan = AgentPlan.from_mapping(snapshot.get("plan") or {})
            index = int(snapshot.get("step_index", 0))
            if index >= len(plan.steps):
                snapshot["phase"] = "verify"
                return self._commit(snapshot, "actions_exhausted")
            step = plan.steps[index]
            if step.kind == "tool":
                output = self.tools.execute(
                    step.tool or "",
                    step.arguments,
                    run_id=snapshot["run_id"],
                    call_id=step.step_id,
                )
            elif step.kind == "skill":
                output = self._reason(
                    snapshot,
                    "skill",
                    self._skill_context(task, step),
                )
            else:
                output = self._reason(
                    snapshot,
                    "act",
                    {
                        "task": {
                            "goal": task.goal,
                            "success_criteria": list(task.success_criteria),
                            "constraints": list(task.constraints),
                        },
                        "step": step.as_dict(),
                        "recent_observations": snapshot.get("observations", [])[-3:],
                    },
                )
            observations = list(snapshot.get("observations", []))
            observations.append(
                {
                    "step_id": step.step_id,
                    "kind": step.kind,
                    "output": json_copy(output),
                    "at": utc_now(),
                }
            )
            snapshot["observations"] = observations
            snapshot["step_index"] = index + 1
            snapshot["phase"] = (
                "verify" if snapshot["step_index"] >= len(plan.steps) else "act"
            )
            return self._commit(snapshot, "step_executed")

        def _verify(self, snapshot: dict[str, Any]) -> dict[str, Any]:
            task = self._task(snapshot)
            context = {
                "task": {
                    "goal": task.goal,
                    "success_criteria": list(task.success_criteria),
                    "constraints": list(task.constraints),
                },
                "observations": snapshot.get("observations", []),
                "phase": "verify",
            }
            response = self._reason(snapshot, "verify", context)
            raw_criteria = response.get("criteria")
            if not isinstance(raw_criteria, Mapping) or not all(
                isinstance(key, str) and isinstance(value, bool)
                for key, value in raw_criteria.items()
            ):
                raise ValueError("verify criteria must map strings to booleans")
            complete = response.get("complete")
            if not isinstance(complete, bool):
                raise ValueError("verify complete must be a boolean")
            criteria = dict(raw_criteria)
            verified = (
                complete
                and len(criteria) >= len(task.success_criteria)
                and all(criteria.values())
            )
            verification = {
                **response,
                "criteria": criteria,
                "complete": verified,
            }
            snapshot["verification"] = json_copy(verification)
            snapshot["result"] = json_copy(
                response.get(
                    "result",
                    snapshot.get("observations", [{}])[-1].get("output")
                    if snapshot.get("observations")
                    else None,
                )
            )
            if verified:
                snapshot["outcome_status"] = "completed"
                snapshot["phase"] = "reflect"
            elif int(snapshot.get("plan_revisions", 0)) < task.max_plan_revisions:
                snapshot["plan_revisions"] = int(snapshot.get("plan_revisions", 0)) + 1
                snapshot["phase"] = "plan"
            else:
                snapshot["outcome_status"] = "blocked"
                snapshot["phase"] = "reflect"
            return self._commit(snapshot, "verified")

        def _reflect(self, snapshot: dict[str, Any]) -> dict[str, Any]:
            task = self._task(snapshot)
            response = self._reason(
                snapshot,
                "reflect",
                {
                    "task": task.as_dict(),
                    "outcome_status": snapshot.get("outcome_status") or "blocked",
                    "result": snapshot.get("result"),
                    "verification": snapshot.get("verification", {}),
                    "observations": snapshot.get("observations", []),
                    "errors": snapshot.get("errors", []),
                },
            )
            improvements = response.get("improvements", [])
            if not isinstance(improvements, list):
                improvements = []
            snapshot["reflection"] = {
                "summary": (
                    response.get("summary", "Task execution finished.").strip()
                    if isinstance(response.get("summary", ""), str)
                    else "Task execution finished."
                ),
                "lessons": self._string_list(response.get("lessons", [])),
                "failures": self._string_list(response.get("failures", [])),
                "improvements": [
                    json_copy(dict(item))
                    for item in improvements
                    if isinstance(item, Mapping)
                ],
            }
            snapshot["phase"] = "learn"
            return self._commit(snapshot, "reflected")

        def _learn(self, snapshot: dict[str, Any]) -> dict[str, Any]:
            task = self._task(snapshot)
            episode_id = f"episode-{snapshot['run_id']}"
            if not snapshot.get("learned"):
                existing = {episode.episode_id for episode in self.memory.load()}
                reflection = snapshot.get("reflection", {})
                if episode_id not in existing:
                    self.memory.append(
                        Episode(
                            episode_id=episode_id,
                            run_id=snapshot["run_id"],
                            task_id=task.task_id,
                            goal=task.goal,
                            tags=task.tags,
                            status=snapshot.get("outcome_status") or "blocked",
                            summary=reflection.get("summary") or "Task execution finished.",
                            lessons=tuple(reflection.get("lessons", [])),
                            failures=tuple(reflection.get("failures", [])),
                            skills=tuple(snapshot.get("selected_skills", [])),
                            created_at=utc_now(),
                        )
                    )
                self.memory.consolidate()
                self.candidates.add_from_reflection(
                    episode_id,
                    reflection.get("improvements", []),
                    task.tags,
                )
                snapshot["learned"] = True
            snapshot["phase"] = "complete"
            snapshot["status"] = (
                "completed"
                if snapshot.get("outcome_status") == "completed"
                else "blocked"
            )
            snapshot["pause_reason"] = None
            return self._commit(snapshot, "learned")

        def _transition(self, snapshot: dict[str, Any]) -> dict[str, Any]:
            phase = snapshot.get("phase")
            if phase == "orient":
                return self._orient(snapshot)
            if phase == "plan":
                return self._plan(snapshot)
            if phase == "act":
                return self._act(snapshot)
            if phase == "verify":
                return self._verify(snapshot)
            if phase == "reflect":
                return self._reflect(snapshot)
            if phase == "learn":
                return self._learn(snapshot)
            if phase == "complete":
                return snapshot
            raise ValueError(f"unknown run phase: {phase}")

        def run(self, run_id: str, *, max_transitions: int = 50) -> dict[str, Any]:
            if max_transitions < 1:
                raise ValueError("max_transitions must be positive")
            snapshot = self.store.load(run_id)
            if snapshot.get("phase") == "complete":
                return snapshot
            if snapshot.get("status") == "continue":
                snapshot["status"] = "running"
                snapshot["pause_reason"] = None
                snapshot = self.store.save(snapshot)
            for _ in range(max_transitions):
                if snapshot.get("phase") == "complete":
                    return snapshot
                try:
                    snapshot = self._transition(snapshot)
                except Exception as exc:
                    errors = list(snapshot.get("errors", []))
                    errors.append(
                        {
                            "phase": snapshot.get("phase"),
                            "type": type(exc).__name__,
                            "message": str(exc),
                            "at": utc_now(),
                        }
                    )
                    snapshot["errors"] = errors
                    snapshot["outcome_status"] = "blocked"
                    if snapshot.get("phase") == "reflect":
                        snapshot["reflection"] = {
                            "summary": "Reflection failed; the execution error was preserved.",
                            "lessons": [],
                            "failures": [str(exc)],
                            "improvements": [],
                        }
                        snapshot["phase"] = "learn"
                    elif snapshot.get("phase") == "learn":
                        snapshot["phase"] = "complete"
                        snapshot["status"] = "blocked"
                    else:
                        snapshot["phase"] = "reflect"
                    snapshot = self._commit(snapshot, "transition_error")
            if snapshot.get("phase") != "complete":
                snapshot["status"] = "continue"
                snapshot["pause_reason"] = "transition_budget_exhausted"
                snapshot = self.store.save(snapshot)
                self.store.append_event(
                    run_id,
                    {
                        "type": "run_paused",
                        "reason": "transition_budget_exhausted",
                        "phase": snapshot.get("phase"),
                    },
                )
            return snapshot
    ''',
)
