"""A* using generic reverse-push goal matching and static dead squares."""
from collections import deque
from itertools import permutations
from search_core import DIRECTIONS, check, search


class GoalMatching:
    def __init__(self, board, deadline):
        self.distances = []
        self.cache = {}
        self.deadline = deadline
        for goal in sorted(board.goals):
            distances = {goal: 0}
            queue = deque([goal])
            while queue:
                check(deadline)
                current = queue.popleft()
                for _, delta in DIRECTIONS:
                    previous = (current[0] - delta[0], current[1] - delta[1])
                    support = (previous[0] - delta[0], previous[1] - delta[1])
                    if previous in board.floor and support in board.floor and previous not in distances:
                        distances[previous] = distances[current] + 1
                        queue.append(previous)
            self.distances.append(distances)
        self.live_squares = set().union(*(set(d) for d in self.distances))

    def __call__(self, boxes):
        if boxes not in self.cache:
            estimate = float("inf")
            for order in permutations(range(len(self.distances))):
                check(self.deadline)
                cost = sum(self.distances[g].get(box, float("inf"))
                           for box, g in zip(boxes, order))
                estimate = min(estimate, cost)
            self.cache[boxes] = estimate
        return self.cache[boxes]


def solve(board, max_expansions=50000, seconds=60):
    return search(board, GoalMatching,
                  lambda b, d, h: lambda boxes: all(box in h.live_squares for box in boxes),
                  max_expansions=max_expansions, seconds=seconds)

