from __future__ import annotations

import re
from pathlib import Path
from textwrap import dedent, indent


def write(path: str, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dedent(content).lstrip(), encoding="utf-8")


# Repair indentation if the exact conservative step-budget patch from 03_harden was
# inserted at column zero by textwrap.dedent.
engine_path = Path("src/continual/engine.py")
engine = engine_path.read_text(encoding="utf-8")
unindented = re.search(
    r"(?ms)^snapshot = self\.store\.snapshot\(run_id\)\n.*?^return self\.store\.snapshot\(run_id\)$",
    engine,
)
if unindented:
    engine = (
        engine[: unindented.start()]
        + indent(unindented.group(0), "        ")
        + engine[unindented.end() :]
    )
step_raise = '        raise RuntimeError(f"max_steps exceeded for {run_id}")'
if step_raise in engine:
    replacement = dedent(
        '''
        snapshot = self.store.snapshot(run_id)
        expected_revision = snapshot.get("revision", 0)
        snapshot["status"] = "continue"
        snapshot["pause_reason"] = "step_budget_exhausted"
        snapshot["pause_details"] = {
            "max_steps": max_steps,
            "phase": snapshot.get("phase"),
        }
        snapshot["expected_revision"] = expected_revision
        self.store.write_snapshot(run_id, snapshot)
        self.store.append_event(
            run_id,
            {
                "type": "run_paused",
                "reason": "step_budget_exhausted",
                "max_steps": max_steps,
                "phase": snapshot.get("phase"),
            },
        )
        return self.store.snapshot(run_id)
        '''
    ).strip()
    engine = engine.replace(step_raise, indent(replacement, "        "), 1)
engine_path.write_text(engine, encoding="utf-8")


# Move the scrubbed environment keyword to the end of the subprocess.run call so it
# cannot precede a positional command argument.
client_path = Path("src/continual/openai_client.py")
client = client_path.read_text(encoding="utf-8")
client = re.sub(
    r"\n\s*env=safe_subprocess_environment\(\),",
    "",
    client,
)
start = client.find("subprocess.run(")
if start >= 0:
    cursor = start + len("subprocess.run(")
    depth = 1
    quote: str | None = None
    escaped = False
    while cursor < len(client) and depth:
        char = client[cursor]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        cursor += 1
    if depth != 0:
        raise RuntimeError("could not parse subprocess.run call")
    call = client[start:cursor]
    if "env=safe_subprocess_environment()" not in call:
        closing = cursor - 1
        line_start = client.rfind("\n", start, closing) + 1
        closing_indent = client[line_start:closing]
        if closing_indent.strip():
            closing_indent = "                "
        argument_indent = closing_indent + "    "
        insertion = f"{argument_indent}env=safe_subprocess_environment(),\n"
        client = client[:line_start] + insertion + client[line_start:]
client_path.write_text(client, encoding="utf-8")

# Fail the bootstrap before tests if a generated source file is syntactically invalid.
for source in (
    "src/agi/evidence.py",
    "src/agi/evaluation.py",
    "src/agi/benchmark.py",
    "src/agi/campaign.py",
    "src/agi/cli.py",
    "src/continual/contracts.py",
    "src/continual/security.py",
    "src/continual/engine.py",
    "src/continual/openai_client.py",
):
    compile(Path(source).read_text(encoding="utf-8"), source, "exec")


write(
    "tests/test_agi_evaluation.py",
    r'''
    from __future__ import annotations

    from agi.evaluation import EvaluationPolicy, evaluate_evidence, evaluate_records
    from agi.evidence import EvidenceRecord


    def record(criterion: str, suffix: str = "1", **overrides: object) -> EvidenceRecord:
        value: dict[str, object] = {
            "evidence_id": f"{criterion}-{suffix}",
            "criterion": criterion,
            "task_id": f"task-{criterion}-{suffix}",
            "domain": "software-engineering",
            "kind": "benchmark",
            "passed": True,
            "verifier": "independent-harness",
            "artifact": f"reports/{criterion}-{suffix}.json",
            "artifact_sha256": "a" * 64,
            "run_id": f"run-{criterion}-{suffix}",
        }
        value.update(overrides)
        return EvidenceRecord.from_mapping(value)


    def relaxed_policy() -> EvaluationPolicy:
        return EvaluationPolicy(
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


    def complete_records() -> list[EvidenceRecord]:
        return [
            record("breadth"),
            record("transfer", held_out=True),
            record(
                "autonomy",
                steps=12,
                recovered_failures=1,
                metadata={"long_horizon": True},
            ),
            record(
                "continual_learning",
                baseline_score=0.4,
                candidate_score=0.6,
                regression_score=0.9,
                regression_floor=0.8,
                metadata={"learning_cycle": True},
            ),
            record(
                "self_improvement",
                baseline_score=0.4,
                candidate_score=0.7,
                regression_score=0.9,
                regression_floor=0.8,
                metadata={
                    "ab_tested": True,
                    "self_modified_component": "planner",
                },
            ),
            record("robustness", perturbations=["distribution-shift"]),
        ]


    def test_boolean_self_report_never_supports_agi_claim() -> None:
        report = evaluate_evidence(
            {
                "breadth": True,
                "transfer": True,
                "autonomy": True,
                "continual_learning": True,
                "self_improvement": True,
                "robustness": True,
            }
        )
        assert report["agi_claim_supported"] is False
        assert len(report["invalid_inputs"]) == 6
        assert report["objective_evidence"] == 0


    def test_all_six_objective_criteria_can_pass_declared_policy() -> None:
        report = evaluate_records(complete_records(), relaxed_policy())
        assert report["agi_claim_supported"] is True
        assert all(report["criteria"].values())


    def test_one_missing_criterion_blocks_claim() -> None:
        records = [
            item for item in complete_records() if item.criterion != "robustness"
        ]
        report = evaluate_records(records, relaxed_policy())
        assert report["agi_claim_supported"] is False
        assert report["missing"] == ["robustness"]


    def test_model_self_report_is_not_objective() -> None:
        records = complete_records()
        records[0] = record("breadth", verifier="model-self-report")
        report = evaluate_records(records, relaxed_policy())
        assert report["criteria"]["breadth"] is False
        assert records[0].evidence_id in report["non_objective_evidence"]


    def test_duplicate_evidence_id_blocks_claim() -> None:
        records = complete_records()
        records.append(records[0])
        report = evaluate_records(records, relaxed_policy())
        assert report["agi_claim_supported"] is False
        assert report["duplicate_evidence_ids"] == [records[0].evidence_id]


    def test_default_breadth_gate_requires_twelve_tasks_and_six_domains() -> None:
        records = [
            record(
                "breadth",
                str(index),
                domain=f"domain-{index % 6}",
                task_id=f"breadth-task-{index}",
            )
            for index in range(12)
        ]
        report = evaluate_records(records)
        assert report["criteria"]["breadth"] is True
        assert report["agi_claim_supported"] is False
    ''',
)

write(
    "tests/test_evidence_ledger.py",
    r'''
    from __future__ import annotations

    import json
    from pathlib import Path

    import pytest

    from agi.evidence import (
        EvidenceLedger,
        EvidenceRecord,
        LedgerIntegrityError,
        sha256_file,
    )


    def evidence(artifact: Path, root: Path) -> EvidenceRecord:
        return EvidenceRecord.from_mapping(
            {
                "evidence_id": "e-1",
                "criterion": "breadth",
                "task_id": "task-1",
                "domain": "software-engineering",
                "kind": "deterministic_test",
                "passed": True,
                "verifier": "pytest-harness",
                "artifact": artifact.relative_to(root).as_posix(),
                "artifact_sha256": sha256_file(artifact),
                "run_id": "run-1",
            }
        )


    def test_ledger_round_trip_and_artifact_verification(tmp_path: Path) -> None:
        artifact = tmp_path / "reports" / "result.json"
        artifact.parent.mkdir()
        artifact.write_text('{"passed": true}\n', encoding="utf-8")
        ledger = EvidenceLedger(tmp_path / ".continual/agi/evidence.jsonl")
        entry_hash = ledger.append(evidence(artifact, tmp_path))
        assert len(entry_hash) == 64
        assert [item.evidence_id for item in ledger.load()] == ["e-1"]
        assert ledger.verify_artifacts(tmp_path) == []


    def test_ledger_detects_tampering(tmp_path: Path) -> None:
        artifact = tmp_path / "result.json"
        artifact.write_text("ok", encoding="utf-8")
        path = tmp_path / "evidence.jsonl"
        ledger = EvidenceLedger(path)
        ledger.append(evidence(artifact, tmp_path))
        value = json.loads(path.read_text(encoding="utf-8"))
        value["record"]["domain"] = "tampered"
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")
        with pytest.raises(LedgerIntegrityError):
            ledger.load()


    def test_artifact_change_is_reported(tmp_path: Path) -> None:
        artifact = tmp_path / "result.txt"
        artifact.write_text("original", encoding="utf-8")
        ledger = EvidenceLedger(tmp_path / "ledger.jsonl")
        ledger.append(evidence(artifact, tmp_path))
        artifact.write_text("changed", encoding="utf-8")
        assert ledger.verify_artifacts(tmp_path) == ["e-1"]
    ''',
)

write(
    "tests/test_agi_benchmark.py",
    r'''
    from __future__ import annotations

    import copy

    import pytest

    from agi.benchmark import BenchmarkManifestError, BenchmarkSuite, default_suite


    def test_default_suite_covers_policy_shape() -> None:
        suite = default_suite()
        coverage = suite.coverage()
        assert coverage["breadth"] == {"tasks": 12, "domains": 6}
        assert coverage["transfer"] == {"tasks": 6, "domains": 3}
        assert coverage["autonomy"]["tasks"] == 3
        assert coverage["continual_learning"]["tasks"] == 3
        assert coverage["self_improvement"]["tasks"] == 3
        assert coverage["robustness"] == {"tasks": 6, "domains": 6}


    def test_transfer_tasks_cannot_be_reclassified_as_seen() -> None:
        value = default_suite().as_dict()
        broken = copy.deepcopy(value)
        transfer = next(
            task for task in broken["tasks"] if task["criterion"] == "transfer"
        )
        transfer["held_out"] = False
        with pytest.raises(BenchmarkManifestError):
            BenchmarkSuite.from_mapping(broken)


    def test_robustness_tasks_require_declared_perturbations() -> None:
        value = default_suite().as_dict()
        broken = copy.deepcopy(value)
        robustness = next(
            task for task in broken["tasks"] if task["criterion"] == "robustness"
        )
        robustness["perturbations"] = []
        with pytest.raises(BenchmarkManifestError):
            BenchmarkSuite.from_mapping(broken)
    ''',
)

write(
    "tests/test_agi_campaign.py",
    r'''
    from __future__ import annotations

    from pathlib import Path

    from agi.campaign import CampaignManager


    def test_initialize_persists_resumable_unmet_work(tmp_path: Path) -> None:
        manager = CampaignManager(tmp_path)
        state = manager.initialize()
        assert state["status"] == "continue"
        assert state["report"]["agi_claim_supported"] is False
        assert set(state["report"]["missing"]) == {
            "breadth",
            "transfer",
            "autonomy",
            "continual_learning",
            "self_improvement",
            "robustness",
        }
        assert len(state["next_milestones"]) == 12
        assert manager.paths.state.is_file()
        assert manager.paths.manifest.is_file()
        assert manager.paths.ledger.is_file()


    def test_initialize_resumes_same_campaign(tmp_path: Path) -> None:
        manager = CampaignManager(tmp_path)
        first = manager.initialize()
        second = CampaignManager(tmp_path).initialize()
        assert second["campaign_id"] == first["campaign_id"]
        advanced = CampaignManager(tmp_path).advance()
        assert advanced["iteration"] == first["iteration"] + 1


    def test_record_hashes_artifact_and_tampering_removes_eligibility(
        tmp_path: Path,
    ) -> None:
        manager = CampaignManager(tmp_path)
        manager.initialize()
        artifact = tmp_path / "reports" / "breadth-1.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text('{"passed": true}\n', encoding="utf-8")
        result = manager.record(
            {
                "evidence_id": "breadth-e-1",
                "criterion": "breadth",
                "task_id": "breadth-01",
                "domain": "software-engineering",
                "kind": "benchmark",
                "passed": True,
                "verifier": "independent-benchmark-harness",
                "artifact_path": "reports/breadth-1.json",
                "run_id": "run-1",
            }
        )
        assert len(result["entry_hash"]) == 64
        assert result["record"]["artifact_sha256"]
        assert result["campaign"]["status"] == "continue"
        artifact.write_text('{"passed": false}\n', encoding="utf-8")
        state = manager.advance()
        assert state["report"]["artifact_mismatches"] == ["breadth-e-1"]
        assert state["report"]["agi_claim_supported"] is False
    ''',
)

write(
    "tests/test_agi_e2e.py",
    r'''
    from __future__ import annotations

    import json
    import subprocess
    import sys
    from pathlib import Path


    def run_cli(root: Path, command: str) -> dict[str, object]:
        completed = subprocess.run(
            [sys.executable, "-m", "agi.cli", "--root", str(root), command],
            check=True,
            text=True,
            capture_output=True,
        )
        return json.loads(completed.stdout)


    def test_cli_initializes_validates_and_resumes(tmp_path: Path) -> None:
        initialized = run_cli(tmp_path, "init")
        assert initialized["status"] == "continue"
        manifest = run_cli(tmp_path, "validate-manifest")
        assert manifest["coverage"]["breadth"]["tasks"] == 12
        advanced = run_cli(tmp_path, "advance")
        assert advanced["campaign_id"] == initialized["campaign_id"]
        assert advanced["iteration"] == initialized["iteration"] + 1
    ''',
)

write(
    "tests/test_runtime_hardening.py",
    r'''
    from __future__ import annotations

    import inspect
    import json
    from pathlib import Path

    import pytest

    from continual.contracts import (
        ComponentOutputError,
        candidate_index_for_target,
        validate_component_output,
    )
    from continual.openai_client import ModelClient
    from continual.security import (
        PathBoundaryError,
        resolve_repo_path,
        safe_subprocess_environment,
    )


    def test_component_output_requires_structured_result_and_fragment() -> None:
        with pytest.raises(ComponentOutputError):
            validate_component_output("execute", {"result": {}})
        value = validate_component_output(
            "execute", {"result": {"ok": True}, "fragment": {}, "local_learn": None}
        )
        assert value["result"] == {"ok": True}


    def test_candidate_index_is_filtered_before_evaluation() -> None:
        value = candidate_index_for_target(
            {
                "schema_version": 1,
                "candidates": [
                    {"id": "a", "target_component": "execute"},
                    {"id": "b", "target_component": "learn"},
                    {"id": "c", "target_component": "*"},
                ],
            },
            "execute",
        )
        assert [item["id"] for item in value["candidates"]] == ["a", "c"]


    def test_repository_path_boundaries(tmp_path: Path) -> None:
        with pytest.raises(PathBoundaryError):
            resolve_repo_path(tmp_path, "../outside", write=True)
        with pytest.raises(PathBoundaryError):
            resolve_repo_path(tmp_path, "prompts/execute.md", write=True)
        with pytest.raises(PathBoundaryError):
            resolve_repo_path(tmp_path, ".env", write=False)
        candidate = resolve_repo_path(
            tmp_path, ".continual/candidates/candidate.md", write=True
        )
        assert candidate.is_relative_to(tmp_path)
        prompt = resolve_repo_path(tmp_path, "prompts/execute.md", write=False)
        assert prompt.is_relative_to(tmp_path)


    def test_subprocess_environment_drops_secret_values() -> None:
        environment = safe_subprocess_environment(
            {
                "PATH": "/bin",
                "HOME": "/tmp/home",
                "OPENAI_API_KEY": "secret",
                "GITHUB_TOKEN": "secret",
                "DATABASE_PASSWORD": "secret",
                "UNRELATED": "not-needed",
            }
        )
        assert environment == {"PATH": "/bin", "HOME": "/tmp/home"}


    def test_candidate_prompt_overlays_active_base_contract(tmp_path: Path) -> None:
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts/execute.md").write_text("BASE CONTRACT\n", encoding="utf-8")
        candidate = tmp_path / ".continual/candidates/candidate.md"
        candidate.parent.mkdir(parents=True)
        candidate.write_text("CANDIDATE REFINEMENT\n", encoding="utf-8")
        client = object.__new__(ModelClient)
        client.root = tmp_path
        prompt = client.prompt("execute", ".continual/candidates/candidate.md")
        assert "BASE CONTRACT" in prompt
        assert "## Candidate overlay" in prompt
        assert "CANDIDATE REFINEMENT" in prompt
        assert prompt.index("BASE CONTRACT") < prompt.index("CANDIDATE REFINEMENT")


    def test_active_component_manifest_selects_base_prompt(tmp_path: Path) -> None:
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts/execute.md").write_text("DEFAULT\n", encoding="utf-8")
        active = tmp_path / "prompts/execute-v2.md"
        active.write_text("ACTIVE V2\n", encoding="utf-8")
        manifest = tmp_path / ".continual/system/active-components.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps({"active": {"execute": {"prompt_path": "prompts/execute-v2.md"}}}),
            encoding="utf-8",
        )
        client = object.__new__(ModelClient)
        client.root = tmp_path
        assert client.prompt("execute") == "ACTIVE V2\n"


    def test_model_subprocess_uses_scrubbed_environment() -> None:
        source = inspect.getsource(ModelClient)
        assert "env=safe_subprocess_environment()" in source


    def test_engine_persists_step_budget_pause() -> None:
        source = Path("src/continual/engine.py").read_text(encoding="utf-8")
        assert "step_budget_exhausted" in source
        assert 'raise RuntimeError(f"max_steps exceeded for {run_id}")' not in source
    ''',
)
