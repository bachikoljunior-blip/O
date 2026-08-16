from __future__ import annotations

from pathlib import Path
from textwrap import dedent


def write(path: str, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dedent(content).lstrip(), encoding="utf-8")


write(
    "src/agi/campaign.py",
    r'''
    from __future__ import annotations

    import json
    import os
    import tempfile
    import uuid
    from dataclasses import dataclass
    from pathlib import Path
    from typing import Any, Mapping

    from .benchmark import BenchmarkSuite, default_suite
    from .evaluation import evaluate_records
    from .evidence import EvidenceLedger, EvidenceRecord, sha256_file, utc_now


    def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise


    @dataclass(frozen=True, slots=True)
    class CampaignPaths:
        root: Path
        state: Path
        ledger: Path
        manifest: Path

        @classmethod
        def under(cls, root: Path) -> "CampaignPaths":
            root = root.resolve()
            campaign = root / ".continual" / "agi"
            return cls(
                root=root,
                state=campaign / "campaign.json",
                ledger=campaign / "evidence.jsonl",
                manifest=campaign / "benchmark-manifest.json",
            )


    class CampaignManager:
        """Persistent AGI evidence campaign that resumes from repository state."""

        def __init__(self, root: Path):
            self.paths = CampaignPaths.under(root)
            self.ledger = EvidenceLedger(self.paths.ledger)

        def initialize(self, *, force: bool = False) -> dict[str, Any]:
            if force or not self.paths.manifest.exists():
                default_suite().write(self.paths.manifest)
            else:
                BenchmarkSuite.read(self.paths.manifest)
            self.paths.ledger.parent.mkdir(parents=True, exist_ok=True)
            self.paths.ledger.touch(exist_ok=True)
            if self.paths.state.exists() and not force:
                return self.status()
            state = {
                "schema_version": 1,
                "campaign_id": f"agi-campaign-{uuid.uuid4().hex[:12]}",
                "status": "continue",
                "iteration": 0,
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "ledger_path": str(self.paths.ledger.relative_to(self.paths.root)),
                "manifest_path": str(self.paths.manifest.relative_to(self.paths.root)),
                "report": evaluate_records([]),
                "next_milestones": [],
                "history": [],
            }
            atomic_write_json(self.paths.state, state)
            return self.advance(increment=False)

        def _read_state(self) -> dict[str, Any]:
            if not self.paths.state.exists():
                return self.initialize()
            value = json.loads(self.paths.state.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("campaign state must be an object")
            return value

        def status(self) -> dict[str, Any]:
            return self._read_state()

        def _eligible_records(self) -> tuple[list[EvidenceRecord], list[str]]:
            records = self.ledger.load()
            mismatches = set(self.ledger.verify_artifacts(self.paths.root))
            return (
                [record for record in records if record.evidence_id not in mismatches],
                sorted(mismatches),
            )

        def _next_milestones(
            self,
            report: Mapping[str, Any],
            suite: BenchmarkSuite,
            records: list[EvidenceRecord],
        ) -> list[dict[str, Any]]:
            completed = {
                record.task_id
                for record in records
                if record.is_objective and record.passed
            }
            missing = list(report.get("missing", []))
            selected: list[dict[str, Any]] = []
            for criterion in missing:
                candidates = [
                    task
                    for task in suite.tasks
                    if task.criterion == criterion and task.task_id not in completed
                ]
                for task in candidates[:2]:
                    selected.append(
                        {
                            "task_id": task.task_id,
                            "criterion": task.criterion,
                            "domain": task.domain,
                            "description": task.description,
                            "verifier": task.verifier,
                            "held_out": task.held_out,
                            "minimum_steps": task.minimum_steps,
                            "perturbations": list(task.perturbations),
                            "required_output": (
                                "A verifier artifact plus a hash-chained EvidenceRecord."
                            ),
                        }
                    )
            return selected[:12]

        def advance(self, *, increment: bool = True) -> dict[str, Any]:
            state = self._read_state()
            suite = BenchmarkSuite.read(self.paths.manifest)
            eligible, artifact_mismatches = self._eligible_records()
            report = evaluate_records(eligible)
            report["artifact_mismatches"] = artifact_mismatches
            if artifact_mismatches:
                report["agi_claim_supported"] = False
            status = "verified" if report["agi_claim_supported"] else "continue"
            history = list(state.get("history", []))
            history.append(
                {
                    "iteration": int(state.get("iteration", 0)),
                    "at": utc_now(),
                    "status": status,
                    "evidence_digest": report["evidence_digest"],
                    "missing": report["missing"],
                }
            )
            next_state = {
                **state,
                "status": status,
                "iteration": int(state.get("iteration", 0)) + (1 if increment else 0),
                "updated_at": utc_now(),
                "report": report,
                "next_milestones": (
                    []
                    if status == "verified"
                    else self._next_milestones(report, suite, eligible)
                ),
                "history": history[-50:],
            }
            atomic_write_json(self.paths.state, next_state)
            return next_state

        def record(self, raw: Mapping[str, Any]) -> dict[str, Any]:
            payload = dict(raw)
            artifact_value = payload.pop("artifact_path", payload.get("artifact"))
            if not isinstance(artifact_value, str) or not artifact_value.strip():
                raise ValueError("artifact_path or artifact must identify a verifier artifact")
            artifact = (self.paths.root / artifact_value).resolve()
            try:
                relative = artifact.relative_to(self.paths.root)
            except ValueError as exc:
                raise ValueError("artifact must remain inside the repository") from exc
            if not artifact.is_file():
                raise FileNotFoundError(f"verifier artifact does not exist: {relative}")
            payload["artifact"] = relative.as_posix()
            payload["artifact_sha256"] = sha256_file(artifact)
            payload.setdefault("created_at", utc_now())
            record = EvidenceRecord.from_mapping(payload)
            entry_hash = self.ledger.append(record)
            state = self.advance()
            return {
                "entry_hash": entry_hash,
                "record": record.as_dict(),
                "campaign": state,
            }
    ''',
)

write(
    "src/agi/cli.py",
    r'''
    from __future__ import annotations

    import argparse
    import json
    from pathlib import Path
    from typing import Any

    from .benchmark import BenchmarkSuite, default_suite
    from .campaign import CampaignManager
    from .evaluation import evaluate_records


    def emit(value: Any) -> None:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


    def parser() -> argparse.ArgumentParser:
        command = argparse.ArgumentParser(
            prog="agi-campaign",
            description=(
                "Manage the repository's resumable, evidence-driven AGI research campaign."
            ),
        )
        command.add_argument(
            "--root",
            type=Path,
            default=Path.cwd(),
            help="repository root (default: current directory)",
        )
        subcommands = command.add_subparsers(dest="command", required=True)
        init = subcommands.add_parser("init", help="initialize persistent campaign state")
        init.add_argument("--force", action="store_true")
        subcommands.add_parser("status", help="print the persisted campaign state")
        subcommands.add_parser("advance", help="re-evaluate evidence and persist next work")
        subcommands.add_parser("evaluate", help="evaluate the ledger without changing state")
        subcommands.add_parser("validate-manifest", help="validate benchmark coverage")
        record = subcommands.add_parser("record", help="append one evidence JSON object")
        record.add_argument("evidence_file", type=Path)
        manifest = subcommands.add_parser("write-default-manifest")
        manifest.add_argument("path", type=Path, nargs="?")
        return command


    def main(argv: list[str] | None = None) -> int:
        args = parser().parse_args(argv)
        manager = CampaignManager(args.root)
        if args.command == "init":
            emit(manager.initialize(force=args.force))
            return 0
        if args.command == "status":
            emit(manager.status())
            return 0
        if args.command == "advance":
            emit(manager.advance())
            return 0
        if args.command == "evaluate":
            eligible, artifact_mismatches = manager._eligible_records()
            report = evaluate_records(eligible)
            report["artifact_mismatches"] = artifact_mismatches
            if artifact_mismatches:
                report["agi_claim_supported"] = False
            emit(report)
            return 0
        if args.command == "validate-manifest":
            suite = BenchmarkSuite.read(manager.paths.manifest)
            emit(suite.as_dict())
            return 0
        if args.command == "record":
            raw = json.loads(args.evidence_file.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("evidence file root must be an object")
            emit(manager.record(raw))
            return 0
        if args.command == "write-default-manifest":
            path = args.path or manager.paths.manifest
            default_suite().write(path)
            emit({"path": str(path), "status": "written"})
            return 0
        raise AssertionError(f"unhandled command: {args.command}")


    if __name__ == "__main__":
        raise SystemExit(main())
    ''',
)

write(
    "src/agi/__main__.py",
    r'''
    from .cli import main

    raise SystemExit(main())
    ''',
)

write(
    "agi/README.md",
    r'''
    # Evidence-driven AGI campaign

    This repository does **not** equate a successful unit test, a model's own
    statement, or a one-off benchmark with AGI.  It contains a persistent campaign
    for building broader capabilities and an explicit gate for deciding whether the
    available evidence supports an AGI claim under a declared policy.

    ## What now runs

    The campaign keeps three durable files under `.continual/agi/`:

    - `benchmark-manifest.json`: frozen milestone definitions spanning breadth,
      transfer, autonomy, continual learning, self-improvement, and robustness.
    - `evidence.jsonl`: an append-only SHA-256 hash chain of verifier records.
    - `campaign.json`: current status, diagnostics, history, and the next unmet
      milestones.  Its status remains `continue` until every criterion passes.

    A boolean such as `{"breadth": true}` cannot satisfy the gate.  A qualifying
    record must identify an external or deterministic verifier, a concrete artifact,
    and that artifact's digest.  The campaign re-hashes artifacts before counting
    them.

    ## Commands

    ```bash
    python -m agi.cli --root . init
    python -m agi.cli --root . status
    python -m agi.cli --root . advance
    python -m agi.cli --root . validate-manifest
    python -m agi.cli --root . record evidence-result.json
    ```

    The installed console entry point is `agi-campaign`.

    ## Claim boundary

    Passing the current policy is evidence for a claim **under that policy**.  It is
    not universal scientific proof.  The policy requires objective records across
    six domains, sealed transfer tasks, long-horizon recovery, before/after learning
    with protected regressions, A/B-tested self-improvement, and perturbation tests.
    Thresholds may be strengthened, but they must not be weakened merely to obtain a
    passing label.
    ''',
)

write(
    "agi/EVIDENCE_POLICY.md",
    r'''
    # AGI evidence policy v1

    The evaluation gate is intentionally one-way conservative:

    1. Only hash-chained records with a verifier artifact are eligible.
    2. Agent or model self-report is never objective evidence.
    3. Failed records remain in the ledger and cannot be rewritten away.
    4. Duplicate evidence IDs block a claim.
    5. Artifact digest mismatches block a claim until resolved by new evidence; the
       historical record remains unchanged.
    6. All six criteria must pass simultaneously.
    7. Regression protection is mandatory for continual learning and
       self-improvement.
    8. Transfer tasks are sealed and held out from capability construction.
    9. A passing report states the policy and evidence digest so it is reproducible.
    10. Thresholds cannot be reduced solely because the campaign is not passing.

    The default gate currently requires:

    - breadth: 12 verified tasks across 6 domains;
    - transfer: 6 held-out tasks across 3 domains;
    - autonomy: 3 long-horizon tasks, each at least 10 semantic steps and with at
      least one externally observed recovery;
    - continual learning: 3 measured learning cycles with preserved regressions;
    - self-improvement: 3 A/B trials spanning at least 2 self-modified components;
    - robustness: 6 tasks across 3 domains and at least 4 perturbation classes.

    These are minimum repository acceptance conditions, not a universally accepted
    definition of AGI.
    ''',
)
