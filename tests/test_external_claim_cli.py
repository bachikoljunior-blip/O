from __future__ import annotations

import json

from agi.external_claim_cli import run_cli


def _write_json(path, value) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_missing_bridge_secret_fails_closed_without_reading_evidence(tmp_path, capsys) -> None:
    ledger = tmp_path / "ledger.json"
    registry = tmp_path / "registry.json"
    _write_json(ledger, {"schema_version": 1})
    _write_json(registry, {"schema_version": 1})

    code = run_cli(
        [
            "--ledger",
            str(ledger),
            "--registry",
            str(registry),
            "--bridge-attestor-id",
            "bridge-a",
        ],
        environ={},
    )

    output = json.loads(capsys.readouterr().out)
    assert code == 2
    assert output["agi_claim_supported"] is False
    assert "AGI_EVIDENCE_BRIDGE_SECRET" in output["error"]


def test_dirty_external_ledger_fails_closed_and_never_prints_bridge_secret(tmp_path, capsys) -> None:
    ledger = tmp_path / "ledger.json"
    registry = tmp_path / "registry.json"
    _write_json(
        ledger,
        {
            "schema_version": 1,
            "challenges": [],
            "disclosures": [],
            "attestations": [],
        },
    )
    _write_json(registry, {"schema_version": 1, "verifiers": []})
    secret = "do-not-leak-this-bridge-secret"

    code = run_cli(
        [
            "--ledger",
            str(ledger),
            "--registry",
            str(registry),
            "--bridge-attestor-id",
            "bridge-a",
        ],
        environ={"AGI_EVIDENCE_BRIDGE_SECRET": secret},
    )

    raw = capsys.readouterr().out
    output = json.loads(raw)
    assert code == 2
    assert output["agi_claim_supported"] is False
    assert secret not in raw


def test_malformed_json_is_conservative_and_can_write_report(tmp_path, capsys) -> None:
    ledger = tmp_path / "ledger.json"
    registry = tmp_path / "registry.json"
    report_path = tmp_path / "result" / "report.json"
    ledger.write_text("{ definitely-not-json", encoding="utf-8")
    _write_json(registry, {"schema_version": 1, "verifiers": []})

    code = run_cli(
        [
            "--ledger",
            str(ledger),
            "--registry",
            str(registry),
            "--bridge-attestor-id",
            "bridge-a",
            "--output",
            str(report_path),
        ],
        environ={"AGI_EVIDENCE_BRIDGE_SECRET": "secret"},
    )

    printed = json.loads(capsys.readouterr().out)
    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert code == 2
    assert printed == persisted
    assert printed["agi_claim_supported"] is False
    assert "error" in printed


def test_strict_cli_rejects_quorum_below_repository_default_before_reading_evidence(tmp_path, capsys) -> None:
    ledger = tmp_path / "missing-ledger.json"
    registry = tmp_path / "missing-registry.json"

    code = run_cli(
        [
            "--ledger",
            str(ledger),
            "--registry",
            str(registry),
            "--bridge-attestor-id",
            "bridge-a",
            "--min-independent-groups-per-criterion",
            "1",
        ],
        environ={"AGI_EVIDENCE_BRIDGE_SECRET": "secret"},
    )

    output = json.loads(capsys.readouterr().out)
    assert code == 2
    assert output["agi_claim_supported"] is False
    assert "cannot be lowered below 2" in output["error"]
