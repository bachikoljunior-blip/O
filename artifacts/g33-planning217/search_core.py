"""Generic deterministic legal-push graph, shared unchanged by both methods.

This is a bounded experiment, not learned planning or independent evaluation.
No corpus-specific rules. One expansion is a non-stale, non-terminal state
whose successors are considered. Player position is part of the exact key.
"""
from collections import deque
from dataclasses import dataclass
import heapq
import itertools
import time

DIRECTIONS = (("U", (-1, 0)), ("R", (0, 1)), ("D", (1, 0)), ("L", (0, -1)))


def add(a, b):
    return a[0] + b[0], a[1] + b[1]


class Deadline(Exception):
    pass


def check(deadline):
    if time.monotonic() >= deadline:
        raise Deadline


@dataclass(frozen=True)
class Board:
    floor: frozenset
    goals: frozenset
    boxes: tuple
    player: tuple


def parse_board(rows):
    if not rows or len({len(row) for row in rows}) != 1:
        raise ValueError("board must be nonempty and rectangular")
    floor, goals, boxes, players = set(), set(), [], []
    for r, row in enumerate(rows):
        for c, ch in enumerate(row):
            if ch not in "# @$.+*":
                raise ValueError("unsupported cell")
            pos = (r, c)
            if ch != "#":
                floor.add(pos)
            if ch in ".+*":
                goals.add(pos)
            if ch in "$*":
                boxes.append(pos)
            if ch in "@+":
                players.append(pos)
    if len(players) != 1 or not boxes or len(boxes) != len(goals):
        raise ValueError("one player and equal nonzero boxes/goals required")
    return Board(frozenset(floor), frozenset(goals), tuple(sorted(boxes)), players[0])


def reachable(board, player, boxes, deadline):
    paths = {player: ""}
    queue = deque([player])
    occupied = set(boxes)
    while queue:
        check(deadline)
        pos = queue.popleft()
        for move, delta in DIRECTIONS:
            nxt = add(pos, delta)
            if nxt in board.floor and nxt not in occupied and nxt not in paths:
                paths[nxt] = paths[pos] + move
                queue.append(nxt)
    return paths


def search(board, heuristic_factory, allowed_factory, max_expansions=50000, seconds=60):
    started = time.monotonic()
    deadline = started + seconds
    expanded = generated = 0
    maximum_frontier = 0

    def result(status, cause, actions=None, pushes=None):
        return {"status": status, "termination_cause": cause,
                "expanded_states": expanded, "generated_states": generated,
                "maximum_frontier": maximum_frontier,
                "elapsed_seconds": time.monotonic() - started,
                "actions": actions, "pushes": pushes}

    try:
        # Heuristic preparation is included in the same wall-clock budget.
        heuristic = heuristic_factory(board, deadline)
        allowed = allowed_factory(board, deadline, heuristic)
        key = (board.boxes, board.player)
        estimate = heuristic(board.boxes)
        check(deadline)
        if estimate == float("inf") or not allowed(board.boxes):
            return result("unsolved", "static_unreachable_goal_matching")
        serial = itertools.count()
        frontier = [(estimate, next(serial), 0, key)]
        best = {key: 0}
        parents = {}
        maximum_frontier = 1
        while frontier:
            check(deadline)
            _, _, cost, key = heapq.heappop(frontier)
            if cost != best.get(key):
                continue
            boxes, player = key
            if frozenset(boxes) == board.goals:
                segments = []
                cursor = key
                while cursor in parents:
                    cursor, segment = parents[cursor]
                    segments.append(segment)
                actions = "".join(reversed(segments))
                check(deadline)
                return result("solved", "all_boxes_on_goals", actions, cost)
            if expanded >= max_expansions:
                return result("unsolved", "expanded_state_limit")
            expanded += 1
            paths = reachable(board, player, boxes, deadline)
            occupied = set(boxes)
            # Direction-major, then lexicographic box position; heap ties use
            # insertion serial. Both methods use precisely this ordering.
            for move, delta in DIRECTIONS:
                for box in boxes:
                    check(deadline)
                    behind = (box[0] - delta[0], box[1] - delta[1])
                    dest = add(box, delta)
                    if behind not in paths or dest not in board.floor or dest in occupied:
                        continue
                    new_boxes = tuple(sorted((occupied - {box}) | {dest}))
                    if not allowed(new_boxes):
                        continue
                    new_key = (new_boxes, box)
                    new_cost = cost + 1
                    if new_cost >= best.get(new_key, float("inf")):
                        continue
                    h = heuristic(new_boxes)
                    check(deadline)
                    if h == float("inf"):
                        continue
                    best[new_key] = new_cost
                    parents[new_key] = (key, paths[behind] + move)
                    heapq.heappush(frontier, (new_cost + h, next(serial), new_cost, new_key))
                    generated += 1
                    maximum_frontier = max(maximum_frontier, len(frontier))
        return result("unsolved", "frontier_exhausted")
    except Deadline:
        return result("timeout", "wall_clock_limit")

