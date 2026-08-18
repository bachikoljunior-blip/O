from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any


STATUS_CONTEXT = "o/live-agi-request"
_ALLOWED_STATES = {"pending", "success", "failure", "error"}
_SHA256_HEX = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def terminal_state(job_status: str) -> str:
    return {
        "success": "success",
        "failure": "failure",
        "cancelled": "error",
    }.get(job_status, "error")


def build_status_payload(
    *,
    state: str,
    run_id: str,
    attempt: str,
    server_url: str,
    repository: str,
) -> dict[str, str]:
    if state not in _ALLOWED_STATES:
        raise ValueError(f"unsupported status state: {state}")
    if not run_id.isdigit() or not attempt.isdigit():
        raise ValueError("run_id and attempt must be decimal identifiers")
    if not server_url.startswith("https://"):
        raise ValueError("server_url must use https")
    if not _REPOSITORY.fullmatch(repository):
        raise ValueError("repository must be owner/name")
    phase = "started" if state == "pending" else f"finished: {state}"
    return {
        "state": state,
        "context": STATUS_CONTEXT,
        "description": f"bounded live campaign run {run_id} attempt {attempt} {phase}",
        "target_url": f"{server_url.rstrip('/')}/{repository}/actions/runs/{run_id}",
    }


def publish_request_status(
    state: str,
    *,
    environ: Mapping[str, str] | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, str]:
    env = os.environ if environ is None else environ
    token = env.get("GITHUB_TOKEN", "")
    repository = env.get("GITHUB_REPOSITORY", "")
    sha = env.get("GITHUB_SHA", "")
    run_id = env.get("GITHUB_RUN_ID", "")
    attempt = env.get("GITHUB_RUN_ATTEMPT", "")
    api_url = env.get("GITHUB_API_URL", "")
    server_url = env.get("GITHUB_SERVER_URL", "")

    if not token:
        raise ValueError("GITHUB_TOKEN is required")
    if not _SHA256_HEX.fullmatch(sha):
        raise ValueError("GITHUB_SHA must be a 40-character lowercase hexadecimal commit id")
    if not api_url.startswith("https://"):
        raise ValueError("GITHUB_API_URL must use https")

    payload = build_status_payload(
        state=state,
        run_id=run_id,
        attempt=attempt,
        server_url=server_url,
        repository=repository,
    )
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/repos/{repository}/statuses/{sha}",
        data=json.dumps(payload, sort_keys=True).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    with opener(request, timeout=30) as response:
        if getattr(response, "status", None) != 201:
            raise RuntimeError(
                f"commit-status creation returned HTTP {getattr(response, 'status', None)}"
            )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m agi.live_request_status")
    parser.add_argument("phase", choices=("pending", "final"))
    parser.add_argument("--job-status", choices=("success", "failure", "cancelled"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.phase == "pending":
        state = "pending"
    else:
        job_status = args.job_status or os.environ.get("JOB_STATUS", "")
        state = terminal_state(job_status)
    payload = publish_request_status(state)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
