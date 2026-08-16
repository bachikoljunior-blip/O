from __future__ import annotations

import json

import pytest

from agi.execution_learning import EXECUTION_SCOPE, run_learned_tool_execution_campaign
from continual.learned_tools import LearnedToolError, LearnedToolRegistry, learned_tool_candidate_payload


def _write_json(path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_unverified_learned_tool_is_not_callable(tmp_path) -> None:
    candidate_id = "learned-tool-test"
    candidate = learned_tool_candidate_payload(
        candidate_id=candidate_id,
        tool_id="tool-test",
        scope="scope/a",
        domain="string",
        expanded_primitives=("reverse", "append_hash"),
        support_sha256="a" * 64,
        description="test learned transform",
    )
    _write_json(
        tmp_path / ".continual" / "candidates" / candidate_id / "candidate.json",
        candidate,
    )

    registry = LearnedToolRegistry(tmp_path)
    assert registry.descriptors("scope/a") == ()
    with pytest.raises(LearnedToolError, match="not verified"):
        registry.apply(scope="scope/a", tool_id="tool-test", value="abc")


def test_execution_campaign_requires_learning_then_regression_before_tool_use(tmp_path) -> None:
    report = run_learned_tool_execution_campaign(tmp_path, "execution-seed")

    assert report["passed"] is True
    assert report["pre_learning_solved"] is False
    assert report["macro_reused"] is True
    assert report["inactive_before_regression"] is True
    assert report["promotion"]["decision"]["adopt_candidate"] is True
    assert report["promotion"]["candidate_status"] == "active-for-scope"
    assert len(report["post_activation_results"]) == 2
    assert all(item["success"] for item in report["post_activation_results"])
    assert report["evidence_tier"] == "development"
    assert "does not" in report["claim_boundary"]

    registry = LearnedToolRegistry(tmp_path)
    descriptors = registry.descriptors(EXECUTION_SCOPE)
    assert len(descriptors) == 1
    assert descriptors[0]["tool_id"] == report["tool_id"]
    with pytest.raises(LearnedToolError, match="not verified"):
        registry.apply(scope="other/scope", tool_id=report["tool_id"], value="abc")


def test_tampered_learned_program_fails_closed_after_promotion(tmp_path) -> None:
    report = run_learned_tool_execution_campaign(tmp_path, "tamper-seed")
    candidate_path = (
        tmp_path
        / ".continual"
        / "candidates"
        / report["candidate_id"]
        / "candidate.json"
    )
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate["learned_tool"]["expanded_primitives"] = ["unknown-primitive"]
    _write_json(candidate_path, candidate)

    registry = LearnedToolRegistry(tmp_path)
    with pytest.raises(LearnedToolError, match="unknown primitive"):
        registry.available(EXECUTION_SCOPE)


def test_invalid_regression_evidence_digest_does_not_activate_tool(tmp_path) -> None:
    candidate_id = "learned-tool-bad-proof"
    candidate = learned_tool_candidate_payload(
        candidate_id=candidate_id,
        tool_id="tool-bad-proof",
        scope="scope/proof",
        domain="string",
        expanded_primitives=("reverse",),
        support_sha256="b" * 64,
        description="proof validation test",
    )
    candidate["status"] = "active-for-scope"
    candidate["scope_states"] = {"scope/proof": "VERIFIED_FOR_SCOPE"}
    candidate["verified_scope_states"] = {
        "scope/proof": {
            "state": "VERIFIED_FOR_SCOPE",
            "regression_evidence_sha256": "not-a-digest",
            "decision_ref": "fake.json",
        }
    }
    _write_json(
        tmp_path / ".continual" / "candidates" / candidate_id / "candidate.json",
        candidate,
    )

    registry = LearnedToolRegistry(tmp_path)
    assert registry.descriptors("scope/proof") == ()
