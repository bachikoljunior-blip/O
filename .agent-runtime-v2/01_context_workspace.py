from __future__ import annotations

from pathlib import Path
from textwrap import dedent


def write(path: str, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dedent(content).lstrip(), encoding="utf-8")


write(
    "src/agi/context.py",
    r'''
    from __future__ import annotations

    import json
    from typing import Any, Mapping

    from .tasking import json_copy


    COMPONENT_BUDGETS = {
        "orient": 16000,
        "plan": 12000,
        "act": 10000,
        "skill": 6000,
        "verify": 18000,
        "reflect": 18000,
    }


    def _size(value: Any) -> int:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True))


    def _shrink(value: Any, target: int) -> Any:
        if target < 32:
            return None
        if isinstance(value, str):
            if len(value) <= target:
                return value
            return value[: max(0, target - 18)] + "…[truncated]"
        if isinstance(value, list):
            if not value:
                return []
            # Context lists are ranked or chronological. Keep the first half and the
            # most recent tail so both relevance and recovery history survive.
            if len(value) == 1:
                return [_shrink(value[0], target - 16)]
            keep = max(1, len(value) // 2)
            head = value[: max(1, keep // 2)]
            tail = value[-max(1, keep - len(head)) :]
            result = head + ["…items omitted…"] + tail
            per_item = max(32, target // max(1, len(result)))
            return [_shrink(item, per_item) for item in result]
        if isinstance(value, dict):
            if not value:
                return {}
            per_item = max(32, target // max(1, len(value)))
            return {str(key): _shrink(item, per_item) for key, item in value.items()}
        return value


    def bound_context(
        context: Mapping[str, Any],
        *,
        character_budget: int,
    ) -> dict[str, Any]:
        if character_budget < 256:
            raise ValueError("context character budget must be at least 256")
        safe = json_copy(dict(context))
        original_size = _size(safe)
        if original_size <= character_budget:
            return safe
        candidate: Any = safe
        for _ in range(8):
            candidate = _shrink(candidate, int(character_budget * 0.88))
            if isinstance(candidate, dict) and _size(candidate) <= character_budget:
                candidate["_context_truncation"] = {
                    "original_characters": original_size,
                    "bounded_characters": _size(candidate),
                }
                if _size(candidate) <= character_budget:
                    return json_copy(candidate)
            character_budget = max(256, int(character_budget * 0.9))
        return {
            "_context_truncation": {
                "original_characters": original_size,
                "bounded_characters": 0,
                "severe": True,
            }
        }
    ''',
)

write(
    "src/agi/workspace.py",
    r'''
    from __future__ import annotations

    import difflib
    import hashlib
    import json
    import os
    import re
    import shutil
    import subprocess
    import sys
    import tempfile
    import uuid
    from dataclasses import dataclass
    from pathlib import Path
    from typing import Any, Mapping, Sequence

    from .evidence import canonical_json, sha256_file, utc_now


    EXCLUDED_NAMES = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
    PROTECTED_NAMES = {
        ".env",
        ".env.local",
        ".npmrc",
        ".pypirc",
        "credentials.json",
    }
    WORKSPACE_ID = re.compile(r"^workspace-[0-9a-f]{16}$")


    class WorkspaceError(RuntimeError):
        pass


    @dataclass(frozen=True, slots=True)
    class VerifierProfile:
        name: str
        command: tuple[str, ...]
        timeout_seconds: int = 120
        max_output_chars: int = 30000


    DEFAULT_PROFILES = {
        "python-tests": VerifierProfile(
            "python-tests", ("{python}", "-m", "pytest", "-q"), 180, 50000
        ),
        "python-compile": VerifierProfile(
            "python-compile",
            ("{python}", "-m", "compileall", "-q", "src", "tests"),
            120,
            30000,
        ),
    }


    def _ignore(directory: str, names: list[str]) -> set[str]:
        ignored = {name for name in names if name in EXCLUDED_NAMES}
        path = Path(directory)
        if path.name == "agi" and path.parent.name == ".continual":
            ignored.add("workspaces")
        return ignored


    def _safe_environment(work: Path) -> dict[str, str]:
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
        }
        environment = {key: value for key, value in os.environ.items() if key in allowed}
        python_paths = [str(work), str(work / "src")]
        environment["PYTHONPATH"] = os.pathsep.join(python_paths)
        environment["PIP_NO_INDEX"] = "1"
        environment["NO_PROXY"] = "*"
        environment["no_proxy"] = "*"
        return environment


    def _resource_limits() -> None:
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
            resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
            resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        except (ImportError, OSError, ValueError):
            pass


    class WorkspaceManager:
        """Transactional repository copies that produce reviewable patch artifacts.

        This is an isolation boundary for accidental repository mutation, not a claim
        of a hostile-code security sandbox. Commands are fixed verifier profiles,
        launched without a shell, with a scrubbed environment and resource limits.
        """

        def __init__(self, root: Path):
            self.root = root.resolve()
            self.directory = self.root / ".continual" / "agi" / "workspaces"
            self.artifacts = self.root / ".continual" / "agi" / "artifacts" / "workspaces"

        def _directory(self, workspace_id: str) -> Path:
            if not WORKSPACE_ID.fullmatch(workspace_id):
                raise WorkspaceError("invalid workspace identifier")
            target = (self.directory / workspace_id).resolve()
            try:
                target.relative_to(self.directory.resolve())
            except ValueError as exc:
                raise WorkspaceError("workspace escapes workspace directory") from exc
            return target

        def _work(self, workspace_id: str) -> Path:
            path = self._directory(workspace_id) / "work"
            if not path.is_dir():
                raise WorkspaceError(f"unknown workspace: {workspace_id}")
            return path

        def create(self) -> dict[str, Any]:
            workspace_id = f"workspace-{uuid.uuid4().hex[:16]}"
            directory = self._directory(workspace_id)
            baseline = directory / "baseline"
            work = directory / "work"
            directory.mkdir(parents=True, exist_ok=False)
            shutil.copytree(self.root, baseline, ignore=_ignore)
            shutil.copytree(baseline, work)
            metadata = {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "created_at": utc_now(),
                "root": str(self.root),
            }
            (directory / "metadata.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return metadata

        def _resolve(self, workspace_id: str, relative: Any, *, write: bool) -> Path:
            if not isinstance(relative, str) or not relative.strip():
                raise WorkspaceError("workspace path must be a non-empty string")
            work = self._work(workspace_id)
            target = (work / relative).resolve()
            try:
                normalized = target.relative_to(work).as_posix()
            except ValueError as exc:
                raise WorkspaceError("workspace path escapes work directory") from exc
            if Path(normalized).name.lower() in PROTECTED_NAMES or normalized.startswith(".git/"):
                raise WorkspaceError(f"protected workspace path: {normalized}")
            if write and normalized in {".", ""}:
                raise WorkspaceError("workspace root cannot be overwritten")
            return target

        def write_text(self, workspace_id: str, relative: str, content: str) -> dict[str, Any]:
            if not isinstance(content, str):
                raise WorkspaceError("workspace content must be a string")
            path = self._resolve(workspace_id, relative, write=True)
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
                "workspace_id": workspace_id,
                "path": path.relative_to(self._work(workspace_id)).as_posix(),
                "sha256": sha256_file(path),
                "bytes": len(content.encode("utf-8")),
            }

        def delete(self, workspace_id: str, relative: str) -> dict[str, Any]:
            path = self._resolve(workspace_id, relative, write=True)
            existed = path.exists()
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
            return {"workspace_id": workspace_id, "path": relative, "existed": existed}

        def _profiles(self) -> dict[str, VerifierProfile]:
            profiles = dict(DEFAULT_PROFILES)
            path = self.root / ".continual" / "agi" / "verifier-profiles.json"
            if not path.exists():
                return profiles
            value = json.loads(path.read_text(encoding="utf-8"))
            raw = value.get("profiles", {}) if isinstance(value, Mapping) else {}
            if not isinstance(raw, Mapping):
                raise WorkspaceError("verifier profiles must be an object")
            for name, entry in raw.items():
                if not isinstance(name, str) or not isinstance(entry, Mapping):
                    continue
                command = entry.get("command")
                timeout = entry.get("timeout_seconds", 120)
                maximum = entry.get("max_output_chars", 30000)
                if (
                    isinstance(command, list)
                    and command
                    and all(isinstance(item, str) and item for item in command)
                    and isinstance(timeout, int)
                    and not isinstance(timeout, bool)
                    and 1 <= timeout <= 600
                    and isinstance(maximum, int)
                    and not isinstance(maximum, bool)
                    and 1000 <= maximum <= 200000
                ):
                    profiles[name] = VerifierProfile(
                        name, tuple(command), timeout, maximum
                    )
            return profiles

        def run_profile(self, workspace_id: str, profile_name: str) -> dict[str, Any]:
            profiles = self._profiles()
            profile = profiles.get(profile_name)
            if profile is None:
                raise WorkspaceError(f"unknown verifier profile: {profile_name}")
            work = self._work(workspace_id)
            command = [
                item.replace("{python}", sys.executable) for item in profile.command
            ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=work,
                    env=_safe_environment(work),
                    text=True,
                    capture_output=True,
                    timeout=profile.timeout_seconds,
                    check=False,
                    preexec_fn=_resource_limits if os.name == "posix" else None,
                )
                stdout = completed.stdout[-profile.max_output_chars :]
                stderr = completed.stderr[-profile.max_output_chars :]
                return {
                    "workspace_id": workspace_id,
                    "profile": profile_name,
                    "command": command,
                    "returncode": completed.returncode,
                    "passed": completed.returncode == 0,
                    "stdout": stdout,
                    "stderr": stderr,
                    "timed_out": False,
                }
            except subprocess.TimeoutExpired as exc:
                return {
                    "workspace_id": workspace_id,
                    "profile": profile_name,
                    "command": command,
                    "returncode": None,
                    "passed": False,
                    "stdout": (exc.stdout or "")[-profile.max_output_chars :]
                    if isinstance(exc.stdout, str)
                    else "",
                    "stderr": (exc.stderr or "")[-profile.max_output_chars :]
                    if isinstance(exc.stderr, str)
                    else "",
                    "timed_out": True,
                }

        @staticmethod
        def _files(root: Path) -> dict[str, Path]:
            result: dict[str, Path] = {}
            for path in root.rglob("*"):
                if path.is_file() and not any(part in EXCLUDED_NAMES for part in path.parts):
                    result[path.relative_to(root).as_posix()] = path
            return result

        def diff(self, workspace_id: str, *, max_patch_chars: int = 200000) -> dict[str, Any]:
            directory = self._directory(workspace_id)
            baseline = directory / "baseline"
            work = directory / "work"
            before = self._files(baseline)
            after = self._files(work)
            changed: list[dict[str, Any]] = []
            patch_parts: list[str] = []
            for relative in sorted(set(before) | set(after)):
                old = before.get(relative)
                new = after.get(relative)
                old_hash = sha256_file(old) if old else None
                new_hash = sha256_file(new) if new else None
                if old_hash == new_hash:
                    continue
                status = "modified" if old and new else "added" if new else "deleted"
                changed.append(
                    {
                        "path": relative,
                        "status": status,
                        "before_sha256": old_hash,
                        "after_sha256": new_hash,
                    }
                )
                try:
                    old_lines = old.read_text(encoding="utf-8").splitlines(True) if old else []
                    new_lines = new.read_text(encoding="utf-8").splitlines(True) if new else []
                except UnicodeDecodeError:
                    patch_parts.append(f"Binary file {relative} {status}\n")
                    continue
                patch_parts.extend(
                    difflib.unified_diff(
                        old_lines,
                        new_lines,
                        fromfile=f"a/{relative}",
                        tofile=f"b/{relative}",
                    )
                )
            patch = "".join(patch_parts)
            truncated = len(patch) > max_patch_chars
            visible_patch = patch[:max_patch_chars]
            self.artifacts.mkdir(parents=True, exist_ok=True)
            patch_path = self.artifacts / f"{workspace_id}.patch"
            manifest_path = self.artifacts / f"{workspace_id}.json"
            patch_path.write_text(patch, encoding="utf-8")
            manifest = {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "created_at": utc_now(),
                "changed_files": changed,
                "patch": patch_path.relative_to(self.root).as_posix(),
                "patch_sha256": sha256_file(patch_path),
            }
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return {
                **manifest,
                "manifest": manifest_path.relative_to(self.root).as_posix(),
                "preview": visible_patch,
                "preview_truncated": truncated,
            }


    def register_workspace_tools(registry: Any) -> None:
        from .tools import ToolDefinition

        manager = WorkspaceManager(registry.root)

        registry.register(
            ToolDefinition(
                "workspace_create",
                lambda root, arguments: manager.create(),
                mutating=True,
                description="Create an isolated transactional repository workspace",
            )
        )
        registry.register(
            ToolDefinition(
                "workspace_write",
                lambda root, arguments: manager.write_text(
                    str(arguments.get("workspace_id")),
                    str(arguments.get("path")),
                    arguments.get("content"),
                ),
                mutating=True,
                description="Write text inside a transactional workspace",
            )
        )
        registry.register(
            ToolDefinition(
                "workspace_delete",
                lambda root, arguments: manager.delete(
                    str(arguments.get("workspace_id")),
                    str(arguments.get("path")),
                ),
                mutating=True,
                description="Delete a path inside a transactional workspace",
            )
        )
        registry.register(
            ToolDefinition(
                "workspace_run",
                lambda root, arguments: manager.run_profile(
                    str(arguments.get("workspace_id")),
                    str(arguments.get("profile")),
                ),
                mutating=False,
                description="Run an allowlisted verifier profile inside a workspace",
            )
        )
        registry.register(
            ToolDefinition(
                "workspace_diff",
                lambda root, arguments: manager.diff(
                    str(arguments.get("workspace_id"))
                ),
                mutating=False,
                description="Create a hash-checked patch artifact from a workspace",
            )
        )
    ''',
)

write(
    "src/agi/perturbations.py",
    r'''
    from __future__ import annotations

    import json
    import os
    import tempfile
    from dataclasses import dataclass
    from pathlib import Path
    from typing import Any, Mapping

    from .tasking import json_copy, utc_now


    @dataclass(frozen=True, slots=True)
    class FaultRule:
        tool: str
        fail_ordinals: tuple[int, ...]
        error: str = "injected recoverable tool failure"

        def __post_init__(self) -> None:
            if not self.tool.strip():
                raise ValueError("fault rule tool cannot be empty")
            if not self.fail_ordinals or any(value < 1 for value in self.fail_ordinals):
                raise ValueError("fault ordinals must be positive")


    class PerturbingToolRegistry:
        """Deterministic, replayable tool-failure injection for robustness tests."""

        def __init__(self, base: Any, rules: list[FaultRule]):
            self.base = base
            self.root = base.root
            self.rules = {rule.tool: rule for rule in rules}
            self.directory = self.root / ".continual" / "agi" / "perturbations"

        def describe(self) -> list[dict[str, Any]]:
            result = self.base.describe()
            for item in result:
                rule = self.rules.get(item["name"])
                if rule:
                    item["perturbation"] = {
                        "fail_ordinals": list(rule.fail_ordinals),
                        "replayable": True,
                    }
            return result

        def _path(self, run_id: str, tool: str) -> Path:
            safe_run = "".join(char for char in run_id if char.isalnum() or char in "-_")
            safe_tool = "".join(char for char in tool if char.isalnum() or char in "-_")
            return self.directory / safe_run / f"{safe_tool}.json"

        @staticmethod
        def _atomic(path: Path, value: Mapping[str, Any]) -> None:
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

        def execute(
            self,
            name: str,
            arguments: Mapping[str, Any],
            *,
            run_id: str,
            call_id: str,
        ) -> dict[str, Any]:
            rule = self.rules.get(name)
            if rule is None:
                return self.base.execute(
                    name, arguments, run_id=run_id, call_id=call_id
                )
            path = self._path(run_id, name)
            state = (
                json.loads(path.read_text(encoding="utf-8"))
                if path.exists()
                else {"calls": []}
            )
            calls = state.get("calls", [])
            existing = next(
                (item for item in calls if item.get("call_id") == call_id), None
            )
            if existing is not None:
                return json_copy(existing["result"])
            ordinal = len(calls) + 1
            if ordinal in rule.fail_ordinals:
                result = {
                    "ok": False,
                    "tool": name,
                    "output": None,
                    "error": rule.error,
                    "cached": False,
                    "perturbation": {
                        "kind": "injected-tool-failure",
                        "ordinal": ordinal,
                        "replayable": True,
                    },
                }
            else:
                result = self.base.execute(
                    name, arguments, run_id=run_id, call_id=call_id
                )
            calls.append(
                {
                    "call_id": call_id,
                    "ordinal": ordinal,
                    "result": json_copy(result),
                    "at": utc_now(),
                }
            )
            state["calls"] = calls
            self._atomic(path, state)
            return json_copy(result)
    ''',
)

# Add workspace tools to the default allowlist.
tools_path = Path("src/agi/tools.py")
tools = tools_path.read_text(encoding="utf-8")
needle = "        return registry\n"
replacement = (
    "        from .workspace import register_workspace_tools\n\n"
    "        register_workspace_tools(registry)\n"
    "        return registry\n"
)
if "register_workspace_tools(registry)" not in tools:
    if needle not in tools:
        raise RuntimeError("could not locate default tool registry return")
    tools = tools.replace(needle, replacement, 1)
tools_path.write_text(tools, encoding="utf-8")

# Bound every explicit context before it is persisted or sent to a reasoner.
agent_path = Path("src/agi/agent.py")
agent = agent_path.read_text(encoding="utf-8")
if "from .context import COMPONENT_BUDGETS, bound_context" not in agent:
    anchor = "from .evidence import canonical_json\n"
    if anchor not in agent:
        raise RuntimeError("could not locate agent import anchor")
    agent = agent.replace(
        anchor,
        anchor + "from .context import COMPONENT_BUDGETS, bound_context\n",
        1,
    )
old = '''            self.store.write_context(snapshot["run_id"], component, context)\n            value = self.reasoner.reason(component, json_copy(dict(context)))\n'''
new = '''            bounded = bound_context(\n                context,\n                character_budget=COMPONENT_BUDGETS.get(component, 10000),\n            )\n            self.store.write_context(snapshot["run_id"], component, bounded)\n            value = self.reasoner.reason(component, bounded)\n'''
if old in agent:
    agent = agent.replace(old, new, 1)
elif "bounded = bound_context(" not in agent:
    raise RuntimeError("could not patch bounded reasoner context")
agent_path.write_text(agent, encoding="utf-8")
