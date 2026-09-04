from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from continual.work_session import (
    WorkSession,
    WorkSessionError,
    submit_work_response,
    verify_declared_repository_blob_bindings,
)


def _root(tmp_path: Path) -> Path:
    shutil.copytree(Path("prompts"), tmp_path / "prompts")
    (tmp_path / "artifacts").mkdir()
    return tmp_path


def _blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content).hexdigest()


def _entry_output(path: str, sha: object) -> dict:
    return {
        "result": {
            "objective": "exercise exact repository evidence binding",
            "evidence": {"path": path, "git_blob_sha": sha},
        },
        "local_learn": {"decision": "NO_CHANGE", "candidates": []},
        "fragment": {"component": "entry", "observations": ["bounded test"]},
    }


def _submit(root: Path, invocation_id: str, output: dict) -> dict:
    return submit_work_response(
        root,
        invocation_id,
        output,
        executor_binding="current_chatgpt_work_session",
        model_identity="work-model",
    )


def test_submit_and_native_consume_verify_exact_repository_bytes(tmp_path: Path) -> None:
    root = _root(tmp_path)
    content = b'{"bounded":true}\n'
    artifact = root / "artifacts" / "result.json"
    artifact.write_bytes(content)
    sha = _blob_sha(content)
    session = WorkSession(root, model_identity="work-model")
    started = session.start("verify one declared evidence binding")
    request = started["pending"][0]

    _submit(root, request["invocation_id"], _entry_output("artifacts/result.json", sha))
    resumed = session.resume(started["run_id"], max_steps=1)

    assert resumed["snapshot"]["phase"] == "root_pending"
    response = (
        root
        / ".continual"
        / "work-model"
        / "invocations"
        / request["invocation_id"]
        / "response.json"
    )
    assert response.is_file()


@pytest.mark.parametrize(
    ("path", "sha", "match"),
    [
        ("artifacts/result.json", "0" * 39, "full lowercase hex"),
        ("artifacts/result.json", "A" * 40, "full lowercase hex"),
        ("artifacts/result.json", 7, "full lowercase hex"),
        ("artifacts/result.json", "0" * 40, "identity mismatch"),
        ("artifacts/missing.json", "0" * 40, "missing or escapes"),
        ("../outside.json", "0" * 40, "normalized and relative"),
        ("artifacts//result.json", "0" * 40, "normalized and relative"),
    ],
)
def test_submit_rejects_malformed_stale_missing_and_escaping_bindings_without_response(
    tmp_path: Path, path: str, sha: object, match: str
) -> None:
    root = _root(tmp_path)
    (root / "artifacts" / "result.json").write_text("result\n", encoding="utf-8")
    session = WorkSession(root, model_identity="work-model")
    request = session.start("reject invalid evidence")["pending"][0]
    response = (
        root
        / ".continual"
        / "work-model"
        / "invocations"
        / request["invocation_id"]
        / "response.json"
    )

    with pytest.raises(WorkSessionError, match=match):
        _submit(root, request["invocation_id"], _entry_output(path, sha))

    assert not response.exists()


def test_symlink_and_conflicting_duplicate_bindings_fail_closed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    content = b"exact\n"
    target = root / "artifacts" / "target.json"
    target.write_bytes(content)
    (root / "artifacts" / "alias.json").symlink_to(target)
    sha = _blob_sha(content)

    with pytest.raises(WorkSessionError, match="traverses a symlink"):
        verify_declared_repository_blob_bindings(
            root,
            {"evidence": {"path": "artifacts/alias.json", "blob_sha": sha}},
        )

    with pytest.raises(WorkSessionError, match="conflicting evidence bindings"):
        verify_declared_repository_blob_bindings(
            root,
            {
                "evidence": [
                    {"path": "artifacts/target.json", "git_blob_sha": sha},
                    {"path": "artifacts/target.json", "git_blob_sha": "0" * 40},
                ]
            },
        )


def test_regular_file_requirement_and_identical_duplicate_replay(tmp_path: Path) -> None:
    root = _root(tmp_path)
    content = b"stable\n"
    artifact = root / "artifacts" / "stable.json"
    artifact.write_bytes(content)
    sha = _blob_sha(content)

    declaration = {
        "evidence": [
            {"path": "artifacts/stable.json", "git_blob_sha": sha},
            {"path": "artifacts/stable.json", "git_blob_sha": sha},
        ]
    }
    first = verify_declared_repository_blob_bindings(root, declaration)
    second = verify_declared_repository_blob_bindings(root, declaration)

    assert first == second
    assert len(first) == 2
    with pytest.raises(WorkSessionError, match="not a regular file"):
        verify_declared_repository_blob_bindings(
            root,
            {"evidence": {"path": "artifacts", "git_blob_sha": sha}},
        )


def test_named_git_blob_field_requires_a_same_object_path_and_prose_is_ignored(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    with pytest.raises(WorkSessionError, match="missing its named path"):
        verify_declared_repository_blob_bindings(
            root, {"validation": {"result_git_blob_sha": "0" * 40}}
        )

    assert (
        verify_declared_repository_blob_bindings(
            root,
            {
                "observation": (
                    "A prose-only artifacts/result.json value " + "0" * 40
                )
            },
        )
        == []
    )


def test_declared_evidence_walk_is_bounded_before_file_access(tmp_path: Path) -> None:
    root = _root(tmp_path)
    too_many = [
        {"path": f"artifacts/missing-{index}.json", "git_blob_sha": "0" * 40}
        for index in range(257)
    ]
    with pytest.raises(WorkSessionError, match="bounded binding limit"):
        verify_declared_repository_blob_bindings(root, {"evidence": too_many})

    nested: dict = {"value": "leaf"}
    for _ in range(34):
        nested = {"next": nested}
    with pytest.raises(WorkSessionError, match="bounded depth limit"):
        verify_declared_repository_blob_bindings(root, nested)


def test_native_consume_rechecks_bytes_after_immutable_submission(tmp_path: Path) -> None:
    root = _root(tmp_path)
    original = b"before\n"
    artifact = root / "artifacts" / "result.json"
    artifact.write_bytes(original)
    session = WorkSession(root, model_identity="work-model")
    started = session.start("detect post-submission evidence drift")
    request = started["pending"][0]
    _submit(
        root,
        request["invocation_id"],
        _entry_output("artifacts/result.json", _blob_sha(original)),
    )
    artifact.write_bytes(b"after\n")
    journal = next(
        (root / ".continual" / "runs" / started["run_id"] / "invocations").glob(
            "*.json"
        )
    )
    before = journal.read_bytes()

    with pytest.raises(WorkSessionError, match="identity mismatch"):
        session.resume(started["run_id"], max_steps=1)

    assert journal.read_bytes() == before


def test_real_correction_is_negative_evidence_without_mutating_accepted_response() -> None:
    root = Path(__file__).resolve().parents[1]
    response_path = (
        root
        / ".continual/work-model/invocations/invoke-1fad3959c1466207fb6876ad/response.json"
    )
    correction_path = (
        root
        / "artifacts/unit-generation29-external-arc-nonzero-bounding-box-transfer-v1-response-correction.json"
    )
    before = response_path.read_bytes()
    correction = json.loads(correction_path.read_text(encoding="utf-8"))
    correct = {
        "evidence": {
            "path": correction["corrected_evidence_ref"],
            "git_blob_sha": correction["correct_value"],
        }
    }
    wrong = {
        "evidence": {
            "path": correction["corrected_evidence_ref"],
            "git_blob_sha": correction["incorrect_value_in_immutable_response"],
        }
    }

    accepted = json.loads(before)
    with pytest.raises(WorkSessionError, match="missing its named path"):
        verify_declared_repository_blob_bindings(root, accepted["output"])
    assert verify_declared_repository_blob_bindings(root, correct)[0][
        "git_blob_sha"
    ] == correction["correct_value"]
    with pytest.raises(WorkSessionError, match="identity mismatch"):
        verify_declared_repository_blob_bindings(root, wrong)

    assert response_path.read_bytes() == before
    assert correction["response_mutated"] is False
    assert correction["response_resubmitted"] is False
