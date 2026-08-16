from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .benchmark import ReferenceAgent, core_suite, run_suite, validate_suite
from .evaluation import EvaluationPolicy, evaluate_evidence
from .openai_agent import OpenAIBenchmarkAgent


def _load(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _dump(value: Any, path: Path | None = None) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path is None:
        print(text, end="")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agi-benchmark")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("validate-suite", help="Validate criterion coverage and task contracts.")

    reference = sub.add_parser("run-reference", help="Run the task-specific harness reference agent.")
    reference.add_argument("--output", type=Path)

    live = sub.add_parser("run-openai", help="Run the development suite against an OpenAI model.")
    live.add_argument("--model")
    live.add_argument("--output", type=Path, required=True)

    evaluate = sub.add_parser("evaluate", help="Evaluate an evidence ledger JSON file.")
    evaluate.add_argument("evidence", type=Path)
    evaluate.add_argument("--output", type=Path)
    evaluate.add_argument("--min-successes", type=int, default=3)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.cmd == "validate-suite":
        result = validate_suite(core_suite())
        _dump(result)
        if not result["valid"]:
            raise SystemExit(1)
    elif args.cmd == "run-reference":
        report = run_suite(ReferenceAgent())
        payload = report.to_dict()
        payload["warning"] = (
            "This task-specific reference validates only the benchmark harness and is not AGI evidence."
        )
        _dump(payload, args.output)
        if not report.passed:
            raise SystemExit(1)
    elif args.cmd == "run-openai":
        report = run_suite(OpenAIBenchmarkAgent(model=args.model))
        payload = report.to_dict()
        payload["evidence_tier"] = "development"
        payload["warning"] = "A passing development suite does not establish AGI."
        _dump(payload, args.output)
        if not report.passed:
            raise SystemExit(1)
    elif args.cmd == "evaluate":
        raw = _load(args.evidence)
        records = raw.get("records", raw) if isinstance(raw, dict) else raw
        policy = EvaluationPolicy(min_successes_per_criterion=args.min_successes)
        result = evaluate_evidence(records, policy)
        _dump(result, args.output)
        if not result["agi_claim_supported"]:
            raise SystemExit(2)


if __name__ == "__main__":
    main()
