from __future__ import annotations

from pathlib import Path


def test_live_request_workflow_is_narrow_and_copilot_only():
    text = Path(".github/workflows/live-agi-request.yml").read_text(encoding="utf-8")

    assert "'agi/live-campaign-*'" in text
    assert "'agi/LIVE_CAMPAIGN_REQUEST.json'" in text
    assert "if: github.actor == github.repository_owner" in text
    assert "contents: read" in text
    assert "statuses: write" in text
    assert "copilot-requests: write" in text
    assert "contents: write" not in text
    assert "python -m pip install -e '.[test,copilot]'" in text
    assert "provider=copilot only" in text
    assert "1 <= runs <= 3" in text
    assert "1 <= instances <= 3" in text
    assert "agi-copilot run-campaign" in text
    assert "agi-copilot run-longhorizon" in text
    assert "GITHUB_TOKEN: ${{ github.token }}" in text
    assert "OPENAI_API_KEY" not in text
    assert "models: read" not in text
    assert "models.github.ai" not in text


def test_live_request_workflow_publishes_observable_request_commit_status():
    text = Path(".github/workflows/live-agi-request.yml").read_text(encoding="utf-8")

    assert "Publish pending request status" in text
    assert "python -m agi.live_request_status pending" in text
    assert "Publish final request status" in text
    assert 'python -m agi.live_request_status final --job-status "${JOB_STATUS}"' in text
    assert "JOB_STATUS: ${{ job.status }}" in text
    assert "if: always()" in text


def test_live_request_workflow_never_commits_model_outputs():
    text = Path(".github/workflows/live-agi-request.yml").read_text(encoding="utf-8")

    assert "git push" not in text
    assert "git commit" not in text
    assert "actions/upload-artifact@v4" in text
    assert "retention-days: 30" in text
