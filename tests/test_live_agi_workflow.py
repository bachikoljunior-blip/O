from __future__ import annotations

from pathlib import Path


def test_live_workflow_has_bounded_openai_and_copilot_paths():
    text = Path(".github/workflows/live-agi-development.yml").read_text(encoding="utf-8")

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
    text = Path(".github/workflows/live-agi-development.yml").read_text(encoding="utf-8")
    token_line = "          GITHUB_TOKEN: ${{ github.token }}"
    assert text.count(token_line) == 1
    copilot_step = text.index("Run Copilot SDK repeated capability and retention campaign")
    token = text.index(token_line)
    upload = text.index("uses: actions/upload-artifact@v4")
    assert copilot_step < token < upload
