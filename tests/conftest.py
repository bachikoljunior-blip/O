from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Sequence

import pytest


def select_pytest_shard_nodeids(
    nodeids: Sequence[str],
    *,
    total: int,
    index: int,
) -> tuple[str, ...]:
    """Return one deterministic, disjoint pytest collection shard.

    Sorting before round-robin assignment makes the result independent of
    filesystem collection order while keeping shard sizes within one test.
    Every exact node id is assigned once across ``range(total)``.
    """

    if not isinstance(total, int) or isinstance(total, bool) or total < 1:
        raise ValueError("pytest shard total must be a positive integer")
    if (
        not isinstance(index, int)
        or isinstance(index, bool)
        or index < 0
        or index >= total
    ):
        raise ValueError("pytest shard index must satisfy 0 <= index < total")
    if len(set(nodeids)) != len(nodeids):
        raise ValueError("pytest nodeids must be unique")
    return tuple(
        nodeid
        for position, nodeid in enumerate(sorted(nodeids))
        if position % total == index
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    total_text = os.environ.get("PYTEST_SHARD_TOTAL")
    index_text = os.environ.get("PYTEST_SHARD_INDEX")
    if total_text is None and index_text is None:
        return
    if total_text is None or index_text is None:
        raise pytest.UsageError(
            "PYTEST_SHARD_TOTAL and PYTEST_SHARD_INDEX must be set together"
        )
    try:
        total = int(total_text)
        index = int(index_text)
        selected_nodeids = set(
            select_pytest_shard_nodeids(
                [item.nodeid for item in items], total=total, index=index
            )
        )
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from exc

    selected: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        (selected if item.nodeid in selected_nodeids else deselected).append(item)
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected


@pytest.fixture
def runtime_repo(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[1]
    shutil.copytree(source / "prompts", tmp_path / "prompts")
    shutil.copytree(source / ".continual" / "system", tmp_path / ".continual" / "system")
    (tmp_path / ".continual" / "candidates").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".continual" / "candidates" / "index.json").write_text(
        '{"schema_version": 2, "candidates": []}\n', encoding="utf-8"
    )
    return tmp_path
