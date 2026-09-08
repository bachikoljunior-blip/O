from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


_SKILL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ContextRoutingError(RuntimeError):
    """Raised when recursive context routing cannot continue safely."""


def _validated_skill_id(value: object, field: str = "skill_id") -> str:
    if not isinstance(value, str) or not _SKILL_ID.fullmatch(value):
        raise ValueError(
            f"{field} must match {_SKILL_ID.pattern!r} and be at most 128 characters"
        )
    return value


def _bounded_nonempty_text(value: object, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds the {maximum}-character limit")
    return value


@dataclass(frozen=True)
class SkillReference:
    """Lightweight child metadata visible before the child content is loaded."""

    skill_id: str
    summary: str

    def __post_init__(self) -> None:
        _validated_skill_id(self.skill_id)
        _bounded_nonempty_text(self.summary, "summary", maximum=4096)


@dataclass(frozen=True)
class SkillNode:
    """One materializable Skill and the lightweight references it exposes."""

    skill_id: str
    content: str
    children: tuple[SkillReference, ...] = ()
    source_ref: str | None = None
    content_sha256: str | None = None
    manifest_sha256: str | None = None

    def __post_init__(self) -> None:
        _validated_skill_id(self.skill_id)
        _bounded_nonempty_text(self.content, "content", maximum=1_000_000)
        children = tuple(self.children)
        if not all(isinstance(child, SkillReference) for child in children):
            raise ValueError("children must contain only SkillReference values")
        child_ids = [child.skill_id for child in children]
        if len(set(child_ids)) != len(child_ids):
            raise ValueError("children must not contain duplicate skill_id values")
        if self.source_ref is not None:
            _bounded_nonempty_text(self.source_ref, "source_ref", maximum=4096)
        for field in ("content_sha256", "manifest_sha256"):
            value = getattr(self, field)
            if value is not None and (
                not isinstance(value, str) or not _SHA256.fullmatch(value)
            ):
                raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
        object.__setattr__(self, "children", children)


@dataclass(frozen=True)
class SelectionFrame:
    """Public context supplied to semantic selection at one recursion level."""

    situation: Mapping[str, Any]
    skill: SkillNode
    depth: int
    path: tuple[str, ...]
    remaining_node_budget: int
    remaining_context_chars: int


@dataclass(frozen=True)
class MaterializedContext:
    skill_id: str
    parent_skill_id: str | None
    depth: int
    path: tuple[str, ...]
    content: str
    source_ref: str | None = None
    content_sha256: str | None = None
    manifest_sha256: str | None = None


@dataclass(frozen=True)
class ContextRoutingResult:
    root_skill_id: str
    materialized: tuple[MaterializedContext, ...]
    trace: tuple[dict[str, Any], ...]
    total_context_chars: int

    @property
    def materialized_skill_ids(self) -> tuple[str, ...]:
        return tuple(item.skill_id for item in self.materialized)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "root_skill_id": self.root_skill_id,
            "materialized_skill_ids": list(self.materialized_skill_ids),
            "materialized": [
                {
                    "skill_id": item.skill_id,
                    "parent_skill_id": item.parent_skill_id,
                    "depth": item.depth,
                    "path": list(item.path),
                    "content": item.content,
                    "source_ref": item.source_ref,
                    "content_sha256": item.content_sha256,
                    "manifest_sha256": item.manifest_sha256,
                }
                for item in self.materialized
            ],
            "trace": [deepcopy(item) for item in self.trace],
            "total_context_chars": self.total_context_chars,
        }


SkillLoader = Callable[[str], SkillNode]
SkillSelector = Callable[[SelectionFrame], Sequence[str]]


class RecursiveContextRouter:
    """Lazily materialize only situation-selected branches of a Skill hierarchy.

    The router deliberately contains no semantic relevance heuristic.  The active model,
    prompt, or scoped Candidate supplies ``selector`` and therefore owns the situation-
    dependent judgment.  This class only validates that judgment and enforces explicit
    depth, node, fan-out, cycle, and context-size boundaries.

    A caller may choose any finite ``max_depth``; the implementation does not bake in a
    fixed semantic hierarchy depth.  Traversal is iterative so a useful deep hierarchy is
    limited by the caller's explicit budget rather than Python's recursion limit.
    """

    def __init__(
        self,
        loader: SkillLoader,
        selector: SkillSelector,
        *,
        max_depth: int = 32,
        max_nodes: int = 128,
        max_selected_children: int = 32,
        max_context_chars: int = 200_000,
    ) -> None:
        if not callable(loader):
            raise ValueError("loader must be callable")
        if not callable(selector):
            raise ValueError("selector must be callable")
        self.max_depth = self._nonnegative_int(max_depth, "max_depth")
        self.max_nodes = self._positive_int(max_nodes, "max_nodes")
        self.max_selected_children = self._positive_int(
            max_selected_children, "max_selected_children"
        )
        self.max_context_chars = self._positive_int(
            max_context_chars, "max_context_chars"
        )
        self.loader = loader
        self.selector = selector

    @staticmethod
    def _positive_int(value: object, field: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{field} must be a positive integer")
        return value

    @staticmethod
    def _nonnegative_int(value: object, field: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{field} must be a non-negative integer")
        return value

    def _load(self, skill_id: str) -> SkillNode:
        try:
            node = self.loader(skill_id)
        except ContextRoutingError:
            raise
        except Exception as exc:
            raise ContextRoutingError(f"failed to load selected skill {skill_id!r}") from exc
        if not isinstance(node, SkillNode):
            raise ContextRoutingError(
                f"loader returned a non-SkillNode value for {skill_id!r}"
            )
        if node.skill_id != skill_id:
            raise ContextRoutingError(
                f"loader identity mismatch: requested {skill_id!r}, got {node.skill_id!r}"
            )
        return node

    def _select(self, frame: SelectionFrame) -> tuple[str, ...]:
        try:
            raw = self.selector(frame)
        except ContextRoutingError:
            raise
        except Exception as exc:
            raise ContextRoutingError(
                f"selector failed for skill {frame.skill.skill_id!r}"
            ) from exc
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            raise ContextRoutingError("selector must return a finite sequence of child skill IDs")
        selected = tuple(raw)
        if len(selected) > self.max_selected_children:
            raise ContextRoutingError(
                "selector exceeded max_selected_children for one Skill"
            )
        for child_id in selected:
            try:
                _validated_skill_id(child_id, "selected child skill_id")
            except ValueError as exc:
                raise ContextRoutingError(str(exc)) from exc
        if len(set(selected)) != len(selected):
            raise ContextRoutingError("selector returned a duplicate child skill_id")
        available = {child.skill_id for child in frame.skill.children}
        unknown = [child_id for child_id in selected if child_id not in available]
        if unknown:
            raise ContextRoutingError(
                f"selector chose children not exposed by {frame.skill.skill_id!r}: {unknown}"
            )
        return selected

    def route(
        self,
        root_skill_id: str,
        situation: Mapping[str, Any],
    ) -> ContextRoutingResult:
        root_skill_id = _validated_skill_id(root_skill_id, "root_skill_id")
        if not isinstance(situation, Mapping):
            raise ValueError("situation must be a mapping")
        frozen_situation = deepcopy(dict(situation))

        # Stack entries are (skill_id, parent_skill_id, depth, path).  Reversing each
        # selected child list preserves the selector's order under depth-first traversal.
        stack: list[tuple[str, str | None, int, tuple[str, ...]]] = [
            (root_skill_id, None, 0, (root_skill_id,))
        ]
        scheduled = {root_skill_id}
        materialized_ids: set[str] = set()
        materialized: list[MaterializedContext] = []
        trace: list[dict[str, Any]] = []
        total_chars = 0

        while stack:
            skill_id, parent_skill_id, depth, path = stack.pop()
            if skill_id in materialized_ids:
                trace.append(
                    {
                        "event": "reuse",
                        "skill_id": skill_id,
                        "parent_skill_id": parent_skill_id,
                        "depth": depth,
                        "path": list(path),
                    }
                )
                continue
            if depth > self.max_depth:
                raise ContextRoutingError(
                    f"selected route exceeds max_depth={self.max_depth} at {skill_id!r}"
                )
            node = self._load(skill_id)
            projected_chars = total_chars + len(node.content)
            if projected_chars > self.max_context_chars:
                raise ContextRoutingError(
                    f"selected context exceeds max_context_chars={self.max_context_chars}"
                )
            total_chars = projected_chars
            materialized_ids.add(skill_id)
            item = MaterializedContext(
                skill_id=skill_id,
                parent_skill_id=parent_skill_id,
                depth=depth,
                path=path,
                content=node.content,
                source_ref=node.source_ref,
                content_sha256=node.content_sha256,
                manifest_sha256=node.manifest_sha256,
            )
            materialized.append(item)
            trace.append(
                {
                    "event": "materialized",
                    "skill_id": skill_id,
                    "parent_skill_id": parent_skill_id,
                    "depth": depth,
                    "path": list(path),
                    "context_chars": len(node.content),
                    "source_ref": node.source_ref,
                    "content_sha256": node.content_sha256,
                    "manifest_sha256": node.manifest_sha256,
                }
            )

            frame = SelectionFrame(
                situation=deepcopy(frozen_situation),
                skill=node,
                depth=depth,
                path=path,
                remaining_node_budget=self.max_nodes - len(scheduled),
                remaining_context_chars=self.max_context_chars - total_chars,
            )
            selected = self._select(frame)
            trace.append(
                {
                    "event": "selected",
                    "skill_id": skill_id,
                    "depth": depth,
                    "selected_child_ids": list(selected),
                }
            )
            if selected and depth >= self.max_depth:
                raise ContextRoutingError(
                    f"selector chose children beyond max_depth={self.max_depth} at {skill_id!r}"
                )
            for child_id in selected:
                if child_id in path:
                    cycle = " -> ".join((*path, child_id))
                    raise ContextRoutingError(f"selected Skill cycle: {cycle}")

            new_ids = [
                child_id
                for child_id in selected
                if child_id not in materialized_ids and child_id not in scheduled
            ]
            if len(scheduled) + len(new_ids) > self.max_nodes:
                raise ContextRoutingError(
                    f"selected route exceeds max_nodes={self.max_nodes}"
                )

            for child_id in reversed(selected):
                if child_id in materialized_ids or child_id in scheduled:
                    trace.append(
                        {
                            "event": "coalesced",
                            "skill_id": child_id,
                            "parent_skill_id": skill_id,
                            "depth": depth + 1,
                            "path": list((*path, child_id)),
                        }
                    )
                    continue
                scheduled.add(child_id)
                stack.append((child_id, skill_id, depth + 1, (*path, child_id)))

        return ContextRoutingResult(
            root_skill_id=root_skill_id,
            materialized=tuple(materialized),
            trace=tuple(trace),
            total_context_chars=total_chars,
        )
