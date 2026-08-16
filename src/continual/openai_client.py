from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from openai import OpenAI


class ModelClient:
    def __init__(self, root: Path):
        self.root = root
        self.client = OpenAI()
        self.model = os.environ.get("OPENAI_MODEL", "gpt-5.6")

    def prompt(self, component: str) -> str:
        path = self.root / "prompts" / f"{component}.md"
        return path.read_text(encoding="utf-8")

    def call(self, component: str, payload: dict[str, Any]) -> dict[str, Any]:
        instructions = self.prompt(component)
        response = self.client.responses.create(
            model=self.model,
            input=[
                {"role": "developer", "content": instructions},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
        text = response.output_text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].lstrip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"{component} returned non-JSON output: {text[:500]}") from e
