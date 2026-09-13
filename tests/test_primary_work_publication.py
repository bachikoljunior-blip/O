"""A publication must include the persisted primary Run's referenced records."""

import json
import subprocess
from pathlib import Path

from continual.work_checkpoint_integrity import verify_work_checkpoint_integrity
from continual.work_session import verify_work_invocations


ROOT = Path(__file__).resolve().parents[1]


def _primary_state():
    return json.loads((ROOT / "agi/WORK_EXECUTION_STATE.json").read_text())


def test_published_primary_native_records_are_complete():
    state = _primary_state()
    result = verify_work_invocations(ROOT, run_id=state["active_run_id"])
    assert result["valid"], result


def test_published_primary_checkpoint_references_exist():
    result = verify_work_checkpoint_integrity(ROOT, state=_primary_state())
    assert result["valid"], result["issues"]


def test_primary_preflight_cache_is_published_and_not_ignored():
    """A completed preflight must survive a clean-main resume."""

    run_id = _primary_state()["active_run_id"]
    preflight = (
        ROOT
        / ".continual"
        / "runs"
        / run_id
        / "preflight"
        / "preflight-execute-831454c00600ebbdd2a8ee71.json"
    )
    journal = (
        ROOT
        / ".continual"
        / "runs"
        / run_id
        / "invocations"
        / "invoke-cedfb9ba69caee1135e15359.json"
    )

    assert preflight.is_file(), "completed Candidate preflight cache was not published"
    assert json.loads(preflight.read_text()) == json.loads(journal.read_text())["output"]

    future_preflight = (
        ".continual/runs/run-publication-guard/preflight/"
        "preflight-execute-future.json"
    )
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", future_preflight],
        cwd=ROOT,
        check=False,
    )
    assert ignored.returncode == 1, "native preflight caches must remain visible to git"
