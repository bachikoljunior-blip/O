from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agi.regression import RegressionPolicy

from .candidate_regression import record_candidate_regression
from .ci_source_observation import (
    prepare_ci_source_observation,
    record_ci_source_observation_receipt,
)
from .engine import Engine
from .self_application import record_self_application
from .work_session import (
    WorkSession,
    pending_work_invocations,
    submit_work_response,
    verify_work_invocations,
)
from .work_checkpoint_integrity import verify_work_checkpoint_integrity
from .work_source_observation import (
    prepare_work_source_observation,
    record_work_source_observation_receipt,
)


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="continual")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository root (default: current directory).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    start = sub.add_parser("start", help="Start one persistent task run.")
    start.add_argument("request")
    start.add_argument("--max-steps", type=int, default=64)

    resume = sub.add_parser("resume", help="Resume one persistent run.")
    resume.add_argument("run_id")
    resume.add_argument("--max-steps", type=int, default=64)

    resume_all = sub.add_parser("resume-all", help="Resume every non-terminal run.")
    resume_all.add_argument("--max-steps", type=int, default=16)

    status = sub.add_parser("status", help="Print the persisted snapshot for a run.")
    status.add_argument("run_id")

    feedback = sub.add_parser("feedback", help="Append user feedback to an episode.")
    feedback.add_argument("episode_id")
    feedback.add_argument("text")

    work_start = sub.add_parser(
        "work-start",
        help="Start a native O Run whose semantic provider is the current ChatGPT Work session.",
    )
    work_start.add_argument("request")
    work_start.add_argument("--max-steps", type=int, default=64)
    work_start.add_argument("--run-id")
    work_start.add_argument("--executor-binding", default="current_chatgpt_work_session")
    work_start.add_argument("--model-identity", default="chatgpt-work-model-unverified")

    work_resume = sub.add_parser("work-resume", help="Resume one Work-backed native O Run.")
    work_resume.add_argument("run_id")
    work_resume.add_argument("--max-steps", type=int, default=64)
    work_resume.add_argument("--executor-binding", default="current_chatgpt_work_session")
    work_resume.add_argument("--model-identity", default="chatgpt-work-model-unverified")

    work_pending = sub.add_parser("work-pending", help="List frozen Work-model requests.")
    work_pending.add_argument("--run-id")

    work_verify = sub.add_parser("work-verify", help="Verify all persisted Work invocations.")
    work_verify.add_argument("--run-id")

    checkpoint_verify = sub.add_parser(
        "work-checkpoint-verify",
        help="Read-only verification of durable Work checkpoint references.",
    )
    checkpoint_verify.add_argument(
        "--state",
        type=Path,
        default=Path("agi/WORK_EXECUTION_STATE.json"),
    )

    work_submit = sub.add_parser("work-submit", help="Submit one immutable Work-model response.")
    work_submit.add_argument("invocation_id")
    work_submit.add_argument("--response", type=Path, required=True)
    work_submit.add_argument("--executor-binding", default="current_chatgpt_work_session")
    work_submit.add_argument("--model-identity", required=True)
    work_submit.add_argument("--model-verified", action="store_true")

    source_prepare = sub.add_parser(
        "work-source-observation-prepare",
        help="Precommit one mandatory Work-state connector observation.",
    )
    source_prepare.add_argument("run_id")
    source_prepare.add_argument("--state-blob-sha", required=True)
    source_prepare.add_argument("--expected-commit-sha", required=True)
    source_prepare.add_argument("--model-identity", required=True)

    source_record = sub.add_parser(
        "work-source-observation-record",
        help="Record one connector receipt for a precommitted Work-state observation.",
    )
    source_record.add_argument("run_id")
    source_record.add_argument("observation_id")
    source_record.add_argument("--request-digest", required=True)
    source_record.add_argument("--commit-sha", required=True)
    source_record.add_argument("--blob-sha", required=True)
    source_record.add_argument("--projection", type=Path, required=True)
    source_record.add_argument("--observed-at", required=True)
    source_record.add_argument("--executor-binding", required=True)
    source_record.add_argument("--model-identity", required=True)

    ci_prepare = sub.add_parser(
        "ci-source-observation-prepare",
        help="Precommit one decision-relevant exact-head CI connector observation.",
    )
    ci_prepare.add_argument("run_id")
    ci_prepare.add_argument("--model-identity", required=True)

    ci_record = sub.add_parser(
        "ci-source-observation-record",
        help="Record bounded workflow run/jobs connector evidence.",
    )
    ci_record.add_argument("run_id")
    ci_record.add_argument("observation_id")
    ci_record.add_argument("--request-digest", required=True)
    ci_record.add_argument("--workflow-run", type=Path, required=True)
    ci_record.add_argument("--jobs", type=Path, required=True)
    ci_record.add_argument("--observed-at", required=True)
    ci_record.add_argument("--executor-binding", required=True)
    ci_record.add_argument("--model-identity", required=True)

    self_apply = sub.add_parser(
        "self-apply",
        help="Persist one task-chat repository-development result as native O state.",
    )
    self_apply.add_argument(
        "--record",
        type=Path,
        required=True,
        help="JSON record containing outcomes, evidence, context risks and an optional Candidate.",
    )

    adopt = sub.add_parser(
        "candidate-regression",
        help="Recompute protected-baseline measurements and record one scoped Candidate decision.",
    )
    adopt.add_argument("candidate_id")
    adopt.add_argument("--scope", required=True)
    adopt.add_argument("--baseline", type=Path, required=True)
    adopt.add_argument("--candidate", type=Path, required=True)
    adopt.add_argument("--target-task", action="append", required=True)
    adopt.add_argument("--maximum-protected-score-drop", type=float, default=0.0)
    adopt.add_argument("--allowed-new-failures", type=int, default=0)
    adopt.add_argument("--minimum-mean-target-gain", type=float, default=0.01)
    adopt.add_argument("--minimum-target-repeats", type=int, default=2)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.cmd == "work-start":
        session = WorkSession(
            args.root,
            executor_binding=args.executor_binding,
            model_identity=args.model_identity,
        )
        _print(session.start(args.request, max_steps=args.max_steps, run_id=args.run_id))
        return

    if args.cmd == "work-resume":
        session = WorkSession(
            args.root,
            executor_binding=args.executor_binding,
            model_identity=args.model_identity,
        )
        _print(session.resume(args.run_id, max_steps=args.max_steps))
        return

    if args.cmd == "work-pending":
        _print({"pending": pending_work_invocations(args.root, run_id=args.run_id)})
        return

    if args.cmd == "work-verify":
        _print(verify_work_invocations(args.root, run_id=args.run_id))
        return

    if args.cmd == "work-checkpoint-verify":
        _print(
            verify_work_checkpoint_integrity(
                args.root,
                state_path=args.state,
            )
        )
        return

    if args.cmd == "work-submit":
        raw = json.loads(args.response.read_text(encoding="utf-8"))
        output = raw.get("output") if isinstance(raw, dict) and "output" in raw else raw
        _print(
            submit_work_response(
                args.root,
                args.invocation_id,
                output,
                executor_binding=args.executor_binding,
                model_identity=args.model_identity,
                model_verified=args.model_verified,
            )
        )
        return

    if args.cmd == "work-source-observation-prepare":
        state = json.loads(
            (args.root / "agi" / "WORK_EXECUTION_STATE.json").read_text(
                encoding="utf-8"
            )
        )
        _print(
            prepare_work_source_observation(
                args.root,
                run_id=args.run_id,
                state=state,
                state_blob_sha=args.state_blob_sha,
                expected_commit_sha=args.expected_commit_sha,
                model_identity=args.model_identity,
            )
        )
        return

    if args.cmd == "work-source-observation-record":
        projection = json.loads(args.projection.read_text(encoding="utf-8"))
        _print(
            record_work_source_observation_receipt(
                args.root,
                run_id=args.run_id,
                observation_id=args.observation_id,
                request_digest=args.request_digest,
                executor_binding=args.executor_binding,
                model_identity=args.model_identity,
                commit_sha=args.commit_sha,
                blob_sha=args.blob_sha,
                projection=projection,
                observed_at=args.observed_at,
            )
        )
        return

    if args.cmd == "ci-source-observation-prepare":
        state = json.loads(
            (args.root / "agi" / "WORK_EXECUTION_STATE.json").read_text(
                encoding="utf-8"
            )
        )
        _print(
            prepare_ci_source_observation(
                args.root,
                run_id=args.run_id,
                state=state,
                model_identity=args.model_identity,
            )
        )
        return

    if args.cmd == "ci-source-observation-record":
        workflow_run = json.loads(args.workflow_run.read_text(encoding="utf-8"))
        jobs = json.loads(args.jobs.read_text(encoding="utf-8"))
        _print(
            record_ci_source_observation_receipt(
                args.root,
                run_id=args.run_id,
                observation_id=args.observation_id,
                request_digest=args.request_digest,
                executor_binding=args.executor_binding,
                model_identity=args.model_identity,
                workflow_run=workflow_run,
                jobs=jobs,
                observed_at=args.observed_at,
            )
        )
        return

    if args.cmd == "self-apply":
        record = json.loads(args.record.read_text(encoding="utf-8"))
        _print(record_self_application(args.root, record))
        return

    if args.cmd == "candidate-regression":
        policy = RegressionPolicy(
            maximum_protected_score_drop=args.maximum_protected_score_drop,
            allowed_new_failures=args.allowed_new_failures,
            minimum_mean_target_gain=args.minimum_mean_target_gain,
            minimum_target_repeats=args.minimum_target_repeats,
        )
        result = record_candidate_regression(
            args.root,
            candidate_id=args.candidate_id,
            scope=args.scope,
            baseline_path=args.baseline,
            candidate_path=args.candidate,
            target_task_ids=args.target_task,
            policy=policy,
        )
        _print(result)
        raise SystemExit(0 if result["decision"]["adopt_candidate"] else 2)

    engine = Engine(args.root)
    if args.cmd == "start":
        run_id = engine.start(args.request, max_steps=args.max_steps)
        _print({"run_id": run_id, "snapshot": engine.store.snapshot(run_id)})
    elif args.cmd == "resume":
        _print(engine.resume(args.run_id, max_steps=args.max_steps))
    elif args.cmd == "resume-all":
        _print(engine.resume_all(max_steps=args.max_steps))
    elif args.cmd == "status":
        _print(engine.store.snapshot(args.run_id))
    elif args.cmd == "feedback":
        engine.feedback(args.episode_id, args.text)
        _print({"ok": True, "episode_id": args.episode_id})


if __name__ == "__main__":
    main()
