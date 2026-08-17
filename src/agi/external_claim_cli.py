from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .external_claim import (
    DEFAULT_MIN_INDEPENDENT_GROUPS_PER_CRITERION,
    evaluate_external_ledger_claim,
)

DEFAULT_SECRET_ENV = "AGI_EVIDENCE_BRIDGE_SECRET"


def _load_object(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agi-external-claim",
        description=(
            "Audit independently signed production evidence and evaluate it through the strict "
            "six-criterion AGI claim gate. The bridge secret is read only from an environment variable."
        ),
    )
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--bridge-attestor-id", required=True)
    parser.add_argument(
        "--bridge-secret-env",
        default=DEFAULT_SECRET_ENV,
        help=f"Environment variable containing the bridge trust secret (default: {DEFAULT_SECRET_ENV}).",
    )
    parser.add_argument(
        "--min-independent-groups-per-criterion",
        type=int,
        default=DEFAULT_MIN_INDEPENDENT_GROUPS_PER_CRITERION,
        help=(
            "Minimum cryptographically distinct external evaluator groups required for every criterion. "
            f"The strict AGI claim CLI cannot lower this below {DEFAULT_MIN_INDEPENDENT_GROUPS_PER_CRITERION}."
        ),
    )
    parser.add_argument("--output", type=Path)
    return parser


def _failure(message: str) -> dict[str, Any]:
    return {
        "agi_claim_supported": False,
        "error": message,
        "claim": "AGI claim is not supported; external evidence evaluation did not complete.",
    }


def run_cli(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    environment = os.environ if environ is None else environ
    secret = environment.get(args.bridge_secret_env, "")
    try:
        if args.min_independent_groups_per_criterion < DEFAULT_MIN_INDEPENDENT_GROUPS_PER_CRITERION:
            report = _failure(
                "strict AGI claim quorum cannot be lowered below "
                f"{DEFAULT_MIN_INDEPENDENT_GROUPS_PER_CRITERION} independent groups per criterion"
            )
        elif not secret:
            report = _failure(
                f"required bridge secret environment variable is missing: {args.bridge_secret_env}"
            )
        else:
            report = evaluate_external_ledger_claim(
                _load_object(args.ledger),
                _load_object(args.registry),
                bridge_attestor_id=args.bridge_attestor_id,
                bridge_secret=secret,
                min_independent_groups_per_criterion=args.min_independent_groups_per_criterion,
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        report = _failure(str(exc))

    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if secret and secret in encoded:
        raise RuntimeError("bridge secret appeared in external claim report")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report.get("agi_claim_supported") is True else 2


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
