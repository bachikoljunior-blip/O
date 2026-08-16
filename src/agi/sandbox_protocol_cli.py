from __future__ import annotations

import argparse
import json
from pathlib import Path

from .longhorizon import ReferenceLongHorizonAgent
from .sandbox_protocol import (
    SandboxProtocolInstance,
    deterministic_sandbox_instances,
    run_sandbox_protocol,
    validate_sandbox_instances,
    write_protocol_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agi-longhorizon")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser(
        "validate-protocol",
        help="Validate the deterministic repeated long-horizon protocol definition.",
    )
    reference = sub.add_parser(
        "run-reference",
        help="Run the persisted-checkpoint reference protocol. Reference output is not AGI evidence.",
    )
    reference.add_argument("--output-dir", type=Path, required=True)
    reference.add_argument("--instances", type=int, default=3)
    return parser


def _dump(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _reference_factory(_instance: SandboxProtocolInstance) -> ReferenceLongHorizonAgent:
    return ReferenceLongHorizonAgent()


def main() -> None:
    args = build_parser().parse_args()
    if args.cmd == "validate-protocol":
        result = validate_sandbox_instances(deterministic_sandbox_instances())
        result["warning"] = "Protocol validation is harness validation only; it is not claim-grade AGI evidence."
        _dump(result)
        if not result["valid"]:
            raise SystemExit(1)
        return

    if args.cmd == "run-reference":
        instances = deterministic_sandbox_instances(args.instances)
        report = run_sandbox_protocol(
            _reference_factory,
            sandbox_root=args.output_dir / "sandbox",
            instances=instances,
        )
        report_path = args.output_dir / "report.json"
        write_protocol_report(report, report_path)
        _dump(
            {
                "passed": report.passed,
                "instance_count": report.instance_count,
                "verified_checkpoints": report.verified_checkpoints,
                "retention_passes": report.retention_passes,
                "protected_regression_passes": report.protected_regression_passes,
                "protocol_digest": report.protocol_digest,
                "report": report_path.as_posix(),
                "warning": "The task-specific reference validates the protocol only and is not AGI evidence.",
            }
        )
        if not report.passed:
            raise SystemExit(1)
        return

    raise SystemExit(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    main()
