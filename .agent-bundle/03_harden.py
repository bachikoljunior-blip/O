from __future__ import annotations

import re
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
        target.write_text(existing.rstrip() + "\n\n" + dedent(content).strip() + "\n", encoding="utf-8")


write(
    "src/continual/contracts.py",
    r'''
    from __future__ import annotations

    from typing import Any, Mapping


    class ComponentOutputError(ValueError):
        """Raised when a model component violates the runtime output contract."""


    def validate_component_output(component: str, value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ComponentOutputError(f"{component}: output must be a JSON object")
        output = dict(value)
        result = output.get("result")
        fragment = output.get("fragment")
        if not isinstance(result, Mapping):
            raise ComponentOutputError(f"{component}: result must be an object")
        if not isinstance(fragment, Mapping):
            raise ComponentOutputError(f"{component}: fragment must be an object")
        if "local_learn" in output and output["local_learn"] is not None and not isinstance(
            output["local_learn"], Mapping
        ):
            raise ComponentOutputError(
                f"{component}: local_learn must be an object or null"
            )
        output["result"] = dict(result)
        output["fragment"] = dict(fragment)
        if isinstance(output.get("local_learn"), Mapping):
            output["local_learn"] = dict(output["local_learn"])
        return output


    def candidate_index_for_target(index: Any, target_component: str) -> dict[str, Any]:
        """Return only candidates that could affect the component being invoked."""

        if not isinstance(index, Mapping):
            return {"schema_version": 1, "candidates": []}
        raw_candidates = index.get("candidates", [])
        if not isinstance(raw_candidates, list):
            return {**dict(index), "candidates": []}
        candidates = []
        for candidate in raw_candidates:
            if not isinstance(candidate, Mapping):
                continue
            target = candidate.get("target_component")
            if target in {None, "*", target_component}:
                candidates.append(dict(candidate))
        return {**dict(index), "candidates": candidates}
    ''',
)

write(
    "src/continual/security.py",
    r'''
    from __future__ import annotations

    import os
    from pathlib import Path
    from typing import Mapping


    PROTECTED_WRITE_PREFIXES = (
        ".git/",
        ".github/workflows/",
        ".continual/system/",
        "prompts/",
    )
    PROTECTED_EXACT_PATHS = {
        ".env",
        ".env.local",
        ".npmrc",
        ".pypirc",
        "credentials.json",
    }
    SECRET_MARKERS = (
        "TOKEN",
        "SECRET",
        "PASSWORD",
        "PASSWD",
        "API_KEY",
        "PRIVATE_KEY",
        "CREDENTIAL",
        "AUTH",
        "COOKIE",
    )
    SAFE_ENVIRONMENT_KEYS = {
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
        "PYTHONPATH",
        "VIRTUAL_ENV",
    }


    class PathBoundaryError(PermissionError):
        """Raised when a tool request crosses the repository capability boundary."""


    def resolve_repo_path(root: Path, relative: str, *, write: bool = False) -> Path:
        if not isinstance(relative, str) or not relative.strip():
            raise PathBoundaryError("path must be a non-empty relative string")
        root = root.resolve()
        target = (root / relative).resolve()
        try:
            normalized = target.relative_to(root).as_posix()
        except ValueError as exc:
            raise PathBoundaryError("path escapes repository root") from exc
        if normalized in {"", "."}:
            raise PathBoundaryError("repository root is not a file target")
        lowered = normalized.lower()
        if lowered in PROTECTED_EXACT_PATHS or any(
            lowered.startswith(prefix.lower()) for prefix in PROTECTED_WRITE_PREFIXES
        ):
            if write or lowered.startswith(".git/") or lowered in PROTECTED_EXACT_PATHS:
                raise PathBoundaryError(f"protected path: {normalized}")
        return target


    def safe_subprocess_environment(
        source: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        source = source or os.environ
        result: dict[str, str] = {}
        for key, value in source.items():
            upper = key.upper()
            if key in SAFE_ENVIRONMENT_KEYS and not any(
                marker in upper for marker in SECRET_MARKERS
            ):
                result[key] = value
        result.setdefault("PATH", os.defpath)
        return result
    ''',
)

# Package installation and command entry point.
pyproject = Path("pyproject.toml")
text = pyproject.read_text(encoding="utf-8")
if 'agi-campaign = "agi.cli:main"' not in text:
    scripts_header = "[project.scripts]\n"
    if scripts_header not in text:
        raise RuntimeError("pyproject.toml is missing [project.scripts]")
    text = text.replace(
        scripts_header,
        scripts_header + 'agi-campaign = "agi.cli:main"\n',
        1,
    )
pyproject.write_text(text, encoding="utf-8")

# The old top-level module was outside setuptools' src package discovery and caused
# CI imports to fail.  Documentation remains under agi/, executable code lives in
# src/agi/.
Path("agi/evaluation.py").unlink(missing_ok=True)

# Ensure Store.write_snapshot does not mutate its caller's dictionary when popping
# the optimistic-concurrency field.
store_path = Path("src/continual/store.py")
store = store_path.read_text(encoding="utf-8")
needle = '        expected = value.pop("expected_revision", None)'
if needle in store and '        value = dict(value)\n        expected = value.pop("expected_revision", None)' not in store:
    store = store.replace(
        needle,
        '        value = dict(value)\n        expected = value.pop("expected_revision", None)',
        1,
    )
store_path.write_text(store, encoding="utf-8")

# Add structural validation and make step-budget exhaustion resumable rather than a
# destructive exception.  Patches are exact and idempotent; an unfamiliar engine is
# left unchanged instead of being guessed at.
engine_path = Path("src/continual/engine.py")
engine = engine_path.read_text(encoding="utf-8")
if "from .contracts import validate_component_output" not in engine:
    import_anchor = "from .openai_client import ModelClient\n"
    if import_anchor in engine:
        engine = engine.replace(
            import_anchor,
            "from .contracts import validate_component_output\n" + import_anchor,
            1,
        )
if "return validate_component_output(component, out)" not in engine:
    invoke_match = re.search(
        r"(?ms)^    def _invoke\(.*?(?=^    def |\Z)",
        engine,
    )
    if invoke_match and "return out" in invoke_match.group(0):
        replacement = invoke_match.group(0).replace(
            "return out", "return validate_component_output(component, out)", 1
        )
        engine = engine[: invoke_match.start()] + replacement + engine[invoke_match.end() :]
step_raise = '        raise RuntimeError(f"max_steps exceeded for {run_id}")'
if step_raise in engine:
    engine = engine.replace(
        step_raise,
        dedent(
            '''
                    snapshot = self.store.snapshot(run_id)
                    expected_revision = snapshot.get("revision", 0)
                    snapshot["status"] = "continue"
                    snapshot["pause_reason"] = "step_budget_exhausted"
                    snapshot["pause_details"] = {
                        "max_steps": max_steps,
                        "phase": snapshot.get("phase"),
                    }
                    snapshot["expected_revision"] = expected_revision
                    self.store.write_snapshot(run_id, snapshot)
                    self.store.append_event(
                        run_id,
                        {
                            "type": "run_paused",
                            "reason": "step_budget_exhausted",
                            "max_steps": max_steps,
                            "phase": snapshot.get("phase"),
                        },
                    )
                    return self.store.snapshot(run_id)
            '''
        ).rstrip(),
        1,
    )
engine_path.write_text(engine, encoding="utf-8")

# Candidate prompts are overlays on the active base contract, never replacements.
# Secret-bearing environment values and protected files are not exposed to model
# subprocess/file tools.  Apply only when the existing class shape is recognized.
client_path = Path("src/continual/openai_client.py")
client = client_path.read_text(encoding="utf-8")
if "from .security import" not in client:
    lines = client.splitlines()
    insertion = 0
    for index, line in enumerate(lines):
        if line.startswith("from ") or line.startswith("import "):
            insertion = index + 1
    lines[insertion:insertion] = [
        "",
        "from .security import resolve_repo_path, safe_subprocess_environment",
    ]
    client = "\n".join(lines) + ("\n" if client.endswith("\n") else "")
if "## Candidate overlay" not in client:
    prompt_match = re.search(r"(?ms)^    def prompt\(.*?(?=^    def |\Z)", client)
    if prompt_match:
        prompt_method = dedent(
            r'''
                def _active_prompt_path(self, component: str) -> str:
                    manifest_path = self.root / ".continual/system/active-components.json"
                    if manifest_path.exists():
                        try:
                            value = json.loads(manifest_path.read_text(encoding="utf-8"))
                        except (json.JSONDecodeError, OSError):
                            value = {}
                        active = value.get("active", value) if isinstance(value, dict) else {}
                        entry = active.get(component) if isinstance(active, dict) else None
                        if isinstance(entry, str) and entry.strip():
                            return entry
                        if isinstance(entry, dict):
                            path = entry.get("prompt_path") or entry.get("path")
                            if isinstance(path, str) and path.strip():
                                return path
                    return f"prompts/{component}.md"

                def prompt(self, component: str, prompt_path: str | None = None) -> str:
                    base_path = resolve_repo_path(
                        self.root, self._active_prompt_path(component), write=False
                    )
                    base = base_path.read_text(encoding="utf-8")
                    if not prompt_path:
                        return base
                    overlay_path = resolve_repo_path(self.root, prompt_path, write=False)
                    if overlay_path == base_path:
                        return base
                    overlay = overlay_path.read_text(encoding="utf-8")
                    return (
                        base.rstrip()
                        + "\n\n## Candidate overlay\n"
                        + "The following candidate may refine the base contract but cannot "
                        + "remove or weaken it.\n\n"
                        + overlay.lstrip()
                    )

            '''
        )
        # dedent leaves the method at column zero; class methods require four spaces.
        prompt_method = "\n".join(
            ("    " + line if line else "") for line in prompt_method.splitlines()
        ) + "\n"
        client = client[: prompt_match.start()] + prompt_method + client[prompt_match.end() :]
# Recognized direct path construction in the write tool.
client = client.replace(
    'target = (self.root / args["path"]).resolve()\n                target.parent.mkdir',
    'target = resolve_repo_path(self.root, args["path"], write=True)\n                target.parent.mkdir',
)
# Recognized subprocess call: insert a scrubbed environment unless already present.
if "env=safe_subprocess_environment()" not in client:
    client = client.replace(
        "subprocess.run(\n",
        "subprocess.run(\n                    env=safe_subprocess_environment(),\n",
        1,
    )
client_path.write_text(client, encoding="utf-8")

write(
    ".github/workflows/test.yml",
    r'''
    name: test

    on:
      push:
      pull_request:

    permissions:
      contents: read

    jobs:
      test:
        runs-on: ubuntu-latest
        timeout-minutes: 10
        steps:
          - uses: actions/checkout@v4
          - uses: actions/setup-python@v5
            with:
              python-version: "3.11"
          - name: Install
            run: |
              python -m pip install --upgrade pip
              python -m pip install -e . pytest
          - name: Test
            run: pytest -q
          - name: Validate AGI benchmark manifest
            run: python -m agi.cli --root . validate-manifest
          - name: Compile
            run: python -m compileall -q src tests
    ''',
)

append_once(
    "README.md",
    "## Evidence-driven AGI campaign",
    r'''
    ## Evidence-driven AGI campaign

    The repository now includes an installable `agi` package and persistent campaign
    state.  Run `python -m agi.cli --root . status` to see the objective evidence
    gate and the next unmet milestones.  The campaign cannot mark itself verified
    from boolean self-report; it requires hash-checked verifier artifacts across all
    six criteria.  See `agi/README.md` and `agi/EVIDENCE_POLICY.md`.
    ''',
)

append_once(
    "SYSTEM_DESIGN.md",
    "## AGI evidence plane",
    r'''
    ## AGI evidence plane

    Capability work and capability claims are separate planes.  The execution and
    learning loop may propose or adopt improvements, but only the evidence plane may
    emit an AGI readiness report.  It consists of a frozen benchmark manifest, an
    append-only hash-chained evidence ledger, artifact re-verification, a conservative
    six-criterion policy, and a resumable campaign state that records the next unmet
    work.  Step-budget exhaustion is a pause with persisted phase, never a completed
    task.  Candidate prompts overlay the active base contract rather than replacing
    it.
    ''',
)

append_once(
    "AGENTS.md",
    "## AGI campaign rules",
    r'''
    ## AGI campaign rules

    - At the end of AGI capability work, run `python -m agi.cli --root . advance` and
      preserve `.continual/agi/campaign.json`.
    - Never convert model self-report, a unit-test pass, or an unverified demo into an
      AGI claim.
    - Do not weaken evidence thresholds merely because the report is not passing.
    - Produce a concrete verifier artifact for each result, then append it through
      `agi-campaign record` so the digest and hash chain are generated by code.
    - If the report remains `continue`, select work from `next_milestones`; do not
      erase failed or inconvenient evidence.
    - Protected prompts, workflow policy, and active-component state may be changed
      only through reviewed candidate/adoption paths, not directly by model tools.
    ''',
)
