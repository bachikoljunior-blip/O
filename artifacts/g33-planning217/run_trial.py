"""One locked trial per process. Existing start/result forbids a second run."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time

import astar
import baseline
from protocol import selected_levels
from replay import verify
from search_core import parse_board


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_new(path, record):
    # Exclusive creation is intentionally not an overwrite/retry path.
    with path.open("x", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--method", required=True, choices=["baseline", "astar"])
    parser.add_argument("--index", required=True, type=int, choices=[0, 1, 2])
    parser.add_argument("--dispatch-receipt", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    precommit_raw = (root / "precommit.json").read_bytes()
    lock = json.loads(precommit_raw)
    dispatch = json.loads(args.dispatch_receipt.read_text())
    trial_id = f"g33-planning217-level{args.index}-{args.method}-v1"
    for name, expected in lock["locked_sha256"].items():
        if sha((root / name).read_bytes()) != expected:
            raise ValueError("locked file changed: " + name)
    if dispatch["trial_id"] != trial_id or dispatch["precommit_sha256"] != sha(precommit_raw):
        raise ValueError("dispatch identity mismatch")
    for field in ("execution_id", "lease_generation", "fence_token_digest", "work_invocation_id", "request_digest"):
        if dispatch[field] != lock[field]:
            raise ValueError("dispatch authority mismatch: " + field)
    if dispatch.get("state_readback_verified") is not True or dispatch.get("precommit_main_readback_verified") is not True:
        raise ValueError("remote proof missing")
    observed = datetime.fromisoformat(dispatch["observed_at"].replace("Z", "+00:00"))
    age = (datetime.now(timezone.utc) - observed).total_seconds()
    if not 0 <= age <= 300:
        raise ValueError("stale or future dispatch observation")
    levels = selected_levels((root / "boxoban-hard-000.txt").read_bytes())
    level = levels[args.index]
    if level["board_sha256"] != lock["levels"][args.index]["board_sha256"]:
        raise ValueError("locked selected board changed")
    outputs = root / "trials"
    outputs.mkdir(exist_ok=True)
    start_path = outputs / (trial_id + ".start.json")
    result_path = outputs / (trial_id + ".result.json")
    if start_path.exists() or result_path.exists():
        print(json.dumps({"status": "already_started_no_reexecution", "trial_id": trial_id,
                          "result_exists": result_path.exists()}))
        return
    ordered = [(i, method) for i in range(3) for method in ("baseline", "astar")]
    for index, method in ordered[:ordered.index((args.index, args.method))]:
        previous = outputs / f"g33-planning217-level{index}-{method}-v1.result.json"
        if not previous.exists():
            raise ValueError("previous trial result missing; query accepted process, do not skip")
    started_at = datetime.now(timezone.utc).isoformat()
    marker = {"trial_id": trial_id, "started_at": started_at, "process_id": os.getpid(),
              "precommit_sha256": sha(precommit_raw), "dispatch": dispatch,
              "method": args.method, "index": args.index, "board_sha256": level["board_sha256"],
              "budget": lock["budget"], "retry_permitted": False}
    write_new(start_path, marker)
    start_clock = time.monotonic()
    try:
        board = parse_board(level["rows"])
        remaining = max(0, 60 - (time.monotonic() - start_clock))
        method = baseline.solve if args.method == "baseline" else astar.solve
        outcome = method(board, max_expansions=50000, seconds=remaining)
        judged = verify(level["rows"], outcome["actions"]) if outcome["status"] == "solved" else None
        if outcome["status"] == "solved" and not judged["valid"]:
            outcome["status"] = "invalid_solution"
        result = {**marker, "outcome": outcome, "replay": judged,
                  "total_elapsed_seconds": time.monotonic() - start_clock,
                  "completed_at": datetime.now(timezone.utc).isoformat(),
                  "python": sys.version, "platform": platform.platform(),
                  "claim_boundary": "same-operator public-corpus bounded trial, not independent evaluation or AGI"}
    except Exception as error:
        result = {**marker, "outcome": {"status": "error", "error_type": type(error).__name__,
                                      "message": str(error), "termination_cause": "exception"},
                  "total_elapsed_seconds": time.monotonic() - start_clock,
                  "completed_at": datetime.now(timezone.utc).isoformat(), "replay": None}
    write_new(result_path, result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

