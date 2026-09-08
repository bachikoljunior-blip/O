from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .benchmark import BenchmarkAgent, BenchmarkReport, run_suite
from .evaluation import EvidenceRecord, EvaluationPolicy, evaluate_evidence
from .workspace import WorkspaceAgent, WorkspaceReport, run_workspace_suite


@dataclass(frozen=True)
class CampaignRun:
    run_id: str
    core: BenchmarkReport
    workspace: WorkspaceReport

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "core": self.core.to_dict(),
            "workspace": self.workspace.to_dict(),
        }


@dataclass(frozen=True)
class CampaignReport:
    campaign_id: str
    runs: tuple[CampaignRun, ...]
    records: tuple[EvidenceRecord, ...]
    claim_evaluation: dict[str, Any]
    all_development_tasks_passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "runs": [run.to_dict() for run in self.runs],
            "records": [asdict(record) for record in self.records],
            "claim_evaluation": self.claim_evaluation,
            "all_development_tasks_passed": self.all_development_tasks_passed,
            "warning": (
                "Development campaign results are not production-tier AGI evidence. "
                "The claim gate remains conservative even when every development task passes."
            ),
        }

    def write(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        for run in self.runs:
            _write_json(run.core.to_dict(), output_dir / f"{run.run_id}-core.json")
            _write_json(run.workspace.to_dict(), output_dir / f"{run.run_id}-workspace.json")
        _write_json([asdict(record) for record in self.records], output_dir / "ledger.json")
        _write_json(self.claim_evaluation, output_dir / "claim-evaluation.json")
        _write_json(self.to_dict(), output_dir / "campaign.json")


def _write_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def run_campaign(
    *,
    campaign_id: str,
    core_agent_factory: Callable[[], BenchmarkAgent],
    workspace_agent_factory: Callable[[], WorkspaceAgent],
    runs: int = 2,
    artifact_prefix: str = "campaign-artifacts",
    tier: str = "development",
    independent: bool = False,
    policy: EvaluationPolicy | None = None,
) -> CampaignReport:
    if runs < 1:
        raise ValueError("runs must be at least 1")
    campaign_runs: list[CampaignRun] = []
    records: list[EvidenceRecord] = []
    for index in range(1, runs + 1):
        run_id = f"{campaign_id}-run-{index:02d}"
        core = run_suite(core_agent_factory())
        workspace = run_workspace_suite(workspace_agent_factory())
        campaign_run = CampaignRun(run_id=run_id, core=core, workspace=workspace)
        campaign_runs.append(campaign_run)
        records.extend(
            core.evidence_records(
                run_id=run_id,
                artifact_ref=f"{artifact_prefix}/{run_id}-core.json",
                tier=tier,
                independent=independent,
            )
        )
        records.extend(
            workspace.evidence_records(
                run_id=run_id,
                artifact_ref=f"{artifact_prefix}/{run_id}-workspace.json",
                tier=tier,
                independent=independent,
            )
        )
    evaluation = evaluate_evidence(records, policy or EvaluationPolicy())
    return CampaignReport(
        campaign_id=campaign_id,
        runs=tuple(campaign_runs),
        records=tuple(records),
        claim_evaluation=evaluation,
        all_development_tasks_passed=all(
            run.core.passed and run.workspace.passed for run in campaign_runs
        ),
    )
