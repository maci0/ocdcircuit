from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ocdcircuit import Board


class FootprintDimensionsTest(unittest.TestCase):
    def test_numeric_dimensions_and_rollback(self) -> None:
        for width, height in ((4, 2), (4, 2.5), (4.5, 2), (4.5, 2.5)):
            with self.subTest(width=width, height=height):
                board = Board("numeric")
                snap = board.ctx.snapshot()
                board.add_footprint("CUSTOM", {
                    "w": width, "h": height,
                    "pads": {"1": (0.0, 0.0, 1.0, 1.0)},
                })
                board.add_part("J1", "CUSTOM", x=10, y=10)
                part = board.parts["J1"]
                self.assertEqual(part.size, (width, height))
                self.assertIsInstance(part.w, float)
                self.assertIsInstance(part.h, float)
                board.connect("N", "J1", 1)
                self.assertEqual(board.pad_pos("J1", 1), (10, 10))
                part.attrs["rot"] = "90"
                self.assertEqual(part.size, (height, width))
                self.assertEqual(board.check("erc")["errors"], [])
                board.ctx.rollback(snap)
                self.assertEqual(board.parts, {})
                self.assertEqual(board.nets, {})
                self.assertEqual(board.custom_fp, {})
                board.unload()


class SilkScoreTest(unittest.TestCase):
    def test_single_column_copper(self) -> None:
        from ocdcircuit.score import tidy

        board = Board("single-column")
        board.add_footprint("CUSTOM", {
            "w": 1.0, "h": 1.0,
            "pads": {"1": (0.0, 0.0, 1.0, 1.0)},
        })
        board.add_part("J1", "CUSTOM", x=5, y=5)
        board.add_part("J2", "CUSTOM", x=5, y=6.3)
        self.assertEqual(tidy(board)["T14_silk_overlap"],
                         {"text_text": 0, "text_copper": 1})


if __name__ == "__main__":
    unittest.main()
