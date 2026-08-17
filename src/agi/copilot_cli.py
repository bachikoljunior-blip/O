from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .campaign import run_campaign
from .copilot_sdk_client import CopilotResponsesClient
from .openai_agent import (
    OpenAIBenchmarkAgent,
    OpenAILongHorizonAgent,
    OpenAIWorkspaceAgent,
)
from .sandbox_protocol import (
    SandboxProtocolInstance,
    deterministic_sandbox_instances,
    run_sandbox_protocol,
    write_protocol_report,
)


_DEFAULT_MODEL = "gpt-5.4"
_BENCHMARK_OUTPUT_CONTRACT = """
For every benchmark response, `state_update` and `procedure_update` must each be JSON objects.
If no persistent state or procedure change is needed, return {} for that field. Never return null,
arrays, strings, numbers, or booleans for either field. The harness will reject malformed values.
The `answer` field must contain the direct task result rather than an explanatory wrapper. Put
optional diagnosis or explanation in `evidence`; do not wrap a requested scalar/list result inside
an object unless the task itself explicitly requests an object.
"""
_BENCHMARK_JSON_RETRY = """
The immediately preceding model response for this same benchmark input was rejected before any
answer was accepted because it was not exactly one JSON object. Retry once using only the required
benchmark response schema. Return the direct task result in `answer`, JSON objects for
`state_update` and `procedure_update`, and no prose or markdown outside the JSON object. Do not
assume the rejected response changed persistent state.
"""
_WORKSPACE_OUTPUT_CONTRACT = """
Every workspace turn must be exactly one JSON object and nothing else: no prose, markdown fences,
prefixes, suffixes, or commentary. A tool turn must use exactly the documented `kind`, `tool`, and
`arguments` action shape. Once the trace and latest observation show the externally checkable goal
is complete, return the documented `kind: final` JSON object on that turn; never replace it with a
natural-language completion message. Malformed or non-object output is rejected, not interpreted.
"""
_WORKSPACE_JSON_RETRY = """
The immediately preceding model response for this same workspace observation was rejected before
any action was accepted because it was not exactly one JSON action object. Retry once using only the
required JSON action schema. Do not add prose or markdown and do not assume any unrecorded action
occurred; use only the supplied observation and trace.
"""


class CopilotBenchmarkAgent(OpenAIBenchmarkAgent):
    def __init__(self, model: str):
        super().__init__(model=model, client=CopilotResponsesClient())
        self.name = f"github-copilot:{model}"

    def _respond(self, *, instructions: str, payload):
        strict_instructions = (
            instructions.rstrip() + "\n" + _BENCHMARK_OUTPUT_CONTRACT.strip() + "\n"
        )
        try:
            return super()._respond(instructions=strict_instructions, payload=payload)
        except (json.JSONDecodeError, ValueError):
            # The rejected output never becomes a benchmark answer or state update. One bounded
            # retry improves provider reliability while keeping the strict parser, evaluator,
            # task expectations, state/procedure validation, and scoring unchanged.
            return super()._respond(
                instructions=(
                    strict_instructions.rstrip() + "\n" + _BENCHMARK_JSON_RETRY.strip() + "\n"
                ),
                payload=payload,
            )


class CopilotWorkspaceAgent(OpenAIWorkspaceAgent):
    def __init__(self, model: str):
        super().__init__(model=model, client=CopilotResponsesClient())
        self.name = f"github-copilot-workspace:{model}"

    def _respond(self, *, instructions: str, payload):
        strict_instructions = (
            instructions.rstrip() + "\n" + _WORKSPACE_OUTPUT_CONTRACT.strip() + "\n"
        )
        try:
            return super()._respond(instructions=strict_instructions, payload=payload)
        except (json.JSONDecodeError, ValueError):
            # A malformed response never becomes a workspace action. One bounded retry improves
            # provider reliability while preserving the exact parser, tool contract, evaluator,
            # step budget, and fail-closed semantics. The retry sees the same persisted trace only.
            return super()._respond(
                instructions=(
                    strict_instructions.rstrip() + "\n" + _WORKSPACE_JSON_RETRY.strip() + "\n"
                ),
                payload=payload,
            )


class CopilotLongHorizonAgent(OpenAILongHorizonAgent):
    def __init__(self, model: str):
        super().__init__(model=model, client=CopilotResponsesClient())
        self.name = f"github-copilot-longhorizon:{model}"


def _model(value: str | None) -> str:
    return value or os.environ.get("COPILOT_MODEL", _DEFAULT_MODEL)


def _dump(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agi-copilot")
    sub = parser.add_subparsers(dest="cmd", required=True)

    campaign = sub.add_parser(
        "run-campaign",
        help="Run bounded core/workspace development campaigns through GitHub Copilot SDK.",
    )
    campaign.add_argument("--model")
    campaign.add_argument("--runs", type=int, default=2)
    campaign.add_argument("--campaign-id", default="copilot-development")
    campaign.add_argument("--output-dir", type=Path, required=True)

    longhorizon = sub.add_parser(
        "run-longhorizon",
        help="Run the durable varied long-horizon protocol through GitHub Copilot SDK.",
    )
    longhorizon.add_argument("--model")
    longhorizon.add_argument("--instances", type=int, default=3)
    longhorizon.add_argument("--output-dir", type=Path, required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    model = _model(args.model)

    if args.cmd == "run-campaign":
        if not 1 <= args.runs <= 5:
            raise SystemExit("runs must be an integer from 1 through 5")
        report = run_campaign(
            campaign_id=args.campaign_id,
            core_agent_factory=lambda: CopilotBenchmarkAgent(model),
            workspace_agent_factory=lambda: CopilotWorkspaceAgent(model),
            runs=args.runs,
            artifact_prefix=args.output_dir.as_posix(),
            tier="development",
            independent=False,
        )
        report.write(args.output_dir)
        _dump(
            {
                "provider": "github-copilot-sdk",
                "model": model,
                "campaign_id": report.campaign_id,
                "run_count": len(report.runs),
                "all_development_tasks_passed": report.all_development_tasks_passed,
                "agi_claim_supported": report.claim_evaluation["agi_claim_supported"],
                "output_dir": args.output_dir.as_posix(),
                "warning": (
                    "Copilot campaign output is internal development evidence only. "
                    "It cannot satisfy the independent production AGI gate."
                ),
            }
        )
        if not report.all_development_tasks_passed:
            raise SystemExit(1)
        return

    if args.cmd == "run-longhorizon":
        if not 1 <= args.instances <= 5:
            raise SystemExit("instances must be an integer from 1 through 5")

        def factory(_instance: SandboxProtocolInstance) -> CopilotLongHorizonAgent:
            return CopilotLongHorizonAgent(model)

        report = run_sandbox_protocol(
            factory,
            sandbox_root=args.output_dir / "sandbox",
            instances=deterministic_sandbox_instances(args.instances),
        )
        report_path = args.output_dir / "report.json"
        write_protocol_report(report, report_path)
        _dump(
            {
                "provider": "github-copilot-sdk",
                "model": model,
                "passed": report.passed,
                "instance_count": report.instance_count,
                "varied_task_count": report.varied_task_count,
                "verified_checkpoints": report.verified_checkpoints,
                "rollback_passes": report.rollback_passes,
                "retention_passes": report.retention_passes,
                "protected_regression_passes": report.protected_regression_passes,
                "protocol_digest": report.protocol_digest,
                "report": report_path.as_posix(),
                "warning": (
                    "Copilot long-horizon output is internal development evidence only. "
                    "It cannot satisfy the independent production AGI gate."
                ),
            }
        )
        if not report.passed:
            raise SystemExit(1)
        return

    raise SystemExit(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    main()
