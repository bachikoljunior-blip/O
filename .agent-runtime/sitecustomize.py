from __future__ import annotations

import atexit
from pathlib import Path


def patch_generated_tests() -> None:
    path = Path("tests/test_general_agent.py")
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    needle = '''        reasoner = successful_reasoner(skill=True)\n        agent = GeneralAgent(tmp_path, reasoner)\n'''
    replacement = '''        reasoner = successful_reasoner(skill=True)\n        skill_dir = tmp_path / ".continual" / "skills"\n        skill_dir.mkdir(parents=True)\n        source_dir = Path(".continual/skills")\n        for name in (\n            "root-task.json",\n            "verify-before-claim.json",\n            "learn-after-task.json",\n        ):\n            (skill_dir / name).write_text(\n                (source_dir / name).read_text(encoding="utf-8"),\n                encoding="utf-8",\n            )\n        agent = GeneralAgent(tmp_path, reasoner)\n'''
    if needle in text and replacement not in text:
        path.write_text(text.replace(needle, replacement, 1), encoding="utf-8")


atexit.register(patch_generated_tests)
