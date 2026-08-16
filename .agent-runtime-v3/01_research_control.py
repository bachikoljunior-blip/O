from __future__ import annotations

from pathlib import Path
from textwrap import dedent


def write(path: str, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dedent(content).lstrip(), encoding="utf-8")


write(
    "src/agi/verifiers.py",
    r'''
    from __future__ import annotations

    import json
    import os
    import subprocess
    from pathlib import Path
    from typing import Any, Mapping, Sequence

    from .benchmark_runner import VerifierResult
    from .tasking import json_copy


    class VerifierError(RuntimeError):
        """Raised when a verifier cannot produce a valid independent result."""


    def _safe_environment() -> dict[str, str]:
        allowed = {
            "PATH",
            "HOME",
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "TMPDIR",
            "TEMP",
            "TMP",
            "SYSTEMROOT",
            "COMSPEC",
            "PATHEXT",
            "VIRTUAL_ENV",
            "PYTHONPATH",
        }
        return {key: value for key, value in os.environ.items() if key in allowed}


    class JsonCommandVerifier:
        """Fresh-process JSON verifier with an explicit independence declaration.

        The command receives one snapshot object on standard input and must emit one
        object containing `passed`, `score`, and optionally `details`.  No shell is
        used.  Declaring independence is a configuration assertion that still needs
        organizational or cryptographic provenance outside this class.
        """

        def __init__(
            self,
            root: Path,
            name: str,
            command: Sequence[str],
            *,
            independent: bool,
            timeout_seconds: int = 120,
        ):
            if not isinstance(name, str) or not name.strip():
                raise ValueError("verifier name cannot be empty")
            if not command or not all(isinstance(item, str) and item for item in command):
                raise ValueError("verifier command must contain executable arguments")
            if timeout_seconds < 1 or timeout_seconds > 600:
                raise ValueError("verifier timeout must be between 1 and 600 seconds")
            self.root = root.resolve()
            self.name = name.strip()
            self.command = tuple(command)
            self.independent = bool(independent)
            self.timeout_seconds = timeout_seconds

        def verify(self, snapshot: Mapping[str, Any]) -> VerifierResult:
            request = json.dumps(json_copy(dict(snapshot)), ensure_ascii=False)
            try:
                completed = subprocess.run(
                    list(self.command),
                    cwd=self.root,
                    env=_safe_environment(),
                    input=request,
                    text=True,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise VerifierError(f"verifier command failed: {exc}") from exc
            if completed.returncode != 0:
                raise VerifierError(
                    f"verifier exited {completed.returncode}: "
                    f"{completed.stderr.strip()[-2000:]}"
                )
            try:
                value = json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                raise VerifierError("verifier did not return one JSON object") from exc
            if not isinstance(value, Mapping):
                raise VerifierError("verifier output must be an object")
            passed = value.get("passed")
            score = value.get("score")
            details = value.get("details", {})
            if not isinstance(passed, bool):
                raise VerifierError("verifier passed must be a boolean")
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise VerifierError("verifier score must be numeric")
            if not isinstance(details, Mapping):
                raise VerifierError("verifier details must be an object")
            return VerifierResult(
                passed=passed,
                score=float(score),
                details=json_copy(dict(details)),
            )
    ''',
)

write(
    "src/agi/research.py",
    r'''
    from __future__ import annotations

    import hashlib
    import json
    import os
    import tempfile
    from dataclasses import asdict, dataclass, field
    from pathlib import Path
    from typing import Any, Mapping, Sequence

    from .agent import GeneralAgent
    from .benchmark_runner import BenchmarkCase, BenchmarkRunner
    from .campaign import CampaignManager
    from .evidence import CRITERION_KEYS, canonical_json, utc_now
    from .reasoner import Reasoner
    from .tasking import TaskRequest, json_copy
    from .verifiers import JsonCommandVerifier


    class ResearchQueueError(RuntimeError):
        pass


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
    class ResearchItem:
        item_id: str
        milestone_id: str
        criterion: str
        domain: str
        goal: str
        verifier_hint: str
        priority: float
        sealed_required: bool
        status: str = "pending"
        dependencies: tuple[str, ...] = ()
        attempts: int = 0
        max_attempts: int = 3
        binding: dict[str, Any] | None = None
        result: dict[str, Any] | None = None
        blockers: list[str] = field(default_factory=list)
        created_at: str = field(default_factory=utc_now)
        updated_at: str = field(default_factory=utc_now)

        @classmethod
        def from_mapping(cls, value: Mapping[str, Any]) -> "ResearchItem":
            if not isinstance(value, Mapping):
                raise ResearchQueueError("research item must be an object")

            def text(name: str) -> str:
                item = value.get(name)
                if not isinstance(item, str) or not item.strip():
                    raise ResearchQueueError(f"research item {name} must be text")
                return item.strip()

            criterion = text("criterion")
            if criterion not in CRITERION_KEYS:
                raise ResearchQueueError(f"unknown criterion: {criterion}")
            status = value.get("status", "pending")
            if status not in {
                "pending",
                "running",
                "completed",
                "blocked",
                "satisfied",
            }:
                raise ResearchQueueError(f"invalid research status: {status}")
            priority = value.get("priority", 0.0)
            attempts = value.get("attempts", 0)
            maximum = value.get("max_attempts", 3)
            if isinstance(priority, bool) or not isinstance(priority, (int, float)):
                raise ResearchQueueError("research priority must be numeric")
            if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
                raise ResearchQueueError("research attempts must be non-negative")
            if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
                raise ResearchQueueError("research max_attempts must be positive")
            dependencies = value.get("dependencies", [])
            blockers = value.get("blockers", [])
            if not isinstance(dependencies, (list, tuple)) or not all(
                isinstance(item, str) and item for item in dependencies
            ):
                raise ResearchQueueError("research dependencies must be strings")
            if not isinstance(blockers, list) or not all(
                isinstance(item, str) and item for item in blockers
            ):
                raise ResearchQueueError("research blockers must be strings")
            binding = value.get("binding")
            result = value.get("result")
            if binding is not None and not isinstance(binding, Mapping):
                raise ResearchQueueError("research binding must be an object")
            if result is not None and not isinstance(result, Mapping):
                raise ResearchQueueError("research result must be an object")
            sealed_required = value.get("sealed_required", False)
            if not isinstance(sealed_required, bool):
                raise ResearchQueueError("sealed_required must be boolean")
            return cls(
                item_id=text("item_id"),
                milestone_id=text("milestone_id"),
                criterion=criterion,
                domain=text("domain"),
                goal=text("goal"),
                verifier_hint=text("verifier_hint"),
                priority=float(priority),
                sealed_required=sealed_required,
                status=status,
                dependencies=tuple(dependencies),
                attempts=attempts,
                max_attempts=maximum,
                binding=json_copy(dict(binding)) if isinstance(binding, Mapping) else None,
                result=json_copy(dict(result)) if isinstance(result, Mapping) else None,
                blockers=list(blockers),
                created_at=text("created_at"),
                updated_at=text("updated_at"),
            )

        def as_dict(self) -> dict[str, Any]:
            value = asdict(self)
            value["dependencies"] = list(self.dependencies)
            return value


    class ResearchQueue:
        """Persistent work queue derived from unmet evidence criteria.

        Queue synchronization never lowers the campaign policy and never interprets
        a missing executable binding as completed research.  It persists the blocker
        and keeps the overall state at `continue`.
        """

        def __init__(self, root: Path):
            self.root = root.resolve()
            self.path = self.root / ".continual" / "agi" / "research" / "queue.json"
            self.campaign = CampaignManager(self.root)

        def _empty(self) -> dict[str, Any]:
            return {
                "schema_version": 1,
                "status": "continue",
                "updated_at": utc_now(),
                "items": [],
                "cycles": [],
                "blockers": [],
            }

        def load(self) -> dict[str, Any]:
            if not self.path.exists():
                return self._empty()
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ResearchQueueError("research queue root must be an object")
            raw = value.get("items", [])
            if not isinstance(raw, list):
                raise ResearchQueueError("research queue items must be a list")
            items = [ResearchItem.from_mapping(item).as_dict() for item in raw]
            cycles = value.get("cycles", [])
            blockers = value.get("blockers", [])
            if not isinstance(cycles, list) or not isinstance(blockers, list):
                raise ResearchQueueError("research queue history is malformed")
            return {
                "schema_version": 1,
                "status": value.get("status", "continue"),
                "updated_at": value.get("updated_at", utc_now()),
                "items": items,
                "cycles": json_copy(cycles),
                "blockers": json_copy(blockers),
            }

        def save(self, state: Mapping[str, Any]) -> dict[str, Any]:
            value = json_copy(dict(state))
            value["schema_version"] = 1
            value["updated_at"] = utc_now()
            _atomic_json(self.path, value)
            return value

        @staticmethod
        def _priority(
            criterion: str,
            diagnostics: Mapping[str, Any],
            position: int,
        ) -> float:
            diagnostic = diagnostics.get(criterion, {})
            numbers = [
                float(value)
                for key, value in diagnostic.items()
                if isinstance(value, (int, float))
                and not isinstance(value, bool)
                and key.startswith("required_")
            ]
            current = [
                float(value)
                for key, value in diagnostic.items()
                if isinstance(value, (int, float))
                and not isinstance(value, bool)
                and not key.startswith("required_")
                and key not in {"minimum_steps", "minimum_recovered_failures"}
            ]
            deficit = max(numbers or [1.0]) - max(current or [0.0])
            return 1000.0 + max(0.0, deficit) * 10.0 - position

        @staticmethod
        def _blockers(item: ResearchItem, completed: set[str]) -> list[str]:
            blockers: list[str] = []
            missing_dependencies = [
                dependency for dependency in item.dependencies if dependency not in completed
            ]
            if missing_dependencies:
                blockers.append(
                    "dependencies incomplete: " + ", ".join(sorted(missing_dependencies))
                )
            if item.binding is None:
                blockers.append("executable task and verifier binding missing")
                return blockers
            binding = item.binding
            task = binding.get("task")
            verifier = binding.get("verifier")
            if not isinstance(task, Mapping):
                blockers.append("binding task is missing")
            else:
                try:
                    TaskRequest.from_mapping(task)
                except Exception as exc:
                    blockers.append(f"task binding invalid: {exc}")
            if not isinstance(verifier, Mapping):
                blockers.append("binding verifier is missing")
            else:
                command = verifier.get("command")
                if not isinstance(command, list) or not command or not all(
                    isinstance(value, str) and value for value in command
                ):
                    blockers.append("verifier command is missing")
                if not verifier.get("independent", False):
                    blockers.append("verifier is not declared independent")
            qualifies = bool(binding.get("qualifies_for_evidence", False))
            sealed = bool(binding.get("sealed", False))
            held_out = bool(binding.get("held_out", False))
            if qualifies and not sealed:
                blockers.append("qualifying benchmark is not sealed")
            if item.sealed_required and not sealed:
                blockers.append("milestone requires a sealed task")
            if item.criterion == "transfer" and qualifies and not held_out:
                blockers.append("qualifying transfer task is not held out")
            return blockers

        def sync(self) -> dict[str, Any]:
            campaign = self.campaign.initialize()
            state = self.load()
            existing = {
                item["milestone_id"]: ResearchItem.from_mapping(item)
                for item in state["items"]
            }
            diagnostics = campaign["report"].get("diagnostics", {})
            missing = set(campaign["report"].get("missing", []))
            for position, milestone in enumerate(campaign.get("next_milestones", [])):
                milestone_id = str(milestone["task_id"])
                if milestone_id in existing:
                    continue
                digest = hashlib.sha256(milestone_id.encode("utf-8")).hexdigest()[:12]
                criterion = str(milestone["criterion"])
                existing[milestone_id] = ResearchItem(
                    item_id=f"research-{digest}",
                    milestone_id=milestone_id,
                    criterion=criterion,
                    domain=str(milestone["domain"]),
                    goal=str(milestone["description"]),
                    verifier_hint=str(milestone["verifier"]),
                    priority=self._priority(criterion, diagnostics, position),
                    sealed_required=criterion == "transfer",
                )
            completed = {
                item.milestone_id
                for item in existing.values()
                if item.status in {"completed", "satisfied"}
            }
            blockers: list[dict[str, Any]] = []
            for item in existing.values():
                if item.criterion not in missing and item.status not in {"completed", "satisfied"}:
                    item.status = "satisfied"
                    item.blockers = []
                    item.updated_at = utc_now()
                    completed.add(item.milestone_id)
                    continue
                if item.status in {"completed", "satisfied"}:
                    continue
                item.blockers = self._blockers(item, completed)
                if item.blockers:
                    blockers.append(
                        {"item_id": item.item_id, "reasons": list(item.blockers)}
                    )
                    if item.attempts >= item.max_attempts:
                        item.status = "blocked"
                    elif item.status == "running":
                        item.status = "pending"
                elif item.status == "blocked" and item.attempts < item.max_attempts:
                    item.status = "pending"
                item.updated_at = utc_now()
            ordered = sorted(
                existing.values(), key=lambda item: (-item.priority, item.item_id)
            )
            state["items"] = [item.as_dict() for item in ordered]
            state["blockers"] = blockers
            state["status"] = (
                "verified"
                if campaign["report"]["agi_claim_supported"]
                else "continue"
            )
            return self.save(state)

        def bind(self, item_id: str, binding: Mapping[str, Any]) -> dict[str, Any]:
            state = self.load()
            found = False
            for raw in state["items"]:
                item = ResearchItem.from_mapping(raw)
                if item.item_id != item_id:
                    continue
                found = True
                item.binding = json_copy(dict(binding))
                item.status = "pending"
                item.blockers = []
                item.updated_at = utc_now()
                raw.clear()
                raw.update(item.as_dict())
                break
            if not found:
                raise KeyError(item_id)
            self.save(state)
            return self.sync()

        def next_runnable(self) -> ResearchItem | None:
            state = self.sync()
            completed = {
                item["milestone_id"]
                for item in state["items"]
                if item["status"] in {"completed", "satisfied"}
            }
            for raw in state["items"]:
                item = ResearchItem.from_mapping(raw)
                if item.status != "pending" or item.attempts >= item.max_attempts:
                    continue
                if self._blockers(item, completed):
                    continue
                return item
            return None

        def update_item(
            self,
            item_id: str,
            *,
            status: str,
            result: Mapping[str, Any] | None = None,
            increment_attempt: bool = False,
        ) -> dict[str, Any]:
            state = self.load()
            found = False
            for raw in state["items"]:
                item = ResearchItem.from_mapping(raw)
                if item.item_id != item_id:
                    continue
                found = True
                item.status = status
                if increment_attempt:
                    item.attempts += 1
                if result is not None:
                    item.result = json_copy(dict(result))
                item.updated_at = utc_now()
                raw.clear()
                raw.update(item.as_dict())
                break
            if not found:
                raise KeyError(item_id)
            return self.save(state)

        def append_cycle(self, value: Mapping[str, Any]) -> dict[str, Any]:
            state = self.load()
            cycles = list(state.get("cycles", []))
            cycles.append(json_copy(dict(value)))
            state["cycles"] = cycles[-100:]
            return self.save(state)


    class ResearchController:
        """Execute one bounded, verifier-bound research item and persist the result."""

        def __init__(self, root: Path, reasoner: Reasoner):
            self.root = root.resolve()
            self.reasoner = reasoner
            self.queue = ResearchQueue(self.root)

        def cycle(self) -> dict[str, Any]:
            item = self.queue.next_runnable()
            if item is None:
                state = self.queue.sync()
                result = {
                    "status": "continue",
                    "executed": False,
                    "reason": "no verifier-bound runnable research item",
                    "blockers": state.get("blockers", []),
                    "at": utc_now(),
                }
                self.queue.append_cycle(result)
                return result
            binding = item.binding or {}
            task = TaskRequest.from_mapping(binding["task"])
            verifier_config = binding["verifier"]
            verifier = JsonCommandVerifier(
                self.root,
                str(verifier_config["name"]),
                list(verifier_config["command"]),
                independent=bool(verifier_config.get("independent", False)),
                timeout_seconds=int(verifier_config.get("timeout_seconds", 120)),
            )
            case = BenchmarkCase(
                case_id=item.milestone_id,
                criterion=item.criterion,
                domain=item.domain,
                task=task,
                sealed=bool(binding.get("sealed", False)),
                qualifies_for_evidence=bool(
                    binding.get("qualifies_for_evidence", False)
                ),
                held_out=bool(binding.get("held_out", False)),
                perturbations=tuple(binding.get("perturbations", [])),
                evidence_metadata=json_copy(
                    dict(binding.get("evidence_metadata", {}))
                ),
            )
            self.queue.update_item(
                item.item_id, status="running", increment_attempt=True
            )
            started_at = utc_now()
            try:
                runner = BenchmarkRunner(
                    self.root, GeneralAgent(self.root, self.reasoner)
                )
                output = runner.run_case(case, verifier)
                completed = (
                    output["snapshot"].get("status") == "completed"
                    and output["verifier_result"].get("passed") is True
                )
                result_summary = {
                    "case_id": output["case_id"],
                    "run_id": output["run_id"],
                    "artifact": output["artifact"],
                    "verifier_result": output["verifier_result"],
                    "evidence_appended": output["evidence_appended"],
                    "evidence_id": output["evidence_id"],
                    "qualification_reason": output["qualification_reason"],
                }
                self.queue.update_item(
                    item.item_id,
                    status="completed" if completed else "pending",
                    result=result_summary,
                )
                cycle = {
                    "status": "continue",
                    "executed": True,
                    "item_id": item.item_id,
                    "milestone_id": item.milestone_id,
                    "completed": completed,
                    "started_at": started_at,
                    "finished_at": utc_now(),
                    "result": result_summary,
                }
            except Exception as exc:
                refreshed = next(
                    ResearchItem.from_mapping(raw)
                    for raw in self.queue.load()["items"]
                    if raw["item_id"] == item.item_id
                )
                terminal = refreshed.attempts >= refreshed.max_attempts
                failure = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "at": utc_now(),
                }
                self.queue.update_item(
                    item.item_id,
                    status="blocked" if terminal else "pending",
                    result={"failure": failure},
                )
                cycle = {
                    "status": "continue",
                    "executed": True,
                    "item_id": item.item_id,
                    "milestone_id": item.milestone_id,
                    "completed": False,
                    "started_at": started_at,
                    "finished_at": utc_now(),
                    "failure": failure,
                }
            self.queue.append_cycle(cycle)
            self.queue.sync()
            return cycle
    ''',
)

write(
    ".github/workflows/agi-research-heartbeat.yml",
    r'''
    name: AGI research queue heartbeat

    on:
      schedule:
        - cron: "23 * * * *"
      workflow_dispatch:

    permissions:
      contents: read

    concurrency:
      group: agi-research-heartbeat
      cancel-in-progress: true

    jobs:
      inspect:
        runs-on: ubuntu-latest
        timeout-minutes: 10
        steps:
          - uses: actions/checkout@v4
          - uses: actions/setup-python@v5
            with:
              python-version: "3.11"
          - run: python -m pip install -e .
          - name: Reconstruct unmet research queue
            run: python -m agi.research_cli --root . sync > /tmp/research.json
          - name: Report without fabricating progress
            shell: bash
            run: |
              python - <<'PY' >> "$GITHUB_STEP_SUMMARY"
              import json
              from pathlib import Path
              value = json.loads(Path('/tmp/research.json').read_text(encoding='utf-8'))
              pending = sum(item['status'] == 'pending' for item in value['items'])
              blocked = sum(bool(item.get('blockers')) for item in value['items'])
              print('# AGI research queue')
              print()
              print(f"- status: `{value['status']}`")
              print(f"- items: `{len(value['items'])}`")
              print(f"- pending: `{pending}`")
              print(f"- currently unexecutable/blocking: `{blocked}`")
              print()
              print('This heartbeat inspects persistent work. It does not relabel missing model or verifier bindings as progress.')
              PY
    ''',
)
