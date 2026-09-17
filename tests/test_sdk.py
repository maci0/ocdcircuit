from __future__ import annotations

import os
import sys
import tarfile
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from threading import Barrier

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ocdcircuit import Board
from ocdcircuit.kb import KB


class BoardSerializationTest(unittest.TestCase):
    def test_empty_nets_survive_text_round_trip(self) -> None:
        from ocdcircuit import agent

        board = Board("empty_nets")
        self.addCleanup(board.unload)
        board.net("UNUSED")
        board.net("SIGNAL")
        board.set_net_attrs("SIGNAL", {"class": "signal"})
        source = agent.dumps(board)
        restored = agent.loads(source)
        self.addCleanup(restored.unload)
        self.assertEqual(set(restored.nets), {"UNUSED", "SIGNAL"})
        self.assertEqual(agent.dumps(restored), source)
        restored.unload()
        self.assertEqual(restored.nets, {})

    def test_empty_block_ports_survive_instantiation(self) -> None:
        from ocdcircuit import agent

        for attrs in ("", " class=signal"):
            with self.subTest(attrs=attrs):
                source = ("board empty_ports 40x30 2L\n"
                          "block channel ports OUT\n"
                          f"  OUT{attrs} ::\n"
                          "end\n"
                          "instance channel as A\n")
                board = agent.loads(source)
                self.addCleanup(board.unload)
                self.assertEqual(set(board.nets), {"A_OUT"})
                self.assertEqual(board.nets["A_OUT"].pins, [])
                self.assertEqual(board.nets["A_OUT"].attrs,
                                 {"class": "signal"} if attrs else {})
                restored = agent.loads(agent.dumps(board))
                self.addCleanup(restored.unload)
                self.assertEqual(agent.dumps(restored), agent.dumps(board))

    def test_empty_nets_survive_json_round_trip(self) -> None:
        from ocdcircuit import agent

        board = Board("empty_nets")
        self.addCleanup(board.unload)
        board.add_part("R1", "R0805")
        board.connect("N", "R1", 1)
        board.set_net_attrs("N", {"class": "signal"})
        board.net("UNUSED")
        snap = board.ctx.snapshot()
        board.disconnect("N", "R1", 1)

        restored = agent.from_json(agent.to_json(board))
        self.addCleanup(restored.unload)
        self.assertEqual(agent.ir(restored), agent.ir(board))
        self.assertEqual(set(restored.nets), {"N", "UNUSED"})
        self.assertEqual(restored.nets["N"].pins, [])
        self.assertEqual(restored.nets["N"].attrs, {"class": "signal"})
        self.assertEqual(restored.nets["UNUSED"].pins, [])
        board.ctx.rollback(snap)
        self.assertEqual(board.nets["N"].pins, [("R1", "1")])
        self.assertEqual(restored.nets["N"].pins, [])
        restored.unload()
        self.assertEqual(restored.nets, {})


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


class KBAddTextTest(unittest.TestCase):
    def test_mcp_retries_preserve_content_and_metadata(self) -> None:
        from apps import mcp

        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(mcp, "PROJ", tmp), patch.object(mcp, "BOARD", None):
            request: dict[str, object] = {"op": "add", "text": "Résumé\n"}
            first = mcp.t_kb(request)
            path = Path(tmp) / "kb" / "note.md"
            before = path.stat()
            self.assertEqual(mcp.t_kb(request), first)
            self.assertEqual(path.read_bytes(), "Résumé\n".encode("utf-8"))
            self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(path.stat().st_ino, before.st_ino)
            self.assertEqual(sorted(p.name for p in path.parent.iterdir()),
                             ["note.md"])

    def test_changed_text_preserves_versions_and_reuses_collision(self) -> None:
        for name, relative in (("notes.md", "notes.md"),
                               ("data.pdf", "datasheets/data.pdf")):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                kb = KB(tmp)
                first = kb.add(name=name, text="old")
                second = kb.add(name=name, text="new")
                self.assertEqual(first["added"], relative)
                self.assertNotEqual(first["added"], second["added"])
                self.assertEqual(KB(tmp).add(name=name, text="new"), second)
                self.assertEqual(KB(tmp).add(name=name, text="old"), first)
                self.assertEqual(kb.count(), 2)
                self.assertEqual((Path(kb.dir) / relative).read_bytes(), b"old")
                self.assertEqual((Path(kb.dir) / str(second["added"])).read_bytes(),
                                 b"new")

    def test_concurrent_text_retries_publish_one_complete_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            barrier = Barrier(4)
            link = os.link
            body = "complete note\n" * 1000

            def publish(src: str, dest: str) -> None:
                barrier.wait(timeout=10)
                link(src, dest)

            def add(_: int) -> dict[str, object]:
                return KB(tmp).add(text=body)

            with patch("ocdcircuit.kb.os.link", side_effect=publish), \
                    ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(add, range(4)))
            self.assertTrue(all(result == results[0] for result in results))
            directory = Path(tmp) / "kb"
            self.assertEqual(sorted(p.name for p in directory.iterdir()), ["note.md"])
            self.assertEqual((directory / "note.md").read_text(), body)

    def test_failed_text_write_can_retry_without_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            kb = KB(tmp)
            with patch("ocdcircuit.kb.os.fsync", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    kb.add(text="complete")
            self.assertEqual(list(Path(kb.dir).iterdir()), [])
            self.assertEqual(kb.add(text="complete")["added"], "note.md")
            self.assertEqual((Path(kb.dir) / "note.md").read_text(), "complete")

    def test_empty_text_retries_reuse_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            kb = KB(tmp)
            self.assertEqual(kb.add(text=""), {"added": "note.md", "bytes": 0})
            self.assertEqual(KB(tmp).add(text=""), {"added": "note.md", "bytes": 0})
            self.assertEqual(kb.count(), 1)


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


class ArchiveReproducibilityTest(unittest.TestCase):
    def test_odb_metadata_and_bytes(self) -> None:
        board = Board("reproducible")
        board.add_part("R1", "R0805", x=5, y=5)
        board.connect("N", "R1", 1)
        self.addCleanup(board.unload)
        for epoch in (0, 1234567890, 5000000000):
            with self.subTest(epoch=epoch), tempfile.TemporaryDirectory() as root:
                artifacts = []
                for index, clock in enumerate((1700000000, 1800000000)):
                    with patch.dict(os.environ, SOURCE_DATE_EPOCH=str(epoch)), \
                            patch("time.time", return_value=clock):
                        path = board.export("odb", outdir=str(Path(root) / str(index)))[0]
                    artifacts.append(Path(path).read_bytes())
                    with tarfile.open(path, "r:gz") as archive:
                        members = archive.getmembers()
                        self.assertEqual(archive.getnames(), sorted(archive.getnames()))
                        self.assertTrue(members)
                        for member in members:
                            self.assertEqual(member.mtime, epoch)
                            self.assertEqual((member.uid, member.gid, member.mode),
                                             (0, 0, 0o644))
                        netlist = archive.extractfile("odb/steps/pcb/netlists/cadnet/netlist")
                        self.assertIsNotNone(netlist)
                        assert netlist is not None
                        self.assertIn(b"$NET N\n  R1.1\n", netlist.read())
                self.assertEqual(artifacts[0], artifacts[1])
                self.assertEqual(int.from_bytes(artifacts[0][4:8], "little"),
                                 min(epoch, 0xFFFFFFFF))


if __name__ == "__main__":
    unittest.main()
