from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from continual.work_session import WorkSession, WorkSessionError, submit_work_response


def _fixture(tmp_path: Path, data: bytes) -> tuple[WorkSession, dict, dict, Path]:
    shutil.copytree(Path("prompts"), tmp_path / "prompts")
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts/result.json").write_bytes(data)
    session = WorkSession(tmp_path, model_identity="work-model")
    started = session.start("verify an explicit canonical JSON claim")
    request = started["pending"][0]
    output = {
        "result": {"objective": "bounded evidence check", "evidence": {
            "path": "artifacts/result.json",
            "git_blob_sha": hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest(),
        }},
        "local_learn": {"decision": "NO_CHANGE", "candidates": []},
        "fragment": {"component": "entry", "observations": ["isolated fixture"]},
    }
    response = tmp_path / ".continual/work-model/invocations" / request["invocation_id"] / "response.json"
    return session, started, output, response


def _submit(tmp_path: Path, started: dict, output: dict) -> dict:
    return submit_work_response(tmp_path, started["pending"][0]["invocation_id"], output,
                                executor_binding="current_chatgpt_work_session", model_identity="work-model")


def test_wrong_canonical_digest_with_correct_git_blob_is_rejected_atomically(tmp_path: Path) -> None:
    session, started, output, response = _fixture(tmp_path, b'{"bounded":true}\n')
    output["result"]["evidence"]["sha256_canonical_json"] = "0" * 64
    snapshot = tmp_path / ".continual/runs" / started["run_id"] / "snapshot.json"
    before = snapshot.read_bytes()

    with pytest.raises(WorkSessionError, match="canonical JSON digest mismatch"):
        _submit(tmp_path, started, output)

    assert not response.exists()
    assert snapshot.read_bytes() == before


def test_correct_canonical_digest_accepts_formatting_unicode_and_exact_replay(tmp_path: Path) -> None:
    data = '{ "z": 1, "a": "日本語" }\n'.encode()
    session, started, output, response = _fixture(tmp_path, data)
    output["result"]["evidence"]["sha256_canonical_json"] = hashlib.sha256(
        '{"a":"日本語","z":1}'.encode("utf-8")
    ).hexdigest()
    first = _submit(tmp_path, started, output)
    before = response.read_bytes()

    assert _submit(tmp_path, started, output) == first
    assert response.read_bytes() == before
    assert session.resume(started["run_id"], max_steps=1)["snapshot"]["phase"] == "root_pending"


def test_canonical_digest_requires_same_object_blob_and_valid_json(tmp_path: Path) -> None:
    session, started, output, response = _fixture(tmp_path, b"not JSON\n")
    output["result"]["evidence"]["sha256_canonical_json"] = "0" * 64
    with pytest.raises(WorkSessionError, match="canonical JSON evidence is malformed"):
        _submit(tmp_path, started, output)
    output["result"]["evidence"].pop("git_blob_sha")
    with pytest.raises(WorkSessionError, match="requires a same-object path/blob binding"):
        _submit(tmp_path, started, output)
    assert not response.exists()


def test_canonical_submission_still_rejects_artifact_drift_before_native_consume(tmp_path: Path) -> None:
    session, started, output, response = _fixture(tmp_path, b'{"value":1}\n')
    output["result"]["evidence"]["sha256_canonical_json"] = hashlib.sha256(b'{"value":1}').hexdigest()
    _submit(tmp_path, started, output)
    before = response.read_bytes()
    (tmp_path / "artifacts/result.json").write_bytes(b'{"value":2}\n')

    with pytest.raises(WorkSessionError, match="evidence blob identity mismatch"):
        session.resume(started["run_id"], max_steps=1)

    assert response.read_bytes() == before


@pytest.mark.parametrize("claim", ["f" * 63, "A" * 64, 7])
def test_malformed_canonical_claim_rejects_without_response(tmp_path: Path, claim: object) -> None:
    session, started, output, response = _fixture(tmp_path, b'{"value":1}\n')
    output["result"]["evidence"]["sha256_canonical_json"] = claim
    with pytest.raises(WorkSessionError, match="canonical JSON digest is not full lowercase hex"):
        _submit(tmp_path, started, output)
    assert not response.exists()
