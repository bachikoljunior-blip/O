from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

try:
    from openai import OpenAI
except ImportError:  # Optional at import time; required only for live model calls.
    OpenAI = None  # type: ignore[assignment]


TOOLS = [
    {
        "type": "function",
        "name": "read_file",
        "description": "Read a non-secret UTF-8 file inside the repository.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "write_file",
        "description": "Atomically create or replace a UTF-8 file inside the repository. Protected active-control files cannot be overwritten directly; propose a Candidate instead.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "list_files",
        "description": "List repository-relative non-secret paths under a directory.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "run_command",
        "description": "Run a command without a shell and with secrets removed from the environment. Network-affecting commands are blocked. Use for tests, builds, lint, and local git inspection.",
        "parameters": {
            "type": "object",
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"}},
                "cwd": {"type": "string"},
                "timeout_seconds": {"type": "integer"},
            },
            "required": ["argv", "cwd", "timeout_seconds"],
            "additionalProperties": False,
        },
    },
]


_SECRET_NAME = re.compile(r"(?:^|[_-])(token|secret|password|passwd|api[_-]?key|private[_-]?key)(?:$|[_-])", re.I)
_SENSITIVE_PARTS = {
    ".env",
    ".env.local",
    ".env.production",
    "credentials",
    "credentials.json",
    "secrets",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
}
_PROTECTED_WRITE_PREFIXES = (
    "prompts/",
    ".continual/system/",
    ".github/workflows/",
)
_PROTECTED_WRITE_FILES = {"AGENTS.md", "SYSTEM_DESIGN.md"}
_NETWORK_PROGRAMS = {
    "curl",
    "wget",
    "ssh",
    "scp",
    "sftp",
    "ftp",
    "nc",
    "ncat",
    "telnet",
    "gh",
}
_NETWORK_GIT_SUBCOMMANDS = {"push", "fetch", "pull", "clone", "remote", "ls-remote", "submodule"}


class ModelClient:
    def __init__(self, root: Path):
        self.root = root.resolve()
        if OpenAI is None:
            raise RuntimeError("openai package is required for live model execution")
        openai_key = os.environ.get("OPENAI_API_KEY")
        if not openai_key:
            raise RuntimeError(
                "OPENAI_API_KEY is required for the continual Engine; GitHub Actions without it must use the constrained Copilot CLI continuity backend"
            )
        self.provider = "openai"
        self.client = OpenAI(api_key=openai_key)
        self.model = os.environ.get("OPENAI_MODEL", "gpt-5.6")

    def _safe_path(self, relative: str) -> Path:
        if not isinstance(relative, str) or not relative.strip():
            raise ValueError("path must be a non-empty repository-relative string")
        raw = Path(relative)
        if raw.is_absolute():
            raise ValueError("absolute paths are not allowed")
        path = (self.root / raw).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("path escapes repository")
        if path == self.root / ".git" or self.root / ".git" in path.parents:
            raise PermissionError("direct .git access is blocked")
        return path

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def _is_sensitive(self, path: Path) -> bool:
        parts = {part.lower() for part in path.relative_to(self.root).parts}
        if parts & _SENSITIVE_PARTS:
            return True
        name = path.name.lower()
        return name.endswith((".pem", ".key", ".p12", ".pfx")) or bool(_SECRET_NAME.search(name))

    def _ensure_readable(self, path: Path) -> None:
        if self._is_sensitive(path):
            raise PermissionError("reading secret-like files is blocked")

    def _ensure_writable(self, path: Path) -> None:
        rel = self._relative(path)
        if self._is_sensitive(path):
            raise PermissionError("writing secret-like files is blocked")
        if rel in _PROTECTED_WRITE_FILES or rel.startswith(_PROTECTED_WRITE_PREFIXES):
            raise PermissionError(
                "active control files are protected; return a Candidate proposal instead of overwriting them"
            )

    def prompt(self, component: str, prompt_path: str | None = None) -> str:
        path = self._safe_path(prompt_path) if prompt_path else self.root / "prompts" / f"{component}.md"
        self._ensure_readable(path)
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _sanitized_env() -> dict[str, str]:
        clean: dict[str, str] = {}
        for key, value in os.environ.items():
            if _SECRET_NAME.search(key):
                continue
            if key.upper() in {"OPENAI_API_KEY", "GITHUB_TOKEN", "GH_TOKEN"}:
                continue
            clean[key] = value
        return clean

    @staticmethod
    def _blocked_command(argv: list[str]) -> bool:
        program = Path(argv[0]).name.lower()
        if program in _NETWORK_PROGRAMS:
            return True
        if program == "git" and len(argv) >= 2 and argv[1].lower() in _NETWORK_GIT_SUBCOMMANDS:
            return True
        if program in {"python", "python3", "pip", "pip3"}:
            lowered = {arg.lower() for arg in argv[1:]}
            if lowered & {"upload", "publish"}:
                return True
        return False

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _tool(self, name: str, args: dict[str, Any]) -> str:
        if name == "read_file":
            path = self._safe_path(args["path"])
            self._ensure_readable(path)
            return path.read_text(encoding="utf-8")

        if name == "write_file":
            path = self._safe_path(args["path"])
            self._ensure_writable(path)
            self._atomic_write(path, args["content"])
            return json.dumps({"ok": True, "path": self._relative(path)}, ensure_ascii=False)

        if name == "list_files":
            base = self._safe_path(args["path"])
            if not base.exists():
                return "[]"
            paths: list[str] = []
            for path in sorted(base.rglob("*")):
                if not path.is_file() or self._is_sensitive(path):
                    continue
                if path == self.root / ".git" or self.root / ".git" in path.parents:
                    continue
                paths.append(self._relative(path))
                if len(paths) >= 2000:
                    break
            return json.dumps(paths, ensure_ascii=False)

        if name == "run_command":
            argv = args["argv"]
            if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
                raise ValueError("argv must be a non-empty string array")
            if self._blocked_command(argv):
                raise PermissionError("network-affecting or publishing command is blocked")
            cwd = self._safe_path(args["cwd"])
            if not cwd.is_dir():
                raise NotADirectoryError(self._relative(cwd))
            timeout = max(1, min(int(args["timeout_seconds"]), 300))
            completed = subprocess.run(
                argv,
                cwd=cwd,
                text=True,
                capture_output=True,
                timeout=timeout,
                env=self._sanitized_env(),
                shell=False,
            )
            return json.dumps(
                {
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[-20000:],
                    "stderr": completed.stderr[-20000:],
                },
                ensure_ascii=False,
            )

        raise ValueError(f"unknown tool: {name}")

    @staticmethod
    def _json_text(text: str) -> dict[str, Any]:
        stripped = text.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            stripped = "\n".join(lines)
            if stripped.lstrip().startswith("json"):
                stripped = stripped.lstrip()[4:].lstrip()
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"model returned non-JSON output: {stripped[:500]}") from exc
        if not isinstance(value, dict):
            raise RuntimeError("model JSON output must be an object")
        return value

    def call(self, component: str, payload: dict[str, Any], prompt_path: str | None = None) -> dict[str, Any]:
        instructions = self.prompt(component, prompt_path)
        response = self.client.responses.create(
            model=self.model,
            instructions=instructions,
            input=json.dumps(payload, ensure_ascii=False),
            tools=TOOLS,
        )
        for _ in range(32):
            calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
            if not calls:
                return self._json_text(response.output_text)
            outputs = []
            for call in calls:
                try:
                    args = json.loads(call.arguments)
                    result = self._tool(call.name, args)
                except Exception as exc:
                    result = json.dumps(
                        {"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False
                    )
                outputs.append(
                    {"type": "function_call_output", "call_id": call.call_id, "output": result}
                )
            response = self.client.responses.create(
                model=self.model,
                instructions=instructions,
                previous_response_id=response.id,
                input=outputs,
                tools=TOOLS,
            )
        raise RuntimeError(f"tool loop exceeded for {component}")
