"""Tiny hand-authored non-corpus sanity checks. Never loads selected data."""
import time
import unittest
import astar
import baseline
from replay import verify
from search_core import parse_board


class Sanity(unittest.TestCase):
    def test_single_push(self):
        rows = ["#####", "#@$.#", "#####"]
        for solver in (baseline.solve, astar.solve):
            outcome = solver(parse_board(rows), max_expansions=100, seconds=1)
            self.assertEqual(outcome["status"], "solved")
            self.assertEqual(outcome["actions"], "R")
            self.assertTrue(verify(rows, outcome["actions"])["valid"])

    def test_two_pushes(self):
        rows = ["######", "#@ $.#", "# $ .#", "######"]
        for solver in (baseline.solve, astar.solve):
            outcome = solver(parse_board(rows), max_expansions=1000, seconds=1)
            self.assertEqual(outcome["status"], "solved")
            self.assertTrue(verify(rows, outcome["actions"])["valid"])

    def test_judge_rejects(self):
        rows = ["#####", "#@$.#", "#####"]
        for moves in ("", "U", "RR", "X"):
            self.assertFalse(verify(rows, moves)["valid"])

    def test_limits(self):
        b = parse_board(["#####", "#@$.#", "#####"])
        for solver in (baseline.solve, astar.solve):
            self.assertEqual(solver(b, max_expansions=0)["termination_cause"], "expanded_state_limit")
            self.assertEqual(solver(b, seconds=0)["status"], "timeout")

    def test_reverse_push_admissible(self):
        b = parse_board(["######", "#@ $ .", "######"])
        h = astar.GoalMatching(b, time.monotonic() + 1)
        self.assertEqual(h(b.boxes), 2)
        self.assertEqual(h(tuple(sorted(b.goals))), 0)


if __name__ == "__main__":
    unittest.main()

