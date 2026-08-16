from __future__ import annotations

import hashlib
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
    creations = 0

    def factory():
        nonlocal creations
        creations += 1
        return ReferenceLongHorizonAgent()

    ckpt = tmp_path / "checkpoint.json"
    observed_before_reload = False

    def inspect_before_reload(path):
        nonlocal observed_before_reload
        observed_before_reload = True
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["checkpoint"]["phase"] == "checkpoint"
        assert raw["checkpoint"]["workspace"]["protected.txt"] == "BASELINE-PROTECTED"

    report = run_long_horizon(
        factory,
        checkpoint_path=ckpt,
        before_checkpoint_reload=inspect_before_reload,
    )
    assert report.passed is True
    assert ckpt.exists()
    data = ckpt.read_text(encoding="utf-8")
    assert "digest" in data and "checkpoint" in data
    assert observed_before_reload is True
    assert creations >= 2


def test_payload_and_self_digest_rewrite_cannot_redefine_trusted_checkpoint(tmp_path):
    ckpt = tmp_path / "checkpoint.json"

    def rewrite_checkpoint(path):
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["checkpoint"]["durable_memory"]["retained-rule"] = "ATTACKER-REWRITE"
        encoded = json.dumps(
            raw["checkpoint"],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode()
        raw["digest"] = hashlib.sha256(encoded).hexdigest()
        path.write_text(json.dumps(raw, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    report = run_long_horizon(
        ReferenceLongHorizonAgent,
        checkpoint_path=ckpt,
        before_checkpoint_reload=rewrite_checkpoint,
        max_turns=12,
    )
    assert report.passed is False
    assert report.injected_failures == 1
    assert report.rollback_verified is False
    assert report.retention_verified is False


def test_deleted_durable_checkpoint_fails_closed_instead_of_crashing(tmp_path):
    ckpt = tmp_path / "checkpoint.json"

    def delete_checkpoint(path):
        assert path.exists()
        path.unlink()

    report = run_long_horizon(
        ReferenceLongHorizonAgent,
        checkpoint_path=ckpt,
        before_checkpoint_reload=delete_checkpoint,
        max_turns=12,
    )
    assert report.passed is False
    assert report.injected_failures == 1
    assert report.rollback_verified is False


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
