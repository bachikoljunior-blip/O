from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .evaluation import EvaluationPolicy, evaluate_evidence


TRUST_ENV = "AGI_TRUSTED_ATTESTORS_JSON"


def load_trusted_attestors(env: dict[str, str] | None = None) -> dict[str, str]:
    source = os.environ if env is None else env
    raw = source.get(TRUST_ENV, "")
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{TRUST_ENV} must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{TRUST_ENV} must be a JSON object")
    result: dict[str, str] = {}
    for key, secret in value.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(secret, str) or not secret:
            raise ValueError(f"{TRUST_ENV} entries must be non-empty string pairs")
        result[key] = secret
    return result


def _read(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agi-evidence")
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--min-successes", type=int, default=3)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    raw = _read(args.evidence)
    records = raw.get("records", raw) if isinstance(raw, dict) else raw
    policy = EvaluationPolicy(min_successes_per_criterion=args.min_successes)
    trusted = load_trusted_attestors()
    result = evaluate_evidence(records, policy, trusted_attestors=trusted)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if not result["agi_claim_supported"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
