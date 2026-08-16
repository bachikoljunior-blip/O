from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .resumable_capability_campaign import run_resumable_capability_campaign

_STAGE_NAMES = (
    "crossdomain_acquisition",
    "durable_transfer_rollback",
    "external_tool_engine",
)


def derive_stage_seeds(seed_source: str) -> dict[str, str]:
    """Derive per-stage development seeds without persisting the source in learner state."""
    if not isinstance(seed_source, str) or not seed_source.strip():
        raise ValueError("seed_source must be non-empty")
    return {
        stage: hashlib.sha256(
            f"O-capability-materialization-v1\0{seed_source}\0{stage}".encode("utf-8")
        ).hexdigest()
        for stage in _STAGE_NAMES
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agi-capability-campaign",
        description=(
            "Advance the repository's resumable, deterministic capability campaign. "
            "The seed source is used only in-memory; persistent campaign state contains commitments."
        ),
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--seed-source", required=True)
    parser.add_argument("--max-new-stages", type=int)
    parser.add_argument("--output", type=Path)
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    seeds = derive_stage_seeds(args.seed_source)
    report = run_resumable_capability_campaign(
        args.root,
        args.campaign_id,
        seeds=seeds,
        max_new_stages=args.max_new_stages,
    )
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.seed_source in encoded:
        raise RuntimeError("raw seed source appeared in capability campaign report")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report.get("passed") is True else 2


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
