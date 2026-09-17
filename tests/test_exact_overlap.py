from __future__ import annotations

import unittest
from unittest.mock import patch

from benches.exact_overlap_pilot import _feas


class ExactOverlapTests(unittest.TestCase):
    def test_part_larger_than_board(self) -> None:
        self.assertEqual(_feas(1, 1.5, 2.0, 1.0, 1.0)[0], "no")

    def test_feasibility_and_wirelength(self) -> None:
        self.assertEqual(_feas(2, 4.0, 2.0, 1.0, 1.0)[0], "yes")
        self.assertEqual(_feas(2, 2.0, 2.0, 1.0, 1.0)[0], "no")
        self.assertEqual(_feas(2, 4.0, 2.0, 1.0, 1.0, [(0, 1)], 1.0)[0], "no")

    def test_completed_search_near_deadline(self) -> None:
        with patch("benches.exact_overlap_pilot.time.perf_counter",
                   side_effect=[0.0] + [0.995] * 100):
            self.assertEqual(_feas(2, 2.0, 2.0, 1.0, 1.0)[0], "no")

    def test_timeout_stops_sibling_search(self) -> None:
        with patch("benches.exact_overlap_pilot.time.perf_counter",
                   side_effect=[0.0, 0.0] + [2.0] * 100):
            flag, _, nodes = _feas(2, 4.0, 2.0, 1.0, 1.0)
            self.assertEqual(flag, "timeout")
            self.assertLessEqual(nodes, 1)


if __name__ == "__main__":
    unittest.main()
