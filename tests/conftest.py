from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture
def runtime_repo(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[1]
    shutil.copytree(source / "prompts", tmp_path / "prompts")
    shutil.copytree(source / ".continual" / "system", tmp_path / ".continual" / "system")
    (tmp_path / ".continual" / "candidates").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".continual" / "candidates" / "index.json").write_text(
        '{"schema_version": 2, "candidates": []}\n', encoding="utf-8"
    )
    return tmp_path
