"""Uniform-cost search: zero heuristic, no static dead-square pruning."""
from search_core import search


def solve(board, max_expansions=50000, seconds=60):
    return search(board, lambda b, d: lambda boxes: 0,
                  lambda b, d, h: lambda boxes: True,
                  max_expansions=max_expansions, seconds=seconds)

