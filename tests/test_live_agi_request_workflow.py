from __future__ import annotations

from pathlib import Path


def _workflow() -> str:
    return Path(".github/workflows/live-agi-request.yml").read_text(encoding="utf-8")


def test_live_request_workflow_is_narrow_and_copilot_only():
    text = _workflow()

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
    text = _workflow()

    assert "Publish pending request status" in text
    assert "python -m agi.live_request_status pending" in text
    assert "Publish final request status" in text
    assert 'python -m agi.live_request_status final --job-status "${JOB_STATUS}"' in text
    assert "JOB_STATUS: ${{ job.status }}" in text
    assert "if: always()" in text


def test_live_request_attempts_capability_and_longhorizon_before_aggregating_failure():
    text = _workflow()
    start = text.index("- name: Run bounded Copilot capability and retention request")
    end = text.index("- uses: actions/upload-artifact@v4", start)
    step = text[start:end]

    campaign = step.index("agi-copilot run-campaign")
    longhorizon = step.index("agi-copilot run-longhorizon")
    status = step.index("request-stage-status.json")
    aggregate = step.index("if (( campaign_status != 0 || longhorizon_status != 0 ))")
    assert campaign < longhorizon < status < aggregate
    assert "set -euo pipefail" in step
    assert "set +e" in step
    assert "set -e" in step
    assert "campaign_status=0" in step
    assert "campaign_status=$?" in step
    assert "longhorizon_status=0" in step
    assert "longhorizon_status=$?" in step
    assert 'mkdir -p "${output_dir}"' in step
    assert "|| campaign_status=$?" not in step
    assert "|| longhorizon_status=$?" not in step


def test_live_request_workflow_never_commits_model_outputs():
    text = _workflow()

    assert "git push" not in text
    assert "git commit" not in text
    assert "actions/upload-artifact@v4" in text
    assert "retention-days: 30" in text
