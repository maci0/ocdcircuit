from __future__ import annotations

import os
import sys
import unittest
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from threading import Barrier

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ocdcircuit import Board
from ocdcircuit.kb import KB


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


class KBAddSourceFileTest(unittest.TestCase):
    def test_changed_content_keeps_both_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "errata.txt"
            kb = KB(tmp)
            src.write_text("old", encoding="utf-8")
            self.assertEqual(kb.add(str(src))["added"], "errata.txt")
            src.write_text("new", encoding="utf-8")
            self.assertEqual(kb.add(str(src))["added"], "errata-2.txt")
            self.assertEqual(KB(tmp).add(str(src))["added"], "errata-2.txt")
            self.assertEqual((Path(kb.dir) / "errata.txt").read_text(), "old")
            self.assertEqual((Path(kb.dir) / "errata-2.txt").read_text(), "new")
            self.assertEqual(sorted(p.name for p in Path(kb.dir).iterdir()),
                             ["errata-2.txt", "errata.txt"])

    def test_failed_copy_does_not_publish_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "errata.txt"
            src.write_text("complete", encoding="utf-8")
            kb = KB(tmp)

            def failed_copy(source: str, dest: str) -> None:
                Path(dest).write_bytes(b"partial")
                raise OSError("copy interrupted")

            with patch("ocdcircuit.kb.shutil.copy2", side_effect=failed_copy):
                with self.assertRaises(OSError):
                    kb.add(str(src))
            self.assertEqual(list(Path(kb.dir).iterdir()), [])
            self.assertEqual(kb.add(str(src))["added"], "errata.txt")
            self.assertEqual((Path(kb.dir) / "errata.txt").read_text(), "complete")

    def test_add_source_file_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj.ocd"
            proj.write_text("board proj 20x10 2L\n", encoding="utf-8")
            kb = KB(tmp, board=None, parts={})

            src = Path(tmp) / "errata.txt"
            src.write_text("rev B: R7 -> 0R\n", encoding="utf-8")

            first = kb.add(str(src))
            r1 = first["added"] if isinstance(first, dict) else None
            self.assertEqual(r1, "errata.txt")

            dup = kb.add(str(src))
            r2 = dup["added"] if isinstance(dup, dict) else None
            self.assertEqual(r2, "errata.txt")

            names = sorted(p.name for p in (Path(tmp) / "kb").iterdir())
            self.assertEqual(names, ["errata.txt"])

    def test_add_source_file_concurrent_retries_single_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj.ocd"
            proj.write_text("board proj 20x10 2L\n", encoding="utf-8")
            kb = KB(tmp, board=None, parts={})

            src = Path(tmp) / "errata.txt"
            src.write_text("rev B: R7 -> 0R\n", encoding="utf-8")

            barrier = Barrier(4)

            def add_copy(_: int) -> dict[str, object]:
                barrier.wait(timeout=10)
                return KB(tmp).add(str(src))

            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(add_copy, range(8)))
            names = sorted(p.name for p in (Path(tmp) / "kb").iterdir())
            self.assertEqual(names, ["errata.txt"])
            self.assertEqual(len({r["added"] for r in results}), 1)


class KBAddURLRetryTest(unittest.TestCase):
    def test_url_add_retry_with_stale_source_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj.ocd"
            proj.write_text("board proj 20x10 2L\n", encoding="utf-8")
            kb = KB(tmp, board=None, parts={})

            def fake_dl(url: str, dest: str, timeout: float = 60.0) -> int:
                Path(dest).parent.mkdir(parents=True, exist_ok=True)
                Path(dest).write_bytes(b"%PDF-1.4\n")
                return 8

            with patch("ocdcircuit.kb._download", fake_dl):
                r1 = kb.add("https://example.test/ds.pdf")
                self.assertEqual(r1["added"], "datasheets/ds.pdf")
                (Path(tmp) / "kb" / "datasheets" / "ds.pdf").unlink()
                r2 = kb.add("https://example.test/ds.pdf")
                self.assertEqual(r2["added"], "datasheets/ds.pdf")
            self.assertEqual(
                (Path(tmp) / "kb" / "sources.tsv").read_text(
                    encoding="utf-8").count("\n"), 1)
            self.assertEqual(
                sorted(p.name for p in
                       (Path(tmp) / "kb" / "datasheets").iterdir()),
                ["ds.pdf"])


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
