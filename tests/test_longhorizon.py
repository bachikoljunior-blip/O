from __future__ import annotations

import json

from agi.longhorizon import (
    LongHorizonAction,
    ReferenceLongHorizonAgent,
    run_long_horizon,
)


def test_reference_long_horizon_requires_restart_rollback_and_retention():
    creations = 0

    def factory():
        nonlocal creations
        creations += 1
        return ReferenceLongHorizonAgent()

    report = run_long_horizon(factory)
    assert report.passed is True
    assert report.injected_failures == 1
    assert report.restarts >= 1
    assert report.rollback_verified is True
    assert report.retention_verified is True
    assert report.protected_regression_verified is True
    assert creations >= 2
    assert "retain" in report.completed_phases
    assert "regression" in report.completed_phases


def test_checkpoint_persisted_and_reloaded(tmp_path):
    # persist checkpoint to disk and ensure recovery uses the durable checkpoint
    creations = 0

    def factory():
        nonlocal creations
        creations += 1
        return ReferenceLongHorizonAgent()

    ckpt = tmp_path / "checkpoint.json"
    report = run_long_horizon(factory, checkpoint_path=ckpt)
    assert report.passed is True
    # checkpoint file should exist and contain digest and checkpoint
    assert ckpt.exists()
    data = json.loads(ckpt.read_text(encoding="utf-8"))
    assert "digest" in data and "checkpoint" in data
    # ensure atomic writer removed temporary files
    tmpfile = ckpt.with_suffix(ckpt.suffix + ".tmp")
    assert not tmpfile.exists()
    # verify that at least two agent creations occurred (fresh context semantics)
    assert creations >= 2


def test_agent_that_refuses_rollback_cannot_pass():
    class RefuseRollback(ReferenceLongHorizonAgent):
        def act(self, observation):
            if observation.failure:
                return LongHorizonAction("finish")
            return super().act(observation)

    report = run_long_horizon(RefuseRollback, max_turns=10)
    assert report.passed is False
    assert report.injected_failures == 1
    assert report.rollback_verified is False


def test_short_external_budget_is_rejected():
    try:
        run_long_horizon(ReferenceLongHorizonAgent, max_turns=7)
    except ValueError as exc:
        assert "at least 8" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_corrupted_checkpoint_detected_and_fails(tmp_path):
    """If the durable checkpoint on disk is tampered with between processes, rollback
    verification must fail and the overall run must not pass the long-horizon checks.
    """
    creations = 0
    ckpt = tmp_path / "checkpoint.json"

    def factory():
        nonlocal creations
        creations += 1
        # On the second agent creation (fresh context after injected failure) simulate
        # an adversary or disk corruption by mutating the persisted checkpoint file.
        if creations == 2 and ckpt.exists():
            try:
                data = json.loads(ckpt.read_text(encoding="utf-8"))
                # Tamper durable memory inside the checkpoint so digest no longer matches.
                if isinstance(data.get("checkpoint"), dict):
                    data["checkpoint"].setdefault("durable_memory", {})["retained-rule"] = "MALICIOUS"
                    ckpt.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            except Exception:
                # If corruption attempt fails, let the run proceed; the harness should still detect mismatch.
                pass
        return ReferenceLongHorizonAgent()

    report = run_long_horizon(factory, checkpoint_path=ckpt)
    # The run must not pass when the durable checkpoint has been corrupted on disk.
    assert report.passed is False
    assert report.injected_failures == 1
    assert report.restarts >= 1
    assert report.rollback_verified is False


def test_longhorizon_multi_run_campaign(tmp_path):
    """Run a small multi-run campaign and assert aggregated retention/protection metrics."""
    from agi.longhorizon import run_long_horizon_campaign

    def factory():
        return ReferenceLongHorizonAgent()

    campaign = run_long_horizon_campaign(factory, runs=5, checkpoint_dir=tmp_path)
    assert campaign["total_runs"] == 5
    assert campaign["passed_count"] == 5
    assert campaign["passed_fraction"] == 1.0
    assert campaign["total_injected_failures"] == 5


def test_longhorizon_campaign_persists_summary(tmp_path):
    """Ensure aggregated campaign summaries can be persisted atomically for audit."""
    from agi.longhorizon import run_long_horizon_campaign, write_campaign_summary

    def factory():
        return ReferenceLongHorizonAgent()

    campaign = run_long_horizon_campaign(factory, runs=3, checkpoint_dir=tmp_path)
    outdir = tmp_path / "campaign-artifacts"
    write_campaign_summary(campaign, outdir, campaign_id="test-longhorizon")
    target = outdir / "test-longhorizon.json"
    assert target.exists()
    import json

    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["total_runs"] == 3
    assert "details" in data
    assert data["passed_fraction"] == 1.0
