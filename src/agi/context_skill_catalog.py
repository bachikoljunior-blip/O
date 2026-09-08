from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping

from .context_skill_router import (
    ContextRoutingError,
    SkillNode,
    SkillReference,
    _validated_skill_id,
)


_MAX_MANIFEST_BYTES = 1_000_000
_MAX_CONTENT_BYTES = 1_000_000


@dataclass(frozen=True)
class _CatalogEntry:
    skill_id: str
    content_path: str
    content_sha256: str
    children: tuple[SkillReference, ...]


ContentReader = Callable[[Path], bytes]


class RepositorySkillCatalog:
    """Bind a recursive Skill graph to repository files without eager content reads.

    The JSON manifest contains lightweight child summaries and expected content
    digests.  Construction validates only manifest metadata.  ``load`` reads the
    selected Skill's content, verifies its digest, and returns a ``SkillNode`` for
    ``RecursiveContextRouter``.  Before every selected load the manifest digest is
    rechecked, so a routing session never silently mixes decisions made against one
    graph revision with content from a later graph revision.
    """

    def __init__(
        self,
        root: Path,
        manifest_path: str | Path,
        *,
        content_reader: ContentReader | None = None,
    ) -> None:
        self.root = root.resolve()
        raw_manifest_path = Path(manifest_path)
        candidate = (
            raw_manifest_path
            if raw_manifest_path.is_absolute()
            else self.root / raw_manifest_path
        )
        self.manifest_path = candidate.resolve()
        self._assert_repo_path(self.manifest_path, "manifest_path")
        self._content_reader = content_reader or Path.read_bytes

        manifest_bytes = self._read_manifest_bytes()
        self.manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        self._entries = self._parse_manifest(manifest_bytes)

    @property
    def skill_ids(self) -> tuple[str, ...]:
        return tuple(self._entries)

    def _assert_repo_path(self, path: Path, field: str) -> None:
        if path == self.root or self.root not in path.parents:
            raise ValueError(f"{field} escapes repository root")

    def _read_manifest_bytes(self) -> bytes:
        try:
            data = self.manifest_path.read_bytes()
        except OSError as exc:
            raise ContextRoutingError("failed to read Skill catalog manifest") from exc
        if len(data) > _MAX_MANIFEST_BYTES:
            raise ContextRoutingError("Skill catalog manifest exceeds byte limit")
        return data

    @staticmethod
    def _sha256(value: object, field: str) -> str:
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
        try:
            parsed = bytes.fromhex(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be a lowercase SHA-256 hex digest") from exc
        if len(parsed) != 32 or value != value.lower():
            raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
        return value

    def _validated_content_path(self, value: object, field: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} must be a non-empty repository-relative path")
        if "\\" in value:
            raise ValueError(f"{field} must use POSIX repository-relative separators")
        pure = PurePosixPath(value)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise ValueError(f"{field} must be a confined repository-relative path")
        resolved = (self.root / Path(*pure.parts)).resolve()
        self._assert_repo_path(resolved, field)
        return pure.as_posix()

    def _parse_manifest(self, data: bytes) -> Mapping[str, _CatalogEntry]:
        try:
            decoded = data.decode("utf-8")
            manifest = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Skill catalog manifest must be valid UTF-8 JSON") from exc
        if not isinstance(manifest, dict) or set(manifest) != {
            "schema_version",
            "skills",
        }:
            raise ValueError(
                "Skill catalog manifest must contain only schema_version and skills"
            )
        if manifest["schema_version"] != 1:
            raise ValueError("unsupported Skill catalog schema_version")
        raw_skills = manifest["skills"]
        if not isinstance(raw_skills, list) or not raw_skills:
            raise ValueError("Skill catalog skills must be a non-empty list")

        entries: dict[str, _CatalogEntry] = {}
        content_paths: set[str] = set()
        for position, raw in enumerate(raw_skills):
            field = f"skills[{position}]"
            if not isinstance(raw, dict) or set(raw) != {
                "skill_id",
                "content_path",
                "content_sha256",
                "children",
            }:
                raise ValueError(
                    f"{field} must contain only skill_id, content_path, "
                    "content_sha256, and children"
                )
            skill_id = _validated_skill_id(raw["skill_id"], f"{field}.skill_id")
            if skill_id in entries:
                raise ValueError(f"duplicate Skill catalog skill_id: {skill_id!r}")
            content_path = self._validated_content_path(
                raw["content_path"], f"{field}.content_path"
            )
            if content_path in content_paths:
                raise ValueError(f"duplicate Skill catalog content_path: {content_path!r}")
            content_paths.add(content_path)
            content_sha256 = self._sha256(
                raw["content_sha256"], f"{field}.content_sha256"
            )
            raw_children = raw["children"]
            if not isinstance(raw_children, list):
                raise ValueError(f"{field}.children must be a list")
            children: list[SkillReference] = []
            for child_position, raw_child in enumerate(raw_children):
                child_field = f"{field}.children[{child_position}]"
                if not isinstance(raw_child, dict) or set(raw_child) != {
                    "skill_id",
                    "summary",
                }:
                    raise ValueError(
                        f"{child_field} must contain only skill_id and summary"
                    )
                children.append(
                    SkillReference(raw_child["skill_id"], raw_child["summary"])
                )
            entries[skill_id] = _CatalogEntry(
                skill_id=skill_id,
                content_path=content_path,
                content_sha256=content_sha256,
                children=tuple(children),
            )

        for entry in entries.values():
            missing = [
                child.skill_id
                for child in entry.children
                if child.skill_id not in entries
            ]
            if missing:
                raise ValueError(
                    f"Skill {entry.skill_id!r} exposes unknown child IDs: {missing}"
                )
        return entries

    def _verify_manifest_unchanged(self) -> None:
        current = hashlib.sha256(self._read_manifest_bytes()).hexdigest()
        if current != self.manifest_sha256:
            raise ContextRoutingError(
                "Skill catalog manifest changed after routing context was bound"
            )

    def _content_file(self, entry: _CatalogEntry) -> Path:
        path = (self.root / Path(*PurePosixPath(entry.content_path).parts)).resolve()
        try:
            self._assert_repo_path(path, "content_path")
        except ValueError as exc:
            raise ContextRoutingError(str(exc)) from exc
        return path

    def load(self, skill_id: str) -> SkillNode:
        try:
            skill_id = _validated_skill_id(skill_id)
        except ValueError as exc:
            raise ContextRoutingError(str(exc)) from exc
        self._verify_manifest_unchanged()
        entry = self._entries.get(skill_id)
        if entry is None:
            raise ContextRoutingError(f"Skill catalog does not contain {skill_id!r}")
        content_path = self._content_file(entry)
        try:
            content_bytes = self._content_reader(content_path)
        except OSError as exc:
            raise ContextRoutingError(
                f"failed to read selected Skill {skill_id!r}"
            ) from exc
        if not isinstance(content_bytes, bytes):
            raise ContextRoutingError("Skill content reader must return bytes")
        if len(content_bytes) > _MAX_CONTENT_BYTES:
            raise ContextRoutingError(
                f"selected Skill {skill_id!r} exceeds byte limit"
            )
        observed_sha256 = hashlib.sha256(content_bytes).hexdigest()
        if observed_sha256 != entry.content_sha256:
            raise ContextRoutingError(
                f"selected Skill {skill_id!r} content digest mismatch"
            )
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContextRoutingError(
                f"selected Skill {skill_id!r} is not valid UTF-8"
            ) from exc
        return SkillNode(
            skill_id=entry.skill_id,
            content=content,
            children=entry.children,
            source_ref=entry.content_path,
            content_sha256=observed_sha256,
            manifest_sha256=self.manifest_sha256,
        )
