from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from openai import OpenAI


TOOLS = [
    {
        "type": "function",
        "name": "read_file",
        "description": "Read a UTF-8 file inside the repository.",
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
        "description": "Create or replace a UTF-8 file inside the repository.",
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
        "description": "List repository-relative paths under a directory.",
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
        "description": "Run a command without a shell, with cwd fixed inside the repository. Use for tests, builds, lint, and git inspection. Network-affecting git commands are blocked.",
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


class ModelClient:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.client = OpenAI()
        self.model = os.environ.get("OPENAI_MODEL", "gpt-5.6")

    def _safe_path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("path escapes repository")
        return path

    def prompt(self, component: str, prompt_path: str | None = None) -> str:
        path = self._safe_path(prompt_path) if prompt_path else self.root / "prompts" / f"{component}.md"
        return path.read_text(encoding="utf-8")

    def _tool(self, name: str, args: dict[str, Any]) -> str:
        if name == "read_file":
            return self._safe_path(args["path"]).read_text(encoding="utf-8")
        if name == "write_file":
            path = self._safe_path(args["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args["content"], encoding="utf-8")
            return json.dumps({"ok": True, "path": args["path"]})
        if name == "list_files":
            base = self._safe_path(args["path"])
            if not base.exists():
                return "[]"
            paths = [str(p.relative_to(self.root)) for p in sorted(base.rglob("*")) if p.is_file()]
            return json.dumps(paths[:2000], ensure_ascii=False)
        if name == "run_command":
            argv = args["argv"]
            if not argv:
                raise ValueError("empty argv")
            banned = {("git", "push"), ("git", "fetch"), ("git", "pull"), ("git", "remote")}
            if len(argv) >= 2 and (argv[0], argv[1]) in banned:
                raise PermissionError("network-affecting git command is blocked; external side effects require a dedicated persisted adapter")
            cwd = self._safe_path(args["cwd"])
            cp = subprocess.run(
                argv,
                cwd=cwd,
                text=True,
                capture_output=True,
                timeout=max(1, min(int(args["timeout_seconds"]), 300)),
            )
            return json.dumps({
                "returncode": cp.returncode,
                "stdout": cp.stdout[-20000:],
                "stderr": cp.stderr[-20000:],
            }, ensure_ascii=False)
        raise ValueError(f"unknown tool: {name}")

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
                break
            outputs = []
            for call in calls:
                try:
                    args = json.loads(call.arguments)
                    result = self._tool(call.name, args)
                except Exception as e:
                    result = json.dumps({"error": type(e).__name__, "message": str(e)}, ensure_ascii=False)
                outputs.append({"type": "function_call_output", "call_id": call.call_id, "output": result})
            response = self.client.responses.create(
                model=self.model,
                instructions=instructions,
                previous_response_id=response.id,
                input=outputs,
                tools=TOOLS,
            )
        else:
            raise RuntimeError(f"tool loop exceeded for {component}")

        text = response.output_text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
            if text.lstrip().startswith("json"):
                text = text.lstrip()[4:].lstrip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"{component} returned non-JSON output: {text[:500]}") from e
