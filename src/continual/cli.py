from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .engine import Engine


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="continual")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository root (default: current directory).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    start = sub.add_parser("start", help="Start one persistent task run.")
    start.add_argument("request")
    start.add_argument("--max-steps", type=int, default=64)

    resume = sub.add_parser("resume", help="Resume one persistent run.")
    resume.add_argument("run_id")
    resume.add_argument("--max-steps", type=int, default=64)

    resume_all = sub.add_parser("resume-all", help="Resume every non-terminal run.")
    resume_all.add_argument("--max-steps", type=int, default=16)

    status = sub.add_parser("status", help="Print the persisted snapshot for a run.")
    status.add_argument("run_id")

    feedback = sub.add_parser("feedback", help="Append user feedback to an episode.")
    feedback.add_argument("episode_id")
    feedback.add_argument("text")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    engine = Engine(args.root)
    if args.cmd == "start":
        run_id = engine.start(args.request, max_steps=args.max_steps)
        _print({"run_id": run_id, "snapshot": engine.store.snapshot(run_id)})
    elif args.cmd == "resume":
        _print(engine.resume(args.run_id, max_steps=args.max_steps))
    elif args.cmd == "resume-all":
        _print(engine.resume_all(max_steps=args.max_steps))
    elif args.cmd == "status":
        _print(engine.store.snapshot(args.run_id))
    elif args.cmd == "feedback":
        engine.feedback(args.episode_id, args.text)
        _print({"ok": True, "episode_id": args.episode_id})


if __name__ == "__main__":
    main()
