from __future__ import annotations

from pathlib import Path


def _workflow() -> str:
    return Path(".github/workflows/live-agi-development.yml").read_text(encoding="utf-8")


def test_live_workflow_has_bounded_openai_and_copilot_paths():
    text = _workflow()

    assert "copilot-requests: write" in text
    assert "provider:" in text
    assert "- openai" in text
    assert "- copilot" in text
    assert "github-copilot-sdk>=1.0.8,<2" in text
    assert "GITHUB_TOKEN: ${{ github.token }}" in text
    assert "agi-copilot run-campaign" in text
    assert "agi-copilot run-longhorizon" in text
    assert "agi-benchmark run-campaign-openai" in text
    assert "agi-longhorizon run-openai" in text
    assert "1|2|3|4|5" in text
    assert "models: read" not in text
    assert "models.github.ai" not in text


def test_copilot_token_is_scoped_to_copilot_execution_step():
    text = _workflow()
    token_line = "          GITHUB_TOKEN: ${{ github.token }}"
    assert text.count(token_line) == 1
    copilot_step = text.index("Run Copilot SDK repeated capability and retention campaign")
    token = text.index(token_line)
    upload = text.index("uses: actions/upload-artifact@v4")
    assert copilot_step < token < upload


def test_openai_secret_is_not_job_wide_and_is_only_used_for_resolution_and_openai_execution():
    text = _workflow()
    secret_line = "          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}"
    assert text.count(secret_line) == 2
    job_env = text.index("    env:\n      OPENAI_MODEL:")
    resolve_step = text.index("Resolve bounded live provider")
    openai_step = text.index("Run OpenAI repeated capability campaign")
    copilot_step = text.index("Run Copilot SDK repeated capability and retention campaign")
    first_secret = text.index(secret_line)
    second_secret = text.index(secret_line, first_secret + 1)
    assert job_env < resolve_step < first_secret < openai_step < second_secret < copilot_step


def test_each_live_provider_attempts_capability_and_longhorizon_before_aggregating_failure():
    text = _workflow()
    cases = [
        (
            "- name: Run OpenAI repeated capability campaign",
            "- name: Run Copilot SDK repeated capability and retention campaign",
            "agi-benchmark run-campaign-openai",
            "agi-longhorizon run-openai",
        ),
        (
            "- name: Run Copilot SDK repeated capability and retention campaign",
            "- uses: actions/upload-artifact@v4",
            "agi-copilot run-campaign",
            "agi-copilot run-longhorizon",
        ),
    ]
    for start_marker, end_marker, campaign_command, longhorizon_command in cases:
        start = text.index(start_marker)
        end = text.index(end_marker, start)
        step = text[start:end]
        campaign = step.index(campaign_command)
        longhorizon = step.index(longhorizon_command)
        status = step.index("request-stage-status.json")
        aggregate = step.index("if (( campaign_status != 0 || longhorizon_status != 0 ))")
        assert campaign < longhorizon < status < aggregate
        assert "set -uo pipefail" in step
        assert "campaign_status=0" in step
        assert "|| campaign_status=$?" in step
        assert "longhorizon_status=0" in step
        assert "|| longhorizon_status=$?" in step
        assert 'mkdir -p "${output_dir}"' in step

    assert text.count("request-stage-status.json") == 2
