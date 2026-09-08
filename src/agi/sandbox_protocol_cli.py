from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .longhorizon import ReferenceLongHorizonAgent
from .openai_agent import OpenAILongHorizonAgent
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
        help="Validate the deterministic semantically varied long-horizon protocol definition.",
    )
    reference = sub.add_parser(
        "run-reference",
        help="Run the varied persisted-checkpoint reference protocol. Reference output is not AGI evidence.",
    )
    reference.add_argument("--output-dir", type=Path, required=True)
    reference.add_argument("--instances", type=int, default=3)

    live = sub.add_parser(
        "run-openai",
        help="Run the varied long-horizon protocol with a live OpenAI model as development evidence.",
    )
    live.add_argument("--output-dir", type=Path, required=True)
    live.add_argument("--instances", type=int, default=3)
    live.add_argument("--model", default=None)
    return parser


def _dump(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _reference_factory(_instance: SandboxProtocolInstance) -> ReferenceLongHorizonAgent:
    return ReferenceLongHorizonAgent()


def _summary(report, report_path: Path, *, agent_kind: str, warning: str) -> dict[str, object]:
    return {
        "passed": report.passed,
        "agent_kind": agent_kind,
        "evidence_tier": "development",
        "instance_count": report.instance_count,
        "varied_task_count": report.varied_task_count,
        "verified_checkpoints": report.verified_checkpoints,
        "rollback_passes": report.rollback_passes,
        "retention_passes": report.retention_passes,
        "protected_regression_passes": report.protected_regression_passes,
        "task_commitments": [item.task_commitment for item in report.instances],
        "protocol_digest": report.protocol_digest,
        "report": report_path.as_posix(),
        "warning": warning,
    }


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
            _summary(
                report,
                report_path,
                agent_kind="task-specific-reference",
                warning="The task-specific reference validates the protocol only and is not AGI evidence.",
            )
        )
        if not report.passed:
            raise SystemExit(1)
        return

    if args.cmd == "run-openai":
        if not os.environ.get("OPENAI_API_KEY"):
            raise SystemExit(
                "OPENAI_API_KEY is required for a live development run; no credential value is read or persisted by this command."
            )
        instances = deterministic_sandbox_instances(args.instances)

        def factory(_instance: SandboxProtocolInstance) -> OpenAILongHorizonAgent:
            # No response/thread identifier is reused. Fresh factory calls therefore have no
            # private conversation continuity across the harness failure boundary.
            return OpenAILongHorizonAgent(model=args.model)

        report = run_sandbox_protocol(
            factory,
            sandbox_root=args.output_dir / "sandbox",
            instances=instances,
        )
        report_path = args.output_dir / "report.json"
        write_protocol_report(report, report_path)
        _dump(
            _summary(
                report,
                report_path,
                agent_kind="openai-live",
                warning=(
                    "This is development evidence only. Even a full pass does not satisfy the AGI claim gate; "
                    "independent production-tier evaluation remains required."
                ),
            )
        )
        if not report.passed:
            raise SystemExit(1)
        return

    raise SystemExit(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    main()
