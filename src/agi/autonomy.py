from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from continual.engine import Engine, TERMINAL_STATUSES


DEFAULT_REQUEST = """Continue building and externally verifying AGI from the repository's persisted state.
Read agi/CONTINUATION.json and the continual runtime state first. Do not treat a completed subtask,
commit, test, benchmark, episode, or self-report as completion of the user-level AGI goal. Continue to
the highest-value falsifiable missing capability or evidence milestone. Preserve safety, candidate
preflight/post-result evaluation, regression evidence, and the conservative AGI evidence gate.
"""

STATE_PATH = Path("agi/AUTONOMY_STATE.json")


class EngineLike(Protocol):
    store: Any

    def start(self, request: str, *, max_steps: int = 64) -> str: ...

    def resume(self, run_id: str, max_steps: int = 64) -> Any: ...


@dataclass(frozen=True)
class AutonomyResult:
    active_run_id: str
    cycles_completed: int
    restarts: int
    snapshot: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_run_id": self.active_run_id,
            "cycles_completed": self.cycles_completed,
            "restarts": self.restarts,
            "snapshot": self.snapshot,
        }


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def find_resumable_run(root: Path) -> str | None:
    runs_dir = root / ".continual" / "runs"
    if not runs_dir.exists():
        return None
    candidates: list[tuple[str, str]] = []
    for run_dir in runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        snapshot = _read_json(run_dir / "snapshot.json", {})
        if not isinstance(snapshot, dict) or snapshot.get("status") in TERMINAL_STATUSES:
            continue
        stamp = str(snapshot.get("updated_at") or snapshot.get("created_at") or "")
        candidates.append((stamp, run_dir.name))
    return max(candidates)[1] if candidates else None


def _continuation_summary(root: Path) -> dict[str, Any]:
    value = _read_json(root / "agi" / "CONTINUATION.json", {})
    return value if isinstance(value, dict) else {}


def _persist_autonomy_state(
    root: Path,
    *,
    run_id: str,
    snapshot: dict[str, Any],
    cycle: int,
    restarts: int,
) -> None:
    _write_json(
        root / STATE_PATH,
        {
            "schema_version": 1,
            "status": "in_progress",
            "active_run_id": run_id,
            "cycle": cycle,
            "restarts": restarts,
            "run_status": snapshot.get("status"),
            "run_phase": snapshot.get("phase"),
            "checkpoint_reason": snapshot.get("checkpoint_reason"),
            "continuation": _continuation_summary(root),
            "rule": "A terminal subtask run does not stop the AGI program; start the next AGI-development run immediately.",
        },
    )


def drive_autonomy(
    root: Path,
    *,
    cycles: int = 4,
    max_steps: int = 24,
    request: str = DEFAULT_REQUEST,
    engine: EngineLike | None = None,
) -> AutonomyResult:
    """Use bounded external runtime continuously without treating subtask completion as program completion.

    The caller controls the hard execution budget through ``cycles``/``max_steps``. Within that budget,
    every terminal run immediately spawns the next run. This means only the external process boundary,
    not a model's subtask-level PASS, creates an execution gap.
    """

    if cycles < 1 or max_steps < 1:
        raise ValueError("cycles and max_steps must be positive")
    root = root.resolve()
    selected = engine or Engine(root)
    run_id = find_resumable_run(root)
    restarts = 0
    completed = 0

    if run_id is None:
        run_id = selected.start(request, max_steps=max_steps)
        completed += 1

    snapshot = selected.store.snapshot(run_id)
    _persist_autonomy_state(root, run_id=run_id, snapshot=snapshot, cycle=completed, restarts=restarts)

    while completed < cycles:
        if snapshot.get("status") in TERMINAL_STATUSES:
            run_id = selected.start(request, max_steps=max_steps)
            restarts += 1
        else:
            selected.resume(run_id, max_steps=max_steps)
        completed += 1
        snapshot = selected.store.snapshot(run_id)
        _persist_autonomy_state(
            root,
            run_id=run_id,
            snapshot=snapshot,
            cycle=completed,
            restarts=restarts,
        )

    return AutonomyResult(run_id, completed, restarts, snapshot)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agi-autonomy")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--cycles", type=int, default=4)
    parser.add_argument("--max-steps", type=int, default=24)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = drive_autonomy(args.root, cycles=args.cycles, max_steps=args.max_steps)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
