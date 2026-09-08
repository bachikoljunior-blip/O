from __future__ import annotations

import argparse
import json
from pathlib import Path

from .induction import run_induction_campaign


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agi-induction",
        description="Run the bounded held-out program-induction development campaign.",
    )
    parser.add_argument("--seed", required=True, help="Fresh evaluator seed for task generation.")
    parser.add_argument("--output", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = run_induction_campaign(args.seed).to_dict()
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    raise SystemExit(0 if report["passed"] else 2)


if __name__ == "__main__":
    main()
