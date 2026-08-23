from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agi.context_skill_catalog import RepositorySkillCatalog
from agi.context_skill_router import ContextRoutingError, RecursiveContextRouter


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_manifest(
    root: Path,
    skills: list[dict[str, object]],
    *,
    name: str = "skill-catalog.json",
) -> Path:
    path = root / name
    path.write_text(
        json.dumps(
            {"schema_version": 1, "skills": skills},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _entry(
    skill_id: str,
    content: bytes,
    *children: str,
    path: str | None = None,
) -> dict[str, object]:
    return {
        "skill_id": skill_id,
        "content_path": path or f"skills/{skill_id}.md",
        "content_sha256": _digest(content),
        "children": [
            {"skill_id": child, "summary": f"summary:{child}"}
            for child in children
        ],
    }


def _write_content(root: Path, skill_id: str, content: bytes) -> Path:
    path = root / "skills" / f"{skill_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_catalog_routes_real_files_and_reads_only_selected_content(tmp_path: Path) -> None:
    contents = {
        "root": b"root context\n",
        "chosen": b"chosen context\n",
        "deep": b"deep context\n",
        "ignored": b"ignored context\n",
    }
    for skill_id, content in contents.items():
        _write_content(tmp_path, skill_id, content)
    manifest = _write_manifest(
        tmp_path,
        [
            _entry("root", contents["root"], "chosen", "ignored"),
            _entry("chosen", contents["chosen"], "deep"),
            _entry("deep", contents["deep"]),
            _entry("ignored", contents["ignored"]),
        ],
    )
    content_reads: list[str] = []

    def content_reader(path: Path) -> bytes:
        content_reads.append(path.relative_to(tmp_path).as_posix())
        return path.read_bytes()

    catalog = RepositorySkillCatalog(
        tmp_path, manifest.relative_to(tmp_path), content_reader=content_reader
    )
    selected = {"root": ("chosen",), "chosen": ("deep",)}
    result = RecursiveContextRouter(
        catalog.load,
        lambda frame: selected.get(frame.skill.skill_id, ()),
    ).route("root", {"task": "load useful context only"})

    assert result.materialized_skill_ids == ("root", "chosen", "deep")
    assert content_reads == [
        "skills/root.md",
        "skills/chosen.md",
        "skills/deep.md",
    ]
    assert "skills/ignored.md" not in content_reads
    assert all(item.manifest_sha256 == catalog.manifest_sha256 for item in result.materialized)
    assert [item.source_ref for item in result.materialized] == content_reads
    assert [item.content_sha256 for item in result.materialized] == [
        _digest(contents[skill_id]) for skill_id in result.materialized_skill_ids
    ]
    public = result.as_dict()
    assert public["materialized"][1]["source_ref"] == "skills/chosen.md"
    assert public["materialized"][1]["content_sha256"] == _digest(
        contents["chosen"]
    )


def test_unselected_content_may_be_absent_without_being_read(tmp_path: Path) -> None:
    root_content = b"root\n"
    missing_content = b"not present on disk\n"
    _write_content(tmp_path, "root", root_content)
    manifest = _write_manifest(
        tmp_path,
        [
            _entry("root", root_content, "missing"),
            _entry("missing", missing_content),
        ],
    )

    catalog = RepositorySkillCatalog(tmp_path, manifest.relative_to(tmp_path))
    result = RecursiveContextRouter(catalog.load, lambda _frame: ()).route(
        "root", {}
    )

    assert result.materialized_skill_ids == ("root",)
    assert not (tmp_path / "skills" / "missing.md").exists()


def test_manifest_change_invalidates_in_flight_route_before_child_read(
    tmp_path: Path,
) -> None:
    root_content = b"root\n"
    child_content = b"child\n"
    _write_content(tmp_path, "root", root_content)
    _write_content(tmp_path, "child", child_content)
    manifest = _write_manifest(
        tmp_path,
        [_entry("root", root_content, "child"), _entry("child", child_content)],
    )
    content_reads: list[str] = []

    def reader(path: Path) -> bytes:
        content_reads.append(path.name)
        return path.read_bytes()

    catalog = RepositorySkillCatalog(tmp_path, manifest.name, content_reader=reader)

    def selector(frame):
        if frame.skill.skill_id == "root":
            manifest.write_bytes(manifest.read_bytes() + b"\n")
            return ("child",)
        return ()

    with pytest.raises(ContextRoutingError, match="manifest changed"):
        RecursiveContextRouter(catalog.load, selector).route("root", {})

    assert content_reads == ["root.md"]


def test_selected_content_digest_mismatch_fails_closed(tmp_path: Path) -> None:
    root_content = b"root\n"
    child_content = b"child\n"
    _write_content(tmp_path, "root", root_content)
    child_path = _write_content(tmp_path, "child", child_content)
    manifest = _write_manifest(
        tmp_path,
        [_entry("root", root_content, "child"), _entry("child", child_content)],
    )
    catalog = RepositorySkillCatalog(tmp_path, manifest.name)

    def selector(frame):
        if frame.skill.skill_id == "root":
            child_path.write_bytes(b"changed after manifest binding\n")
            return ("child",)
        return ()

    with pytest.raises(ContextRoutingError, match="content digest mismatch"):
        RecursiveContextRouter(catalog.load, selector).route("root", {})


@pytest.mark.parametrize(
    ("entry_patch", "message"),
    [
        ({"content_path": "../escape.md"}, "confined repository-relative"),
        ({"content_path": "skills\\escape.md"}, "POSIX repository-relative"),
        ({"content_sha256": "A" * 64}, "lowercase SHA-256"),
        ({"children": [{"skill_id": "unknown", "summary": "missing"}]}, "unknown child"),
    ],
)
def test_manifest_shape_and_paths_fail_closed(
    tmp_path: Path, entry_patch: dict[str, object], message: str
) -> None:
    root_content = b"root\n"
    _write_content(tmp_path, "root", root_content)
    root_entry = _entry("root", root_content)
    root_entry.update(entry_patch)
    manifest = _write_manifest(tmp_path, [root_entry])

    with pytest.raises(ValueError, match=message):
        RepositorySkillCatalog(tmp_path, manifest.name)


def test_selected_symlink_retargeted_outside_repository_fails_closed(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    root_content = b"root\n"
    child_content = b"child\n"
    _write_content(repo, "root", root_content)
    child_path = _write_content(repo, "child", child_content)
    outside = tmp_path / "outside.md"
    outside.write_bytes(child_content)
    manifest = _write_manifest(
        repo,
        [_entry("root", root_content, "child"), _entry("child", child_content)],
    )
    catalog = RepositorySkillCatalog(repo, manifest.name)

    def selector(frame):
        if frame.skill.skill_id == "root":
            child_path.unlink()
            child_path.symlink_to(outside)
            return ("child",)
        return ()

    with pytest.raises(ContextRoutingError, match="escapes repository root"):
        RecursiveContextRouter(catalog.load, selector).route("root", {})
