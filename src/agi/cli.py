from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .benchmark import ReferenceAgent, core_suite, run_suite, validate_suite
from .campaign import run_campaign
from .evaluation import EvaluationPolicy, evaluate_evidence
from .heldout import (
    EvaluationIdentity,
    ReferenceHeldOutAgent,
    generate_heldout_suite,
    run_heldout_suite,
    seed_commitment,
    validate_heldout_suite,
)
from .openai_agent import OpenAIBenchmarkAgent, OpenAIWorkspaceAgent
from .workspace import (
    ReferenceWorkspaceAgent,
    run_workspace_suite,
    validate_workspace_suite,
    workspace_suite,
)


def _load(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _dump(value: Any, path: Path | None = None) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    if path is None:
        print(text, end="")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _add_model_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", help="OpenAI API model; defaults to OPENAI_MODEL or gpt-5.")


def _heldout_identities(executor_name: str) -> tuple[EvaluationIdentity, EvaluationIdentity, EvaluationIdentity]:
    return (
        EvaluationIdentity("generator:sealed-v1", "generator"),
        EvaluationIdentity(f"executor:{executor_name}", "executor"),
        EvaluationIdentity("scorer:deterministic-v1", "scorer"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agi-benchmark")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("validate-suite", help="Validate one-turn criterion coverage and contracts.")
    sub.add_parser(
        "validate-workspace-suite",
        help="Validate the multi-step tool-mediated workspace suite.",
    )
    heldout_validate = sub.add_parser(
        "validate-heldout-suite",
        help="Validate runtime-generated sealed task coverage with a public CI seed.",
    )
    heldout_validate.add_argument("--nonce", default="heldout-v1")

    reference = sub.add_parser("run-reference", help="Run the one-turn harness reference agent.")
    reference.add_argument("--output", type=Path)

    workspace_reference = sub.add_parser(
        "run-workspace-reference",
        help="Run the task-specific workspace harness reference agent.",
    )
    workspace_reference.add_argument("--output", type=Path)

    heldout_reference = sub.add_parser(
        "run-heldout-reference",
        help="Run the algorithmic reference over generated tasks without exposing sealed answers.",
    )
    heldout_reference.add_argument("--output", type=Path)
    heldout_reference.add_argument("--nonce", default="heldout-v1")

    live = sub.add_parser("run-openai", help="Run the one-turn development suite against OpenAI.")
    _add_model_argument(live)
    live.add_argument("--output", type=Path, required=True)

    workspace_live = sub.add_parser(
        "run-workspace-openai",
        help="Run the multi-turn workspace development suite against OpenAI.",
    )
    _add_model_argument(workspace_live)
    workspace_live.add_argument("--output", type=Path, required=True)

    heldout_live = sub.add_parser(
        "run-heldout-openai",
        help="Run a secret-seeded held-out suite against OpenAI. Seed comes only from AGI_HELDOUT_SEED.",
    )
    _add_model_argument(heldout_live)
    heldout_live.add_argument("--output", type=Path, required=True)
    heldout_live.add_argument("--nonce", default="heldout-v1")

    campaign_reference = sub.add_parser(
        "run-campaign-reference",
        help="Run repeated reference checks over both development suites.",
    )
    campaign_reference.add_argument("--runs", type=int, default=2)
    campaign_reference.add_argument("--campaign-id", default="reference-harness")
    campaign_reference.add_argument("--output-dir", type=Path, required=True)

    campaign_live = sub.add_parser(
        "run-campaign-openai",
        help="Run repeated OpenAI development campaigns over one-turn and workspace tasks.",
    )
    _add_model_argument(campaign_live)
    campaign_live.add_argument("--runs", type=int, default=2)
    campaign_live.add_argument("--campaign-id", default="openai-development")
    campaign_live.add_argument("--output-dir", type=Path, required=True)

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
    elif args.cmd == "validate-workspace-suite":
        result = validate_workspace_suite(workspace_suite())
        _dump(result)
        if not result["valid"]:
            raise SystemExit(1)
    elif args.cmd == "validate-heldout-suite":
        seed = "public-ci-validation-seed"
        tasks = generate_heldout_suite(seed, nonce=args.nonce)
        result = validate_heldout_suite(tasks)
        result["seed_commitment"] = seed_commitment(seed, args.nonce)
        result["warning"] = "The CI seed is public and validates the generator only; it is not held-out AGI evidence."
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
    elif args.cmd == "run-workspace-reference":
        report = run_workspace_suite(ReferenceWorkspaceAgent())
        payload = report.to_dict()
        payload["warning"] = (
            "This task-specific reference validates only the workspace harness and is not AGI evidence."
        )
        _dump(payload, args.output)
        if not report.passed:
            raise SystemExit(1)
    elif args.cmd == "run-heldout-reference":
        seed = "public-heldout-reference-seed"
        agent = ReferenceHeldOutAgent()
        generator, executor, scorer = _heldout_identities(agent.name)
        report = run_heldout_suite(
            agent,
            generate_heldout_suite(seed, nonce=args.nonce, generator=generator),
            seed_commit=seed_commitment(seed, args.nonce),
            generator=generator,
            executor=executor,
            scorer=scorer,
        )
        payload = report.to_dict()
        payload["warning"] = "Public-seed reference validates sealing and scoring only; it is not AGI evidence."
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
    elif args.cmd == "run-workspace-openai":
        report = run_workspace_suite(OpenAIWorkspaceAgent(model=args.model))
        payload = report.to_dict()
        payload["evidence_tier"] = "development"
        payload["warning"] = "A passing workspace development suite does not establish AGI."
        _dump(payload, args.output)
        if not report.passed:
            raise SystemExit(1)
    elif args.cmd == "run-heldout-openai":
        seed = os.environ.get("AGI_HELDOUT_SEED")
        if not seed:
            raise SystemExit("AGI_HELDOUT_SEED is required and must come from a secret evaluation source")
        agent = OpenAIBenchmarkAgent(model=args.model)
        generator, executor, scorer = _heldout_identities(agent.name)
        report = run_heldout_suite(
            agent,
            generate_heldout_suite(seed, nonce=args.nonce, generator=generator),
            seed_commit=seed_commitment(seed, args.nonce),
            generator=generator,
            executor=executor,
            scorer=scorer,
        )
        payload = report.to_dict()
        payload["evidence_tier"] = "development-heldout"
        payload["warning"] = (
            "The seed and expected values are not persisted, but one held-out run is still insufficient for an AGI claim."
        )
        _dump(payload, args.output)
        if not report.passed:
            raise SystemExit(1)
    elif args.cmd == "run-campaign-reference":
        report = run_campaign(
            campaign_id=args.campaign_id,
            core_agent_factory=ReferenceAgent,
            workspace_agent_factory=ReferenceWorkspaceAgent,
            runs=args.runs,
            artifact_prefix=args.output_dir.as_posix(),
        )
        report.write(args.output_dir)
        _dump(
            {
                "campaign_id": report.campaign_id,
                "all_development_tasks_passed": report.all_development_tasks_passed,
                "agi_claim_supported": report.claim_evaluation["agi_claim_supported"],
                "output_dir": args.output_dir.as_posix(),
                "warning": "Reference scripts validate harnesses only and are not AGI evidence.",
            }
        )
        if not report.all_development_tasks_passed:
            raise SystemExit(1)
    elif args.cmd == "run-campaign-openai":
        report = run_campaign(
            campaign_id=args.campaign_id,
            core_agent_factory=lambda: OpenAIBenchmarkAgent(model=args.model),
            workspace_agent_factory=lambda: OpenAIWorkspaceAgent(model=args.model),
            runs=args.runs,
            artifact_prefix=args.output_dir.as_posix(),
        )
        report.write(args.output_dir)
        _dump(
            {
                "campaign_id": report.campaign_id,
                "all_development_tasks_passed": report.all_development_tasks_passed,
                "agi_claim_supported": report.claim_evaluation["agi_claim_supported"],
                "output_dir": args.output_dir.as_posix(),
                "warning": "Development campaign evidence cannot establish AGI by itself.",
            }
        )
        if not report.all_development_tasks_passed:
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
