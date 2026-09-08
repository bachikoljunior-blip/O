from __future__ import annotations

from collections import Counter

import pytest

from agi.context_skill_router import (
    ContextRoutingError,
    RecursiveContextRouter,
    SelectionFrame,
    SkillNode,
    SkillReference,
)


def _node(skill_id: str, *children: str, content: str | None = None) -> SkillNode:
    return SkillNode(
        skill_id=skill_id,
        content=content or f"context:{skill_id}",
        children=tuple(
            SkillReference(child_id, f"summary:{child_id}") for child_id in children
        ),
    )


def test_recurses_to_useful_depth_selects_multiple_children_and_loads_only_selected() -> None:
    graph = {
        "root": _node("root", "planning", "coding", "irrelevant"),
        "planning": _node("planning", "strategy", "unused-plan"),
        "strategy": _node("strategy", "deep"),
        "deep": _node("deep"),
        "coding": _node("coding", "tests", "unused-code"),
        "tests": _node("tests"),
        "irrelevant": _node("irrelevant"),
        "unused-plan": _node("unused-plan"),
        "unused-code": _node("unused-code"),
    }
    selected = {
        "root": ("planning", "coding"),
        "planning": ("strategy",),
        "strategy": ("deep",),
        "coding": ("tests",),
    }
    loaded: list[str] = []
    seen_situations: list[dict[str, object]] = []

    def loader(skill_id: str) -> SkillNode:
        loaded.append(skill_id)
        return graph[skill_id]

    def selector(frame: SelectionFrame) -> tuple[str, ...]:
        seen_situations.append(dict(frame.situation))
        return selected.get(frame.skill.skill_id, ())

    result = RecursiveContextRouter(loader, selector, max_depth=12).route(
        "root", {"task": "implement recursive routing", "needs": ["plan", "tests"]}
    )

    assert result.materialized_skill_ids == (
        "root",
        "planning",
        "strategy",
        "deep",
        "coding",
        "tests",
    )
    assert loaded == list(result.materialized_skill_ids)
    assert not {"irrelevant", "unused-plan", "unused-code"}.intersection(loaded)
    assert all(item["task"] == "implement recursive routing" for item in seen_situations)
    assert [item.depth for item in result.materialized] == [0, 1, 2, 3, 1, 2]
    assert result.as_dict()["materialized_skill_ids"] == list(
        result.materialized_skill_ids
    )


def test_caller_can_select_a_deep_hierarchy_without_python_recursion() -> None:
    last_depth = 80
    graph = {
        f"skill-{depth}": _node(
            f"skill-{depth}",
            *((f"skill-{depth + 1}",) if depth < last_depth else ()),
        )
        for depth in range(last_depth + 1)
    }

    result = RecursiveContextRouter(
        graph.__getitem__,
        lambda frame: tuple(child.skill_id for child in frame.skill.children),
        max_depth=last_depth,
        max_nodes=last_depth + 1,
    ).route("skill-0", {"reason": "deep context is useful"})

    assert len(result.materialized) == last_depth + 1
    assert result.materialized[-1].skill_id == f"skill-{last_depth}"
    assert result.materialized[-1].depth == last_depth


def test_shared_selected_child_is_loaded_and_materialized_once() -> None:
    graph = {
        "root": _node("root", "left", "right"),
        "left": _node("left", "shared"),
        "right": _node("right", "shared"),
        "shared": _node("shared"),
    }
    selected = {
        "root": ("left", "right"),
        "left": ("shared",),
        "right": ("shared",),
    }
    calls: Counter[str] = Counter()

    def loader(skill_id: str) -> SkillNode:
        calls[skill_id] += 1
        return graph[skill_id]

    result = RecursiveContextRouter(
        loader, lambda frame: selected.get(frame.skill.skill_id, ())
    ).route("root", {})

    assert result.materialized_skill_ids == ("root", "left", "shared", "right")
    assert calls == Counter({"root": 1, "left": 1, "shared": 1, "right": 1})
    assert any(
        event["event"] == "coalesced" and event["skill_id"] == "shared"
        for event in result.trace
    )


def test_selected_cycle_fails_before_reloading_ancestor() -> None:
    graph = {
        "root": _node("root", "child"),
        "child": _node("child", "root"),
    }
    loaded: list[str] = []

    def loader(skill_id: str) -> SkillNode:
        loaded.append(skill_id)
        return graph[skill_id]

    with pytest.raises(ContextRoutingError, match="selected Skill cycle"):
        RecursiveContextRouter(
            loader,
            lambda frame: tuple(child.skill_id for child in frame.skill.children),
        ).route("root", {})

    assert loaded == ["root", "child"]


@pytest.mark.parametrize(
    ("selection", "message"),
    [
        (("unknown",), "not exposed"),
        (("child", "child"), "duplicate"),
        ("child", "finite sequence"),
        (("bad/id",), "must match"),
    ],
)
def test_invalid_semantic_selection_fails_closed(selection: object, message: str) -> None:
    graph = {"root": _node("root", "child"), "child": _node("child")}

    with pytest.raises(ContextRoutingError, match=message):
        RecursiveContextRouter(
            graph.__getitem__, lambda _frame: selection  # type: ignore[arg-type,return-value]
        ).route("root", {})


def test_depth_and_node_budgets_reject_selected_branches_before_loading_them() -> None:
    graph = {
        "root": _node("root", "one", "two"),
        "one": _node("one"),
        "two": _node("two"),
    }
    loaded: list[str] = []

    def loader(skill_id: str) -> SkillNode:
        loaded.append(skill_id)
        return graph[skill_id]

    with pytest.raises(ContextRoutingError, match="beyond max_depth=0"):
        RecursiveContextRouter(
            loader,
            lambda frame: tuple(child.skill_id for child in frame.skill.children),
            max_depth=0,
        ).route("root", {})
    assert loaded == ["root"]

    loaded.clear()
    with pytest.raises(ContextRoutingError, match="exceeds max_nodes=2"):
        RecursiveContextRouter(
            loader,
            lambda frame: tuple(child.skill_id for child in frame.skill.children),
            max_nodes=2,
        ).route("root", {})
    assert loaded == ["root"]


def test_context_budget_and_loader_identity_fail_closed() -> None:
    graph = {
        "root": _node("root", "child", content="12345"),
        "child": _node("child", content="67890"),
    }
    loaded: list[str] = []

    def loader(skill_id: str) -> SkillNode:
        loaded.append(skill_id)
        return graph[skill_id]

    with pytest.raises(ContextRoutingError, match="max_context_chars=9"):
        RecursiveContextRouter(
            loader,
            lambda frame: tuple(child.skill_id for child in frame.skill.children),
            max_context_chars=9,
        ).route("root", {})
    assert loaded == ["root", "child"]

    with pytest.raises(ContextRoutingError, match="identity mismatch"):
        RecursiveContextRouter(
            lambda _skill_id: _node("different"), lambda _frame: ()
        ).route("root", {})


def test_skill_descriptors_reject_ambiguous_or_unsafe_shape() -> None:
    with pytest.raises(ValueError, match="must match"):
        SkillReference("../escape", "unsafe")
    with pytest.raises(ValueError, match="duplicate"):
        SkillNode(
            "root",
            "context",
            (
                SkillReference("child", "first"),
                SkillReference("child", "second"),
            ),
        )
    with pytest.raises(ValueError, match="non-empty"):
        SkillNode("root", "")


def test_situation_is_copied_before_selector_can_observe_later_caller_mutation() -> None:
    graph = {"root": _node("root")}
    situation = {"nested": {"value": 1}}
    observed: list[int] = []

    def selector(frame: SelectionFrame) -> tuple[str, ...]:
        observed.append(frame.situation["nested"]["value"])
        return ()

    router = RecursiveContextRouter(graph.__getitem__, selector)
    result = router.route("root", situation)
    situation["nested"]["value"] = 2

    assert observed == [1]
    assert result.materialized_skill_ids == ("root",)
