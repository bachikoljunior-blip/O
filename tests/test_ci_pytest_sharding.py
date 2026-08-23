from __future__ import annotations

import pytest

from conftest import select_pytest_shard_nodeids


def test_pytest_shards_are_deterministic_disjoint_and_exhaustive() -> None:
    nodeids = [f"tests/test_{position:02d}.py::test_case" for position in range(19)]
    shards = [
        select_pytest_shard_nodeids(nodeids, total=4, index=index)
        for index in range(4)
    ]

    assert shards == [
        select_pytest_shard_nodeids(
            list(reversed(nodeids)), total=4, index=index
        )
        for index in range(4)
    ]
    assert set().union(*(set(shard) for shard in shards)) == set(nodeids)
    assert sum(len(shard) for shard in shards) == len(nodeids)
    assert max(map(len, shards)) - min(map(len, shards)) <= 1


@pytest.mark.parametrize(
    ("total", "index", "message"),
    [
        (0, 0, "positive integer"),
        (True, 0, "positive integer"),
        (4, -1, "0 <= index < total"),
        (4, 4, "0 <= index < total"),
    ],
)
def test_pytest_shard_bounds_fail_closed(total: object, index: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        select_pytest_shard_nodeids(
            ["tests/test_a.py::test_a"],
            total=total,  # type: ignore[arg-type]
            index=index,  # type: ignore[arg-type]
        )


def test_pytest_shards_reject_duplicate_nodeids() -> None:
    with pytest.raises(ValueError, match="unique"):
        select_pytest_shard_nodeids(
            ["tests/test_a.py::test_a", "tests/test_a.py::test_a"],
            total=2,
            index=0,
        )
