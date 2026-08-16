from __future__ import annotations

import argparse
from pathlib import Path

from .engine import Engine


def main() -> None:
    parser = argparse.ArgumentParser(prog="continual")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start")
    p_start.add_argument("request")

    p_resume = sub.add_parser("resume")
    p_resume.add_argument("run_id")

    p_feedback = sub.add_parser("feedback")
    p_feedback.add_argument("episode_id")
    p_feedback.add_argument("text")

    args = parser.parse_args()
    engine = Engine(Path.cwd())
    if args.cmd == "start":
        print(engine.start(args.request))
    elif args.cmd == "resume":
        engine.resume(args.run_id)
    elif args.cmd == "feedback":
        engine.feedback(args.episode_id, args.text)


if __name__ == "__main__":
    main()
