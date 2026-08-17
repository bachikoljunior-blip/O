from __future__ import annotations

import json
from pathlib import Path

import agi.capability_campaign_cli as cli


def test_stage_seed_derivation_is_deterministic_and_domain_separated() -> None:
    first = cli.derive_stage_seeds("github-run-12345")
    second = cli.derive_stage_seeds("github-run-12345")
    other = cli.derive_stage_seeds("github-run-12346")

    assert first == second
    assert first != other
    assert len(first) == 3
    assert len(set(first.values())) == 3
    assert all(len(seed) == 64 for seed in first.values())
    assert "github-run-12345" not in json.dumps(first, sort_keys=True)


def test_materialization_cli_never_persists_raw_seed_source(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    captured = {}

    def fake_campaign(root, campaign_id, *, seeds, max_new_stages=None):
        captured.update(
            root=root,
            campaign_id=campaign_id,
            seeds=seeds,
            max_new_stages=max_new_stages,
        )
        return {
            "passed": True,
            "campaign_id": campaign_id,
            "seed_commitments": {key: "a" * 64 for key in seeds},
        }

    monkeypatch.setattr(cli, "run_resumable_capability_campaign", fake_campaign)
    raw_source = "github-run-987654321"
    output = tmp_path / "report.json"
    code = cli.run_cli(
        [
            "--root",
            str(tmp_path),
            "--campaign-id",
            "materialized-01",
            "--seed-source",
            raw_source,
            "--output",
            str(output),
        ]
    )

    assert code == 0
    assert captured["campaign_id"] == "materialized-01"
    assert set(captured["seeds"]) == {
        "crossdomain_acquisition",
        "durable_transfer_rollback",
        "external_tool_engine",
    }
    assert raw_source not in output.read_text(encoding="utf-8")
    assert raw_source not in capsys.readouterr().out


def test_materialization_workflow_requires_review_branch_and_never_auto_merges() -> None:
    workflow = (
        Path(__file__).parents[1]
        / ".github"
        / "workflows"
        / "materialize-verified-capabilities.yml"
    ).read_text(encoding="utf-8")

    assert "agi/materialized-capabilities-*" in workflow
    assert "permissions:\n  contents: write\n  models: read" in workflow
    assert "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in workflow
    assert "github.actor != 'github-actions[bot]'" in workflow
    assert "Enforce repository mutation boundary" in workflow
    assert "auto_merge': False" in workflow
    assert "git push origin" in workflow
    assert "[skip ci]" not in workflow
    assert "gh pr merge" not in workflow
    assert "git push origin main" not in workflow
