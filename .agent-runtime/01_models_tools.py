from __future__ import annotations

from pathlib import Path
from textwrap import dedent


def write(path: str, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dedent(content).lstrip(), encoding="utf-8")


write(
    "src/agi/tasking.py",
    r'''
    from __future__ import annotations

    import json
    import uuid
    from dataclasses import asdict, dataclass, field
    from datetime import datetime, timezone
    from typing import Any, Mapping


    class TaskValidationError(ValueError):
        """Raised when a general-agent task or plan violates its contract."""


    def utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()


    def json_copy(value: Any) -> Any:
        try:
            return json.loads(json.dumps(value, ensure_ascii=False))
        except (TypeError, ValueError) as exc:
            raise TaskValidationError("value must be JSON serializable") from exc


    def _required_text(value: Any, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise TaskValidationError(f"{field_name} must be a non-empty string")
        return value.strip()


    def _strings(value: Any, field_name: str, *, allow_empty: bool = True) -> tuple[str, ...]:
        if value is None and allow_empty:
            return ()
        if not isinstance(value, (list, tuple)) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise TaskValidationError(f"{field_name} must be a list of non-empty strings")
        result = tuple(item.strip() for item in value)
        if not result and not allow_empty:
            raise TaskValidationError(f"{field_name} cannot be empty")
        return result


    @dataclass(frozen=True, slots=True)
    class TaskRequest:
        task_id: str
        goal: str
        success_criteria: tuple[str, ...]
        constraints: tuple[str, ...] = ()
        tags: tuple[str, ...] = ()
        max_plan_revisions: int = 2
        metadata: dict[str, Any] = field(default_factory=dict)

        @classmethod
        def from_mapping(cls, value: Mapping[str, Any]) -> "TaskRequest":
            if not isinstance(value, Mapping):
                raise TaskValidationError("task must be an object")
            raw_id = value.get("task_id")
            task_id = (
                _required_text(raw_id, "task_id")
                if raw_id is not None
                else f"task-{uuid.uuid4().hex[:12]}"
            )
            revisions = value.get("max_plan_revisions", 2)
            if isinstance(revisions, bool) or not isinstance(revisions, int) or revisions < 0:
                raise TaskValidationError("max_plan_revisions must be a non-negative integer")
            metadata = value.get("metadata", {})
            if not isinstance(metadata, Mapping):
                raise TaskValidationError("metadata must be an object")
            return cls(
                task_id=task_id,
                goal=_required_text(value.get("goal"), "goal"),
                success_criteria=_strings(
                    value.get("success_criteria"), "success_criteria", allow_empty=False
                ),
                constraints=_strings(value.get("constraints", []), "constraints"),
                tags=_strings(value.get("tags", []), "tags"),
                max_plan_revisions=revisions,
                metadata=json_copy(dict(metadata)),
            )

        def as_dict(self) -> dict[str, Any]:
            value = asdict(self)
            value["success_criteria"] = list(self.success_criteria)
            value["constraints"] = list(self.constraints)
            value["tags"] = list(self.tags)
            return value


    @dataclass(frozen=True, slots=True)
    class PlanStep:
        step_id: str
        description: str
        kind: str = "reason"
        tool: str | None = None
        skill_id: str | None = None
        arguments: dict[str, Any] = field(default_factory=dict)
        success_hint: str = ""

        @classmethod
        def from_mapping(cls, value: Mapping[str, Any], index: int) -> "PlanStep":
            if not isinstance(value, Mapping):
                raise TaskValidationError(f"plan step {index} must be an object")
            kind = value.get("kind", "reason")
            if kind not in {"reason", "tool", "skill"}:
                raise TaskValidationError(
                    f"plan step {index} kind must be reason, tool, or skill"
                )
            tool = value.get("tool")
            skill_id = value.get("skill_id")
            if kind == "tool" and (not isinstance(tool, str) or not tool.strip()):
                raise TaskValidationError(f"plan step {index} requires tool")
            if kind == "skill" and (
                not isinstance(skill_id, str) or not skill_id.strip()
            ):
                raise TaskValidationError(f"plan step {index} requires skill_id")
            arguments = value.get("arguments", {})
            if not isinstance(arguments, Mapping):
                raise TaskValidationError(f"plan step {index} arguments must be an object")
            raw_id = value.get("step_id", f"step-{index + 1}")
            return cls(
                step_id=_required_text(raw_id, f"plan step {index} step_id"),
                description=_required_text(
                    value.get("description"), f"plan step {index} description"
                ),
                kind=kind,
                tool=tool.strip() if isinstance(tool, str) else None,
                skill_id=skill_id.strip() if isinstance(skill_id, str) else None,
                arguments=json_copy(dict(arguments)),
                success_hint=(
                    value.get("success_hint", "").strip()
                    if isinstance(value.get("success_hint", ""), str)
                    else ""
                ),
            )

        def as_dict(self) -> dict[str, Any]:
            return asdict(self)


    @dataclass(frozen=True, slots=True)
    class AgentPlan:
        steps: tuple[PlanStep, ...]
        rationale: str = ""

        @classmethod
        def from_mapping(cls, value: Mapping[str, Any]) -> "AgentPlan":
            if not isinstance(value, Mapping):
                raise TaskValidationError("plan response must be an object")
            raw_steps = value.get("steps")
            if not isinstance(raw_steps, list) or not raw_steps:
                raise TaskValidationError("plan must contain at least one step")
            steps = tuple(
                PlanStep.from_mapping(step, index) for index, step in enumerate(raw_steps)
            )
            identifiers = [step.step_id for step in steps]
            if len(identifiers) != len(set(identifiers)):
                raise TaskValidationError("plan step IDs must be unique")
            rationale = value.get("rationale", "")
            return cls(
                steps=steps,
                rationale=rationale.strip() if isinstance(rationale, str) else "",
            )

        def as_dict(self) -> dict[str, Any]:
            return {
                "steps": [step.as_dict() for step in self.steps],
                "rationale": self.rationale,
            }
    ''',
)

write(
    "src/agi/reasoner.py",
    r'''
    from __future__ import annotations

    import json
    import os
    import subprocess
    from pathlib import Path
    from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

    from .tasking import json_copy


    class ReasonerError(RuntimeError):
        """Raised when a reasoning backend fails or returns an invalid envelope."""


    class Reasoner(Protocol):
        def reason(self, component: str, context: Mapping[str, Any]) -> Mapping[str, Any]:
            ...


    class ScriptedReasoner:
        """Deterministic fresh-call reasoner used by tests and reproducible replays."""

        def __init__(self, scripts: Mapping[str, Iterable[Mapping[str, Any]]]):
            self.scripts = {
                component: [json_copy(dict(item)) for item in responses]
                for component, responses in scripts.items()
            }
            self.calls: list[dict[str, Any]] = []

        def reason(self, component: str, context: Mapping[str, Any]) -> Mapping[str, Any]:
            safe_context = json_copy(dict(context))
            self.calls.append({"component": component, "context": safe_context})
            queue = self.scripts.get(component)
            if not queue:
                queue = self.scripts.get("*")
            if not queue:
                raise ReasonerError(f"no scripted response remains for {component}")
            return json_copy(queue.pop(0))


    class FunctionReasoner:
        def __init__(
            self,
            handler: Callable[[str, Mapping[str, Any]], Mapping[str, Any]],
        ):
            self.handler = handler
            self.calls: list[dict[str, Any]] = []

        def reason(self, component: str, context: Mapping[str, Any]) -> Mapping[str, Any]:
            safe_context = json_copy(dict(context))
            self.calls.append({"component": component, "context": safe_context})
            result = self.handler(component, safe_context)
            if not isinstance(result, Mapping):
                raise ReasonerError(f"{component} handler must return an object")
            return json_copy(dict(result))


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


    class JsonCommandReasoner:
        """Run an explicitly supplied command as a stateless JSON-in/JSON-out reasoner.

        No shell is used and secret-bearing environment variables are removed.  Each
        call starts a fresh process, so a child skill cannot inherit hidden parent
        conversation state.
        """

        def __init__(
            self,
            root: Path,
            command: Sequence[str],
            *,
            timeout_seconds: int = 120,
        ):
            if not command or not all(isinstance(item, str) and item for item in command):
                raise ValueError("reasoner command must contain executable arguments")
            self.root = root.resolve()
            self.command = tuple(command)
            self.timeout_seconds = timeout_seconds

        def reason(self, component: str, context: Mapping[str, Any]) -> Mapping[str, Any]:
            request = {"component": component, "context": json_copy(dict(context))}
            try:
                completed = subprocess.run(
                    list(self.command),
                    cwd=self.root,
                    env=_safe_environment(),
                    input=json.dumps(request, ensure_ascii=False),
                    text=True,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ReasonerError(f"reasoner command failed: {exc}") from exc
            if completed.returncode != 0:
                message = completed.stderr.strip()[-2000:]
                raise ReasonerError(
                    f"reasoner command exited {completed.returncode}: {message}"
                )
            try:
                value = json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                raise ReasonerError("reasoner command did not return one JSON object") from exc
            if not isinstance(value, Mapping):
                raise ReasonerError("reasoner command result must be an object")
            return json_copy(dict(value))


    def scripted_reasoner_from_file(path: Path) -> ScriptedReasoner:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ReasonerError("script file root must be an object")
        scripts: dict[str, list[Mapping[str, Any]]] = {}
        for component, responses in value.items():
            if not isinstance(component, str) or not isinstance(responses, list):
                raise ReasonerError("script entries must map component names to lists")
            if not all(isinstance(item, Mapping) for item in responses):
                raise ReasonerError(f"script responses for {component} must be objects")
            scripts[component] = responses
        return ScriptedReasoner(scripts)
    ''',
)

write(
    "src/agi/tools.py",
    r'''
    from __future__ import annotations

    import hashlib
    import json
    import os
    import tempfile
    from dataclasses import dataclass
    from pathlib import Path
    from typing import Any, Callable, Mapping

    from .evidence import canonical_json
    from .tasking import json_copy


    class ToolRegistrationError(ValueError):
        pass


    @dataclass(frozen=True, slots=True)
    class ToolDefinition:
        name: str
        handler: Callable[[Path, Mapping[str, Any]], Any]
        mutating: bool = False
        description: str = ""


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


    def _resolve(root: Path, relative: Any, *, write: bool = False) -> Path:
        if not isinstance(relative, str) or not relative.strip():
            raise PermissionError("path must be a non-empty string")
        root = root.resolve()
        target = (root / relative).resolve()
        try:
            normalized = target.relative_to(root).as_posix()
        except ValueError as exc:
            raise PermissionError("path escapes repository root") from exc
        lowered = normalized.lower()
        if lowered.startswith(".git/") or lowered in {
            ".env",
            ".env.local",
            ".npmrc",
            ".pypirc",
            "credentials.json",
        }:
            raise PermissionError(f"protected path: {normalized}")
        if write and not (
            lowered.startswith("artifacts/")
            or lowered.startswith(".continual/agi/artifacts/")
        ):
            raise PermissionError(
                "model writes are restricted to artifacts/ or .continual/agi/artifacts/"
            )
        return target


    class ToolRegistry:
        """Allowlisted tools with durable idempotency results per logical call."""

        def __init__(self, root: Path):
            self.root = root.resolve()
            self._definitions: dict[str, ToolDefinition] = {}
            self.cache = self.root / ".continual" / "agi" / "tool-cache"

        def register(self, definition: ToolDefinition) -> None:
            if not definition.name or definition.name in self._definitions:
                raise ToolRegistrationError(
                    f"tool name must be unique and non-empty: {definition.name!r}"
                )
            self._definitions[definition.name] = definition

        def describe(self) -> list[dict[str, Any]]:
            return [
                {
                    "name": item.name,
                    "mutating": item.mutating,
                    "description": item.description,
                }
                for item in sorted(self._definitions.values(), key=lambda value: value.name)
            ]

        def execute(
            self,
            name: str,
            arguments: Mapping[str, Any],
            *,
            run_id: str,
            call_id: str,
        ) -> dict[str, Any]:
            if name not in self._definitions:
                return {"ok": False, "error": f"unknown tool: {name}", "cached": False}
            if not isinstance(arguments, Mapping):
                return {"ok": False, "error": "tool arguments must be an object", "cached": False}
            identity = {
                "name": name,
                "arguments": json_copy(dict(arguments)),
                "run_id": run_id,
                "call_id": call_id,
            }
            key = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
            cache_path = self.cache / f"{key}.json"
            if cache_path.exists():
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                cached["cached"] = True
                return cached
            definition = self._definitions[name]
            try:
                output = json_copy(definition.handler(self.root, dict(arguments)))
                result = {
                    "ok": True,
                    "tool": name,
                    "output": output,
                    "error": None,
                    "cached": False,
                }
            except Exception as exc:  # the error is data for the recovery loop
                result = {
                    "ok": False,
                    "tool": name,
                    "output": None,
                    "error": f"{type(exc).__name__}: {exc}",
                    "cached": False,
                }
            _atomic_json(cache_path, result)
            return result


    def default_tool_registry(root: Path) -> ToolRegistry:
        registry = ToolRegistry(root)

        def read_text(repo: Path, arguments: Mapping[str, Any]) -> dict[str, Any]:
            path = _resolve(repo, arguments.get("path"), write=False)
            maximum = arguments.get("max_chars", 20000)
            if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
                raise ValueError("max_chars must be a positive integer")
            text = path.read_text(encoding="utf-8")
            return {
                "path": path.relative_to(repo).as_posix(),
                "text": text[:maximum],
                "truncated": len(text) > maximum,
            }

        def write_artifact(repo: Path, arguments: Mapping[str, Any]) -> dict[str, Any]:
            path = _resolve(repo, arguments.get("path"), write=True)
            content = arguments.get("content")
            if not isinstance(content, str):
                raise ValueError("content must be a string")
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            except BaseException:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
                raise
            return {
                "path": path.relative_to(repo).as_posix(),
                "bytes": len(content.encode("utf-8")),
            }

        def list_files(repo: Path, arguments: Mapping[str, Any]) -> dict[str, Any]:
            relative = arguments.get("path", ".")
            directory = _resolve(repo, relative, write=False)
            if not directory.is_dir():
                raise NotADirectoryError(directory)
            maximum = arguments.get("max_entries", 200)
            if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
                raise ValueError("max_entries must be a positive integer")
            entries = []
            for item in sorted(directory.iterdir(), key=lambda value: value.name)[:maximum]:
                if item.name == ".git":
                    continue
                entries.append(
                    {
                        "name": item.name,
                        "path": item.relative_to(repo).as_posix(),
                        "type": "directory" if item.is_dir() else "file",
                    }
                )
            return {"path": directory.relative_to(repo).as_posix(), "entries": entries}

        def json_query(repo: Path, arguments: Mapping[str, Any]) -> dict[str, Any]:
            path = _resolve(repo, arguments.get("path"), write=False)
            value: Any = json.loads(path.read_text(encoding="utf-8"))
            keys = arguments.get("keys", [])
            if not isinstance(keys, list):
                raise ValueError("keys must be a list")
            for key in keys:
                if isinstance(value, list) and isinstance(key, int):
                    value = value[key]
                elif isinstance(value, dict) and isinstance(key, str):
                    value = value[key]
                else:
                    raise KeyError(key)
            return {"path": path.relative_to(repo).as_posix(), "value": value}

        registry.register(
            ToolDefinition("read_text", read_text, description="Read bounded repository text")
        )
        registry.register(
            ToolDefinition(
                "write_artifact",
                write_artifact,
                mutating=True,
                description="Atomically write a task artifact inside an allowed directory",
            )
        )
        registry.register(
            ToolDefinition("list_files", list_files, description="List one repository directory")
        )
        registry.register(
            ToolDefinition("json_query", json_query, description="Read a path within a JSON file")
        )
        return registry
    ''',
)
