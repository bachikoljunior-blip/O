"""A publication must include the persisted primary Run's referenced records."""

import json
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
