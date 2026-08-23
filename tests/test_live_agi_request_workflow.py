from __future__ import annotations

from pathlib import Path


def _workflow() -> str:
    return Path(".github/workflows/live-agi-request.yml").read_text(encoding="utf-8")


def test_live_request_workflow_is_narrow_and_supports_bounded_provider_fallback():
    text = _workflow()

    assert "'agi/live-campaign-*'" in text
    assert "'agi/LIVE_CAMPAIGN_REQUEST.json'" in text
    assert "if: github.actor == github.repository_owner" in text
    assert "contents: read" in text
    assert "statuses: write" in text
    assert "copilot-requests: write" in text
    assert "contents: write" not in text
    assert "python -m pip install -e '.[test]'" in text
    assert "provider must be one of: auto, openai, copilot" in text
    assert "provider=openai requires OPENAI_API_KEY repository secret." in text
    assert "1 <= runs <= 3" in text
    assert "from agi.sandbox_protocol import MIN_REPEATED_INSTANCES" in text
    assert "MIN_REPEATED_INSTANCES <= instances <= 3" in text
    assert "1 <= instances <= 3" not in text
    assert "if: steps.provider.outputs.provider == 'openai'" in text
    assert "if: steps.provider.outputs.provider == 'copilot'" in text
    assert "agi-benchmark run-campaign-openai" in text
    assert "agi-longhorizon run-openai" in text
    assert "agi-copilot run-campaign" in text
    assert "agi-copilot run-longhorizon" in text
    assert "GITHUB_TOKEN: ${{ github.token }}" in text
    assert "OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}" in text
    assert "models: read" not in text
    assert "models.github.ai" not in text


def test_live_request_workflow_resolves_auto_without_leaking_secret_material():
    text = _workflow()

    assert "REQUESTED_PROVIDER: ${{ steps.request.outputs.provider }}" in text
    assert 'if [[ -n "${OPENAI_API_KEY:-}" ]]; then' in text
    assert "resolved=openai" in text
    assert "resolved=copilot" in text
    assert 'echo "provider=${resolved}" >> "${GITHUB_OUTPUT}"' in text
    assert 'echo "${OPENAI_API_KEY}' not in text
    assert "print(OPENAI_API_KEY" not in text


def test_live_request_workflow_publishes_observable_request_commit_status():
    text = _workflow()

    assert "Publish pending request status" in text
    assert "python -m agi.live_request_status pending" in text
    assert "Publish final request status" in text
    assert 'python -m agi.live_request_status final --job-status "${JOB_STATUS}"' in text
    assert "JOB_STATUS: ${{ job.status }}" in text
    assert "if: always()" in text


def _assert_aggregated_request_step(step: str, campaign_command: str, longhorizon_command: str) -> None:
    campaign = step.index(campaign_command)
    longhorizon = step.index(longhorizon_command)
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


def test_live_request_attempts_openai_capability_and_longhorizon_before_aggregating_failure():
    text = _workflow()
    start = text.index("- name: Run bounded OpenAI capability and retention request")
    end = text.index("- name: Run bounded Copilot capability and retention request", start)
    step = text[start:end]

    _assert_aggregated_request_step(
        step,
        "agi-benchmark run-campaign-openai",
        "agi-longhorizon run-openai",
    )


def test_live_request_attempts_copilot_capability_and_longhorizon_before_aggregating_failure():
    text = _workflow()
    start = text.index("- name: Run bounded Copilot capability and retention request")
    end = text.index("- uses: actions/upload-artifact@v4", start)
    step = text[start:end]

    _assert_aggregated_request_step(
        step,
        "agi-copilot run-campaign",
        "agi-copilot run-longhorizon",
    )


def test_live_request_workflow_never_commits_model_outputs():
    text = _workflow()

    assert "git push" not in text
    assert "git commit" not in text
    assert "actions/upload-artifact@v4" in text
    assert "retention-days: 30" in text
