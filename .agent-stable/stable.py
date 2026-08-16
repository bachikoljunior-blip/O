from __future__ import annotations

from pathlib import Path
from textwrap import dedent


def write(path: str, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dedent(content).lstrip(), encoding="utf-8")


def append_once(path: str, marker: str, content: str) -> None:
    target = Path(path)
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    if marker not in existing:
        target.write_text(existing.rstrip() + "\n\n" + dedent(content).strip() + "\n", encoding="utf-8")


# Prevent the root documentation directory from shadowing the src-layout package in
# editable checkouts.
write(
    "agi/__init__.py",
    r'''
    """Checkout compatibility wrapper for the installable src/agi package."""

    from pathlib import Path as _Path

    _src_package = _Path(__file__).resolve().parents[1] / "src" / "agi"
    if _src_package.is_dir():
        __path__.append(str(_src_package))

    from .evaluation import CRITERIA, EvaluationPolicy, evaluate_evidence, evaluate_records
    from .evidence import EvidenceLedger, EvidenceRecord

    __all__ = [
        "CRITERIA",
        "EvaluationPolicy",
        "EvidenceLedger",
        "EvidenceRecord",
        "evaluate_evidence",
        "evaluate_records",
    ]
    ''',
)
Path("agi/evaluation.py").unlink(missing_ok=True)

pyproject = Path("pyproject.toml")
text = pyproject.read_text(encoding="utf-8")
if 'agi-campaign = "agi.cli:main"' not in text:
    header = "[project.scripts]\n"
    if header not in text:
        raise RuntimeError("pyproject.toml is missing [project.scripts]")
    text = text.replace(header, header + 'agi-campaign = "agi.cli:main"\n', 1)
pyproject.write_text(text, encoding="utf-8")

write(
    ".github/workflows/test.yml",
    r'''
    name: test

    on:
      push:
      pull_request:

    permissions:
      contents: read

    jobs:
      test:
        runs-on: ubuntu-latest
        timeout-minutes: 10
        steps:
          - uses: actions/checkout@v4
          - uses: actions/setup-python@v5
            with:
              python-version: "3.11"
          - run: python -m pip install --upgrade pip
          - run: python -m pip install -e . pytest
          - run: pytest -q
          - run: python -m agi.cli --root . validate-manifest
          - run: python -m compileall -q src tests
    ''',
)

write(
    "tests/test_agi_evaluation.py",
    r'''
    from __future__ import annotations

    from agi.evaluation import EvaluationPolicy, evaluate_evidence, evaluate_records
    from agi.evidence import EvidenceRecord


    def make(criterion: str, **extra: object) -> EvidenceRecord:
        value: dict[str, object] = {
            "evidence_id": f"e-{criterion}",
            "criterion": criterion,
            "task_id": f"task-{criterion}",
            "domain": "software-engineering",
            "kind": "benchmark",
            "passed": True,
            "verifier": "independent-harness",
            "artifact": f"reports/{criterion}.json",
            "artifact_sha256": "a" * 64,
            "run_id": f"run-{criterion}",
        }
        value.update(extra)
        return EvidenceRecord.from_mapping(value)


    POLICY = EvaluationPolicy(
        breadth_tasks=1,
        breadth_domains=1,
        transfer_tasks=1,
        transfer_domains=1,
        autonomy_tasks=1,
        autonomy_min_steps=10,
        autonomy_recoveries=1,
        continual_cycles=1,
        self_improvement_trials=1,
        self_improvement_components=1,
        robustness_tasks=1,
        robustness_domains=1,
        robustness_perturbations=1,
    )


    def complete() -> list[EvidenceRecord]:
        return [
            make("breadth"),
            make("transfer", held_out=True),
            make(
                "autonomy",
                steps=10,
                recovered_failures=1,
                metadata={"long_horizon": True},
            ),
            make(
                "continual_learning",
                baseline_score=0.2,
                candidate_score=0.4,
                regression_score=0.9,
                regression_floor=0.8,
                metadata={"learning_cycle": True},
            ),
            make(
                "self_improvement",
                baseline_score=0.2,
                candidate_score=0.5,
                regression_score=0.9,
                regression_floor=0.8,
                metadata={"ab_tested": True, "self_modified_component": "planner"},
            ),
            make("robustness", perturbations=["distribution-shift"]),
        ]


    def test_boolean_self_report_is_rejected() -> None:
        report = evaluate_evidence({key: True for key in (
            "breadth", "transfer", "autonomy", "continual_learning",
            "self_improvement", "robustness"
        )})
        assert report["agi_claim_supported"] is False
        assert len(report["invalid_inputs"]) == 6


    def test_complete_objective_set_passes_declared_relaxed_policy() -> None:
        assert evaluate_records(complete(), POLICY)["agi_claim_supported"] is True


    def test_missing_criterion_blocks_claim() -> None:
        report = evaluate_records(complete()[:-1], POLICY)
        assert report["agi_claim_supported"] is False
        assert report["missing"] == ["robustness"]


    def test_self_verifier_does_not_count() -> None:
        records = complete()
        records[0] = make("breadth", verifier="model-self-report")
        assert evaluate_records(records, POLICY)["criteria"]["breadth"] is False
    ''',
)

write(
    "tests/test_evidence_ledger.py",
    r'''
    from __future__ import annotations

    import json
    from pathlib import Path

    import pytest

    from agi.evidence import EvidenceLedger, EvidenceRecord, LedgerIntegrityError, sha256_file


    def make(root: Path, artifact: Path) -> EvidenceRecord:
        return EvidenceRecord.from_mapping({
            "evidence_id": "e-1",
            "criterion": "breadth",
            "task_id": "breadth-01",
            "domain": "software-engineering",
            "kind": "deterministic_test",
            "passed": True,
            "verifier": "pytest-harness",
            "artifact": artifact.relative_to(root).as_posix(),
            "artifact_sha256": sha256_file(artifact),
            "run_id": "run-1",
        })


    def test_hash_chain_and_artifact_check(tmp_path: Path) -> None:
        artifact = tmp_path / "report.json"
        artifact.write_text('{"passed": true}', encoding="utf-8")
        ledger = EvidenceLedger(tmp_path / "ledger.jsonl")
        ledger.append(make(tmp_path, artifact))
        assert ledger.verify_artifacts(tmp_path) == []
        artifact.write_text('{"passed": false}', encoding="utf-8")
        assert ledger.verify_artifacts(tmp_path) == ["e-1"]


    def test_hash_chain_detects_record_rewrite(tmp_path: Path) -> None:
        artifact = tmp_path / "report.json"
        artifact.write_text("ok", encoding="utf-8")
        path = tmp_path / "ledger.jsonl"
        ledger = EvidenceLedger(path)
        ledger.append(make(tmp_path, artifact))
        value = json.loads(path.read_text(encoding="utf-8"))
        value["record"]["domain"] = "rewritten"
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")
        with pytest.raises(LedgerIntegrityError):
            ledger.load()
    ''',
)

write(
    "tests/test_agi_benchmark.py",
    r'''
    from agi.benchmark import default_suite


    def test_default_manifest_has_required_coverage() -> None:
        coverage = default_suite().coverage()
        assert coverage["breadth"] == {"tasks": 12, "domains": 6}
        assert coverage["transfer"] == {"tasks": 6, "domains": 3}
        assert coverage["autonomy"]["tasks"] == 3
        assert coverage["continual_learning"]["tasks"] == 3
        assert coverage["self_improvement"]["tasks"] == 3
        assert coverage["robustness"]["tasks"] == 6
    ''',
)

write(
    "tests/test_agi_campaign.py",
    r'''
    from pathlib import Path

    from agi.campaign import CampaignManager


    def test_campaign_persists_unmet_work_and_resumes(tmp_path: Path) -> None:
        manager = CampaignManager(tmp_path)
        first = manager.initialize()
        assert first["status"] == "continue"
        assert first["report"]["agi_claim_supported"] is False
        assert len(first["next_milestones"]) == 12
        second = CampaignManager(tmp_path).initialize()
        assert second["campaign_id"] == first["campaign_id"]
        advanced = CampaignManager(tmp_path).advance()
        assert advanced["iteration"] == first["iteration"] + 1


    def test_record_is_hashed_and_keeps_campaign_running(tmp_path: Path) -> None:
        manager = CampaignManager(tmp_path)
        manager.initialize()
        artifact = tmp_path / "reports/result.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text('{"passed": true}', encoding="utf-8")
        result = manager.record({
            "evidence_id": "breadth-e-1",
            "criterion": "breadth",
            "task_id": "breadth-01",
            "domain": "software-engineering",
            "kind": "benchmark",
            "passed": True,
            "verifier": "independent-benchmark-harness",
            "artifact_path": "reports/result.json",
            "run_id": "run-1",
        })
        assert len(result["entry_hash"]) == 64
        assert result["campaign"]["status"] == "continue"
    ''',
)

write(
    "tests/test_agi_e2e.py",
    r'''
    import json
    import subprocess
    import sys
    from pathlib import Path


    def invoke(root: Path, command: str) -> dict[str, object]:
        result = subprocess.run(
            [sys.executable, "-m", "agi.cli", "--root", str(root), command],
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(result.stdout)


    def test_cli_initializes_and_advances(tmp_path: Path) -> None:
        first = invoke(tmp_path, "init")
        assert first["status"] == "continue"
        manifest = invoke(tmp_path, "validate-manifest")
        assert manifest["coverage"]["breadth"]["tasks"] == 12
        second = invoke(tmp_path, "advance")
        assert second["campaign_id"] == first["campaign_id"]
    ''',
)

Path("tests/test_runtime_hardening.py").unlink(missing_ok=True)

append_once(
    "README.md",
    "## Evidence-driven AGI campaign",
    r'''
    ## Evidence-driven AGI campaign

    `python -m agi.cli --root . status` prints the persistent six-criterion
    evidence report and the next unmet milestones. Boolean self-report cannot pass;
    qualifying records require hash-verified artifacts. See `agi/README.md` and
    `agi/EVIDENCE_POLICY.md`.
    ''',
)
append_once(
    "SYSTEM_DESIGN.md",
    "## AGI evidence plane",
    r'''
    ## AGI evidence plane

    Capability construction and capability claims are separated. The evidence plane
    uses a frozen benchmark manifest, append-only hash-chained records, artifact
    re-verification, conservative multi-domain thresholds, and resumable campaign
    state. Unmet criteria remain explicit work rather than being relabeled success.
    ''',
)
append_once(
    "AGENTS.md",
    "## AGI campaign rules",
    r'''
    ## AGI campaign rules

    - Run `python -m agi.cli --root . advance` after AGI capability work.
    - Preserve `.continual/agi/campaign.json` and select subsequent work from its
      `next_milestones` while status is `continue`.
    - Never treat model self-report, a unit-test pass, or an unverified demo as AGI.
    - Never weaken evidence thresholds merely to obtain a passing report.
    - Append concrete verifier artifacts through `agi-campaign record`; do not erase
      failed or inconvenient historical evidence.
    ''',
)
