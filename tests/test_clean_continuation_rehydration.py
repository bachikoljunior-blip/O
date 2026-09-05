from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from agi.clean_continuation_rehydration import (
    RehydrationError,
    admit_replay,
    git_blob_sha,
    run_rehydration,
    validate_projection,
)
from continual.work_session import WorkSession, submit_work_response


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _entry_output() -> dict:
    return {
        "result": {"objective": "exercise a clean continuation fixture"},
        "local_learn": {"decision": "NO_CHANGE", "candidates": []},
        "fragment": {"component": "entry", "observations": ["bounded fixture"]},
    }


def _fixture(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / "projection"
    root.mkdir(parents=True)
    shutil.copytree(Path("prompts"), root / "prompts")
    run_id = "run-clean-rehydration-fixture"
    session = WorkSession(root, executor_binding="fixture-binding", model_identity="fixture-model")
    started = session.start("create exact root continuation", run_id=run_id)
    entry = started["pending"][0]
    submit_work_response(
        root,
        entry["invocation_id"],
        _entry_output(),
        executor_binding="fixture-binding",
        model_identity="fixture-model",
    )
    resumed = session.resume(run_id, max_steps=2)
    pending = resumed["pending"][0]
    snapshot = resumed["snapshot"]
    native_path = next(
        path
        for path in (root / ".continual" / "runs" / run_id / "invocations").glob("*.json")
        if json.loads(path.read_text())["status"] == "awaiting_work_model"
    )
    native = json.loads(native_path.read_text())
    fence = "fixture-fence-v1"
    fence_digest = hashlib.sha256(fence.encode()).hexdigest()
    request_ref = f'.continual/work-model/invocations/{pending["invocation_id"]}/request.json'
    native_ref = native_path.relative_to(root).as_posix()
    snapshot_ref = f".continual/runs/{run_id}/snapshot.json"
    state = {
        "execution_id": "fixture-execution",
        "lease_generation": 29,
        "fence_token": fence,
        "active_run_id": run_id,
        "primary_native_run": {
            "run_id": run_id,
            "executor_binding": "fixture-binding",
            "model_identity": "fixture-model",
            "answered_invocations": [f'{entry["invocation_id"]} (Entry)'],
        },
        "exact_continuation": {
            "status": "remote_main_readback_verified",
            "lease_generation": 29,
            "fence_token_digest": fence_digest,
            "run_snapshot_ref": snapshot_ref,
            "snapshot_branch": "main",
            "snapshot_head_sha": "a" * 40,
            "snapshot_revision": snapshot["revision"],
            "snapshot_phase": "root_pending",
            "pending_work_invocation_id": pending["invocation_id"],
            "pending_native_invocation_id": native["invocation_id"],
            "pending_request_ref": request_ref,
            "pending_native_invocation_ref": native_ref,
            "pending_request_digest": pending["request_digest"],
        },
    }
    _write_json(root / "agi" / "WORK_EXECUTION_STATE.json", state)
    paths = [Path("agi/WORK_EXECUTION_STATE.json"), Path(request_ref), Path(native_ref), Path(snapshot_ref)]
    declaration = {
        "source_commit_sha": "b" * 40,
        "source_tree_sha": "c" * 40,
        "execution_id": "fixture-execution",
        "lease_generation": 29,
        "fence_token": fence,
        "fence_token_digest": fence_digest,
        "run_id": run_id,
        "pending_work_invocation_id": pending["invocation_id"],
        "pending_native_invocation_id": native["invocation_id"],
        "pending_request_digest": pending["request_digest"],
        "blobs": {path.as_posix(): git_blob_sha((root / path).read_bytes()) for path in paths},
    }
    return root, declaration


def _refresh_blob(root: Path, declaration: dict, rel: str) -> None:
    declaration["blobs"][rel] = git_blob_sha((root / rel).read_bytes())


def test_projection_passes_without_mutation_and_replay_is_suppressed(tmp_path: Path) -> None:
    root, declaration = _fixture(tmp_path)
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    report = validate_projection(root, declaration)
    assert report["status"] == "PASS"
    assert report["pending_count"] == 1
    assert report["semantic_provider_calls"] == 0
    assert report["mutation_count"] == 0
    assert {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    admitted: set[str] = set()
    assert admit_replay(report, admitted)["accepted"] is True
    duplicate = admit_replay(report, admitted)
    assert duplicate == {
        "idempotency_key": next(iter(admitted)),
        "accepted": False,
        "suppressed_duplicate": True,
    }


def test_declared_blob_missing_or_changed_fails_closed(tmp_path: Path) -> None:
    root, declaration = _fixture(tmp_path)
    request = root / next(
        Path(path) for path in declaration["blobs"] if path.endswith("/request.json")
    )
    request.unlink()
    with pytest.raises(RehydrationError, match="declared blob is missing"):
        validate_projection(root, declaration)

    root, declaration = _fixture(tmp_path / "changed")
    request = root / next(
        Path(path) for path in declaration["blobs"] if path.endswith("/request.json")
    )
    request.write_bytes(request.read_bytes() + b" ")
    with pytest.raises(RehydrationError, match="declared blob mismatch"):
        validate_projection(root, declaration)


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("request_digest", "pending request digest mismatch"),
        ("missing_native", "declared blob is missing"),
        ("snapshot_revision", "snapshot revision mismatch"),
        ("continuation_status", "continuation is not remote-main verified"),
        ("generation", "state generation mismatch"),
        ("fence", "state fence mismatch"),
    ],
)
def test_binding_corruptions_fail_closed(tmp_path: Path, case: str, message: str) -> None:
    root, declaration = _fixture(tmp_path)
    state_rel = "agi/WORK_EXECUTION_STATE.json"
    state = json.loads((root / state_rel).read_text())
    if case == "request_digest":
        state["exact_continuation"]["pending_request_digest"] = "d" * 64
        _write_json(root / state_rel, state)
        _refresh_blob(root, declaration, state_rel)
    elif case == "missing_native":
        (root / state["exact_continuation"]["pending_native_invocation_ref"]).unlink()
    elif case == "snapshot_revision":
        snapshot_rel = state["exact_continuation"]["run_snapshot_ref"]
        snapshot = json.loads((root / snapshot_rel).read_text())
        snapshot["revision"] += 1
        _write_json(root / snapshot_rel, snapshot)
        _refresh_blob(root, declaration, snapshot_rel)
    elif case == "continuation_status":
        state["exact_continuation"]["status"] = "local_only"
        _write_json(root / state_rel, state)
        _refresh_blob(root, declaration, state_rel)
    elif case == "generation":
        state["lease_generation"] = 30
        _write_json(root / state_rel, state)
        _refresh_blob(root, declaration, state_rel)
    elif case == "fence":
        state["fence_token"] = "wrong-fence"
        _write_json(root / state_rel, state)
        _refresh_blob(root, declaration, state_rel)
    with pytest.raises(RehydrationError, match=message):
        validate_projection(root, declaration)


def test_declaration_identity_corruptions_fail_closed(tmp_path: Path) -> None:
    root, declaration = _fixture(tmp_path)
    stale = deepcopy(declaration)
    stale["blobs"]["agi/WORK_EXECUTION_STATE.json"] = "e" * 40
    with pytest.raises(RehydrationError, match="declared blob mismatch"):
        validate_projection(root, stale)
    wrong_fence = deepcopy(declaration)
    wrong_fence["fence_token_digest"] = "f" * 64
    with pytest.raises(RehydrationError, match="declared fence digest mismatch"):
        validate_projection(root, wrong_fence)


def test_immutable_historical_remote_main_integration() -> None:
    declaration = json.loads(
        Path("artifacts/unit-generation29-clean-remote-main-continuation-rehydration-v1-precommit.json").read_text()
    )
    try:
        actual_tree = subprocess.check_output(
            ["git", "rev-parse", f'{declaration["source_commit_sha"]}^{{tree}}'],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        pytest.skip("immutable subject commit is not present in this shallow/projection checkout")
    if actual_tree != declaration["source_tree_sha"]:
        pytest.skip("local synthetic projection does not contain the immutable remote tree")
    report = run_rehydration(Path.cwd(), declaration)
    assert report["status"] == "PASS"
    assert report["source_repository_mutation_count"] == 0
